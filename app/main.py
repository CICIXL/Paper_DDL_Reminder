"""
main.py - the web app: every address the site answers.

Each function below with an @app.route(...) line above it is one "route":
the address in the brackets is what a visitor's browser asks for, and the
function builds the reply.

PUBLIC PAGES
    /                      all conferences, with countdowns
    /c/<id>                one conference
    /subscribe             the sign-up form
    /confirm/<token>       the link in the confirmation email
    /manage/<token>        a subscriber's private page for changing choices
    /unsubscribe/<token>   remove a subscriber and their data
    /privacy, /about
FEEDS FOR MACHINES
    /deadlines.ics, /sitemap.xml, /robots.txt, /healthz
PROTECTED (need the TASK_TOKEN)
    /tasks/daily           run the daily task now
    /tasks/stats           subscriber counts

Run it on your own computer from the repository folder:
    uv run flask --app app.main run --debug
"""

import hmac
import re
import threading
import time
from collections import defaultdict, deque
from datetime import datetime, timezone

from flask import (Flask, Response, abort, jsonify, redirect, render_template,
                   request, url_for)
from markupsafe import Markup

from . import cards, confdata, config, db, emailer, reminders

EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]{2,}$")
SIGNUP_WAIT_SECONDS = 600     # at most one sign-up email per address per 10 minutes
SIGNUP_MAIL_LIMIT = 5         # and at most 5 in total until they confirm


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------

def load_conferences():
    """Read the conference data and sort it: soonest upcoming date first."""
    settings, _, conferences = confdata.load()
    now_ms = cards.ms(datetime.now(timezone.utc))
    conferences.sort(key=lambda c: cards.sort_key(c, now_ms))
    return settings, conferences, now_ms


def area_list(conferences):
    areas = []
    for conf in conferences:
        for area in conf["areas"]:
            if area not in areas:
                areas.append(area)
    return areas


def choice_groups(conferences):
    """
    The tick-boxes on the sign-up form: conference series grouped by their
    main area, e.g. {"ML": [("icml", "ICML", "International Conference ..."), ...]}.
    """
    groups, seen = {}, set()
    for conf in sorted(conferences, key=lambda c: confdata.series_name(c).lower()):
        key = confdata.series(conf)
        if key in seen:
            continue
        seen.add(key)
        area = conf["areas"][0] if conf["areas"] else "Other"
        groups.setdefault(area, []).append(
            {"key": key, "name": confdata.series_name(conf), "full": conf.get("full_name", "")})
    order = area_list(conferences) + ["Other"]
    return [(area, groups[area]) for area in order if area in groups]


def read_choices(form, conferences):
    """Take the ticked boxes from a submitted form, keeping only real options."""
    known_areas = set(area_list(conferences))
    known_series = {confdata.series(c) for c in conferences}
    areas = {a for a in form.getlist("area") if a in known_areas}
    series = {s for s in form.getlist("series") if s in known_series}
    return areas, series


def client_ip():
    forwarded = request.headers.get("X-Forwarded-For", "")
    return forwarded.split(",")[0].strip() or request.remote_addr or "unknown"


class RateLimiter:
    """Allow each visitor a limited number of tries in a time window."""

    def __init__(self, limit, seconds):
        self.limit, self.seconds = limit, seconds
        self.hits = defaultdict(deque)
        self.lock = threading.Lock()

    def allow(self, who):
        now = time.monotonic()
        with self.lock:
            queue = self.hits[who]
            while queue and now - queue[0] > self.seconds:
                queue.popleft()
            if len(queue) >= self.limit:
                return False
            queue.append(now)
            if len(self.hits) > 5000:      # do not let the table grow forever
                self.hits.clear()
            return True


def seconds_since(iso_text):
    if not iso_text:
        return float("inf")
    return (datetime.now(timezone.utc) - datetime.fromisoformat(iso_text)).total_seconds()


def message_page(title, body, status=200, link=None, link_text=None):
    return render_template("message.html", title=title, body=body,
                           link=link, link_text=link_text, private=True), status


# ---------------------------------------------------------------------------
# The app
# ---------------------------------------------------------------------------

def create_app():
    app = Flask(__name__)
    app.config["MAX_CONTENT_LENGTH"] = 64 * 1024      # nobody needs to post more
    db.init()
    signup_limiter = RateLimiter(limit=8, seconds=600)

    @app.context_processor
    def shared_values():
        # Values every page template can use.
        return {"site_name": config.SITE_NAME, "base_url": config.base_url(),
                "contact_email": config.contact_email()}

    @app.after_request
    def safety_headers(response):
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        # Private links contain a secret token; never pass it on to other sites.
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        if request.path.startswith(("/manage/", "/confirm/", "/unsubscribe/", "/tasks/")):
            response.headers["Cache-Control"] = "no-store"
            response.headers["X-Robots-Tag"] = "noindex"
        return response

    # ----------------------------------------------------------- public pages

    @app.route("/")
    def home():
        settings, conferences, now_ms = load_conferences()
        tracker = confdata.read_tracker_state()
        return render_template(
            "index.html",
            tagline=settings.get("site_tagline", ""),
            count=len(conferences),
            areas=[(a, cards.slug(a)) for a in area_list(conferences)],
            next_up=Markup(cards.next_up(conferences, now_ms)),
            cards=Markup("".join(cards.card(c) for c in conferences)),
            verified=settings.get("dates_verified", "not recorded"),
            synced=tracker.get("last_sync", "not yet run"),
        )

    @app.route("/c/<conf_id>")
    def conference(conf_id):
        _, conferences, _ = load_conferences()
        conf = next((c for c in conferences if c["id"] == conf_id), None)
        if conf is None:
            abort(404)
        dates = [f"{ev['label'].lower()} {confdata.nice_date(ev['date'])}" for ev in confdata.events(conf)]
        return render_template(
            "conference.html", conf=conf, card=Markup(cards.card(conf)),
            series=confdata.series(conf), series_name=confdata.series_name(conf),
            summary="; ".join(dates),
        )

    @app.route("/subscribe", methods=["GET", "POST"])
    def subscribe():
        _, conferences, _ = load_conferences()
        groups, areas = choice_groups(conferences), area_list(conferences)

        def form(error=None, email="", picked_areas=(), picked_series=(), status=200):
            return render_template(
                "subscribe.html", groups=groups, areas=areas, error=error, email=email,
                picked_areas=set(picked_areas), picked_series=set(picked_series)), status

        if request.method == "GET":
            return form(picked_areas=request.args.getlist("a"), picked_series=request.args.getlist("c"))

        done = message_page(
            "Check your inbox",
            "If that address can receive mail, a confirmation link is on its way. "
            "Click it to start getting reminders. It can take a minute, and it may land in spam.")

        if request.form.get("website"):        # a hidden field only robots fill in
            return done
        email = request.form.get("email", "").strip().lower()
        picked_areas, picked_series = read_choices(request.form, conferences)
        if len(email) > 254 or not EMAIL_PATTERN.match(email):
            return form("That email address does not look right.", email, picked_areas, picked_series, 400)
        if not picked_areas and not picked_series:
            return form("Tick at least one area or conference.", email, picked_areas, picked_series, 400)
        if not signup_limiter.allow(client_ip()):
            return form("Too many tries from your network. Please wait ten minutes.",
                        email, picked_areas, picked_series, 429)

        with db.session() as conn:
            user = db.user_by_email(conn, email)
            if user is None:
                user = db.create_user(conn, email)
                db.set_follows(conn, user["id"], picked_areas, picked_series)
            elif not user["confirmed"]:
                db.set_follows(conn, user["id"], picked_areas, picked_series)
            # A confirmed subscriber's choices are never changed from this public
            # form: anyone could type their address. They get their manage link.

            too_soon = seconds_since(user["last_mail_at"]) < SIGNUP_WAIT_SECONDS
            too_many = user["signup_mails"] >= SIGNUP_MAIL_LIMIT and not user["confirmed"]
            if too_soon or too_many:
                return done
            if user["confirmed"]:
                subject, html_body, text_body = emailer.manage_link(user["token"])
            else:
                subject, html_body, text_body = emailer.confirmation(user["token"])
            try:
                sent = emailer.send(conn, email, subject, html_body, text_body)
            except emailer.QuotaReached:
                sent = False
            if sent:
                db.note_signup_mail(conn, user["id"])
        if not sent:
            return message_page(
                "We could not send the email",
                "Your choices are saved, but our email allowance for today is used up or the "
                "mail service is having trouble. Please submit the form again tomorrow.", 503)
        return done

    @app.route("/confirm/<token>")
    def confirm(token):
        with db.session() as conn:
            user = db.user_by_token(conn, token)
            if user is None:
                return message_page("This link is not valid",
                                    "It may have expired. Sign up again to get a new one.", 404,
                                    url_for("subscribe"), "Sign up")
            db.confirm_user(conn, user["id"])
        return redirect(url_for("manage", token=token, welcome=1))

    @app.route("/manage/<token>", methods=["GET", "POST"])
    def manage(token):
        _, conferences, _ = load_conferences()
        saved = False
        with db.session() as conn:
            user = db.user_by_token(conn, token)
            if user is None:
                return message_page("This link is not valid",
                                    "If you unsubscribed, sign up again to get a new one.", 404,
                                    url_for("subscribe"), "Sign up")
            if not user["confirmed"]:
                return redirect(url_for("confirm", token=token))
            if request.method == "POST":
                picked_areas, picked_series = read_choices(request.form, conferences)
                db.set_follows(conn, user["id"], picked_areas, picked_series)
                saved = True
            picked_areas, picked_series = db.get_follows(conn, user["id"])
        return render_template(
            "manage.html", groups=choice_groups(conferences), areas=area_list(conferences),
            picked_areas=picked_areas, picked_series=picked_series, email=user["email"],
            token=token, saved=saved, welcome=bool(request.args.get("welcome")), private=True)

    @app.route("/unsubscribe/<token>", methods=["GET", "POST"])
    def unsubscribe(token):
        with db.session() as conn:
            user = db.user_by_token(conn, token)
            if user is None:
                return message_page("You are not subscribed",
                                    "There is nothing stored for this link.")
            if request.method == "GET":
                # Ask first: some mail programs open every link in an email.
                return render_template("unsubscribe.html", email=user["email"], token=token, private=True)
            db.delete_user(conn, user["id"])
        return message_page("You are unsubscribed",
                            "Your email address and choices have been deleted. "
                            "You will not get any more emails.",
                            link=url_for("home"), link_text="Back to the deadlines")

    @app.route("/privacy")
    def privacy():
        return render_template("privacy.html")

    @app.route("/about")
    def about():
        return render_template("about.html")

    # ------------------------------------------------------ feeds for machines

    @app.route("/deadlines.ics")
    def calendar_feed():
        settings, conferences, _ = load_conferences()
        settings = dict(settings, site_title=config.SITE_NAME)
        body = cards.build_ics(settings, conferences, confdata.today_aoe())
        return Response(body, mimetype="text/calendar",
                        headers={"Content-Disposition": 'inline; filename="paperddl.ics"'})

    @app.route("/sitemap.xml")
    def sitemap():
        _, conferences, _ = load_conferences()
        base = config.base_url()
        pages = ["/", "/subscribe", "/about", "/privacy"] + [f"/c/{c['id']}" for c in conferences]
        body = '<?xml version="1.0" encoding="UTF-8"?>\n' \
               '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n' + \
               "".join(f"  <url><loc>{base}{p}</loc></url>\n" for p in pages) + "</urlset>\n"
        return Response(body, mimetype="application/xml")

    @app.route("/robots.txt")
    def robots():
        body = ("User-agent: *\nDisallow: /manage/\nDisallow: /confirm/\nDisallow: /unsubscribe/\n"
                f"Disallow: /tasks/\n\nSitemap: {config.base_url()}/sitemap.xml\n")
        return Response(body, mimetype="text/plain")

    @app.route("/healthz")
    def health():
        return "ok"

    # ---------------------------------------------------------- protected pages

    def require_task_token():
        expected = config.task_token()
        if not expected:
            abort(503, description="TASK_TOKEN is not set on the server.")
        given = request.headers.get("Authorization", "").removeprefix("Bearer ").strip()
        if not hmac.compare_digest(given.encode(), expected.encode()):
            abort(401)

    @app.route("/tasks/daily", methods=["POST"])
    def task_daily():
        require_task_token()
        return jsonify(reminders.run_daily())

    @app.route("/tasks/stats")
    def task_stats():
        require_task_token()
        with db.session() as conn:
            return jsonify(db.stats(conn))

    # ------------------------------------------------------------------ errors

    @app.errorhandler(404)
    def not_found(_error):
        return message_page("Page not found", "That address does not exist here.", 404,
                            url_for("home"), "See all deadlines")

    @app.errorhandler(500)
    def server_error(_error):
        return message_page("Something went wrong",
                            "The problem is on our side. Please try again in a moment.", 500)

    if config.scheduler_on():
        start_scheduler()
    return app


# ---------------------------------------------------------------------------
# The built-in alarm clock
# ---------------------------------------------------------------------------

def start_scheduler():
    """
    Run the daily task once a day without any outside trigger.

    A background thread wakes every few minutes and asks: is it past the run
    time, and has today's run not happened yet? The answer is stored in the
    database, so a restart in the middle of the day does not cause a second run.
    """
    def loop():
        while True:
            time.sleep(240)
            try:
                now = datetime.now(timezone.utc)
                if (now.hour, now.minute) < config.DAILY_RUN_UTC:
                    continue
                today = now.date().isoformat()
                with db.session() as conn:
                    if db.meta_get(conn, "scheduler_day") == today:
                        continue
                    db.meta_set(conn, "scheduler_day", today)
                reminders.run_daily()
            except Exception as e:      # never let the clock die
                print(f"[scheduler] error: {e}")

    threading.Thread(target=loop, name="daily-scheduler", daemon=True).start()
    print("[scheduler] started")


app = create_app()

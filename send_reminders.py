"""
send_reminders.py - works out which reminders are due today and emails them.

It is meant to run once a day (GitHub does this automatically, see README).
Each subscriber gets at most ONE email per day, listing everything that is due.
If nothing is due, nobody gets an email.

Useful commands (run from this folder):

    python3 send_reminders.py --dry-run
        Show what would be sent today. Sends nothing.

    python3 send_reminders.py --dry-run --today 2027-01-15
        Pretend today is another date. Handy for checking the rules.

    python3 send_reminders.py --test you@example.com
        Send one sample email to yourself to check the email settings work.

DATE CHANGES
    When sync_dates.py notices that a date was announced or moved, it queues a
    notice. This script adds a "Date changes" section to the next email, so
    people hear about a moved deadline the day it is picked up.

HOW "DUE" IS DECIDED
    conferences.toml has a [reminders] section, e.g.  submission = [30, 7, 1]
    That means: nudge 30, 7 and 1 days before a paper deadline.
    The script remembers what it already sent (state/sent_log.json), so:
      - nobody is reminded twice for the same nudge, and
      - if a day's run is missed, the nudge goes out on the next run instead
        of being lost (the email always states the true number of days left).
"""

import argparse
import html
import json
import os
import smtplib
import ssl
import sys
from datetime import date, timedelta
from email.message import EmailMessage
from email.utils import formataddr

import confdata
from confdata import ROOT, TRACKER_STATE, days_phrase, event_label, events, nice_date

SENT_LOG = ROOT / "state" / "sent_log.json"
LOCAL_SUBSCRIBERS = ROOT / "subscribers.txt"


# ---------------------------------------------------------------------------
# Step 1: who gets the emails?
# ---------------------------------------------------------------------------

def read_subscribers():
    """
    Subscribers come from the SUBSCRIBERS secret on GitHub, or from a local
    file called subscribers.txt when you test on your own computer.

    One person per line. Optionally limit someone to certain areas:
        alice@example.com
        bob@example.com | NLP, Systems
    """
    text = os.environ.get("SUBSCRIBERS", "")
    if not text.strip() and LOCAL_SUBSCRIBERS.exists():
        text = LOCAL_SUBSCRIBERS.read_text(encoding="utf-8")

    people, seen = [], set()
    for line in text.splitlines():
        line = line.split("#")[0].strip()
        if not line:
            continue
        address, _, area_text = line.partition("|")
        address = address.strip()
        if "@" not in address or address.lower() in seen:
            continue
        seen.add(address.lower())
        areas = {a.strip().lower() for a in area_text.split(",") if a.strip()}
        people.append({"email": address, "areas": areas})
    return people


# ---------------------------------------------------------------------------
# Step 2: what is due today?
# ---------------------------------------------------------------------------

def load_sent_log():
    if SENT_LOG.exists():
        try:
            return set(json.loads(SENT_LOG.read_text(encoding="utf-8")).get("sent", []))
        except (json.JSONDecodeError, AttributeError):
            pass
    return set()


def save_sent_log(sent, today):
    """Write the log back, dropping entries for dates more than 45 days old."""
    cutoff = today - timedelta(days=45)
    kept = []
    for key in sent:
        try:
            when = date.fromisoformat(key.split("|")[2])
        except (IndexError, ValueError):
            continue
        if when >= cutoff:
            kept.append(key)
    SENT_LOG.parent.mkdir(exist_ok=True)
    SENT_LOG.write_text(json.dumps({"sent": sorted(kept)}, indent=1) + "\n", encoding="utf-8")


def find_due(conferences, reminders, today, sent):
    """Return the list of milestones that need a reminder today."""
    due = []
    for conf in conferences:
        for ev in events(conf):
            nudges = reminders.get(ev["kind"], [])
            days_left = (ev["date"] - today).days
            if days_left < 0:
                continue  # already passed
            # Which nudge are we inside? e.g. with [30, 7, 1] and 5 days
            # left, we are inside the "7" nudge.
            inside = [n for n in nudges if days_left <= n]
            if not inside:
                continue  # still too far away
            nudge = min(inside)
            key = f"{conf['id']}|{ev['kind']}|{ev['date']}|{nudge}"
            if key in sent:
                continue  # this nudge already went out
            due.append({**ev, "days_left": days_left, "key": key})
    due.sort(key=lambda e: (e["days_left"], e["conf"]["name"]))
    return due


def pending_changes(conferences, today):
    """Date changes queued by sync_dates.py that have not been emailed yet."""
    by_id = {c["id"]: c for c in conferences}
    changes = []
    for n in confdata.read_tracker_state().get("notices", []):
        conf = by_id.get(n.get("id"))
        try:
            new = date.fromisoformat(n["new"])
            old = date.fromisoformat(n["old"]) if n.get("old") else None
        except (KeyError, ValueError):
            continue
        # Skip it if the date has changed again since the notice was queued.
        if conf is None or conf.get(n.get("field")) != new:
            continue
        changes.append({"conf": conf, "kind": n["field"],
                        "label": event_label(conf, n["field"]), "old": old, "new": new})
    changes.sort(key=lambda c: c["new"])
    return changes


def clear_changes():
    state = confdata.read_tracker_state()
    if state.get("notices"):
        state["notices"] = []
        TRACKER_STATE.write_text(json.dumps(state, indent=1, sort_keys=True) + "\n", encoding="utf-8")


def items_for(person, due):
    """Apply a subscriber's optional area filter."""
    if not person["areas"]:
        return due
    return [e for e in due
            if person["areas"] & {a.lower() for a in e["conf"]["areas"]}]


# ---------------------------------------------------------------------------
# Step 3: write the email
# ---------------------------------------------------------------------------

def subject_line(items, changes=()):
    if not items:
        first = changes[0]
        text = f"Date change: {first['conf']['name']} {first['label'][0].lower() + first['label'][1:]} is now {nice_date(first['new'])}"
        if len(changes) > 1:
            text += f" (+{len(changes) - 1} more)"
        return text
    first = items[0]
    text = f"{first['conf']['name']}: {first['label']} {days_phrase(first['days_left'])}"
    if len(items) > 1:
        text += f" (+{len(items) - 1} more)"
    if changes:
        text += " + date changes"
    return text


def change_phrase(change):
    if change["old"]:
        return f"moved from {nice_date(change['old'])} to {nice_date(change['new'])}"
    return f"announced: {nice_date(change['new'])}"


def time_note(ev):
    if not ev["is_deadline"]:
        return ""
    return ev["conf"].get("time_note") or "end of day, Anywhere on Earth"


def plain_body(items, settings, changes=()):
    lines = []
    if changes:
        lines += ["Date changes:", ""]
        for ch in changes:
            lines.append(f"* {ch['conf']['name']} - {ch['label']} {change_phrase(ch)}")
            if ch["conf"].get("url"):
                lines.append(f"  {ch['conf']['url']}")
        lines += ["  (Picked up automatically from a community tracker - confirm on the official site.)", ""]
    if items:
        lines += ["Coming up:", ""]
    for ev in items:
        conf = ev["conf"]
        lines.append(f"* {conf['name']} - {ev['label']}")
        when = f"  {nice_date(ev['date'])} ({days_phrase(ev['days_left'])})"
        if time_note(ev):
            when += f" - {time_note(ev)}"
        lines.append(when)
        if ev["auto"]:
            lines.append("  Auto-updated from a tracker - check the official site.")
        elif ev["unverified"]:
            lines.append("  Unconfirmed date - check the official site.")
        if conf.get("url"):
            lines.append(f"  {conf['url']}")
        lines.append("")
    if settings.get("site_url"):
        lines.append(f"All dates: {settings['site_url']}")
    lines.append("Dates can move. Always confirm on the official conference site.")
    return "\n".join(lines)


def html_body(items, settings, changes=()):
    esc = html.escape
    change_block = ""
    if changes:
        lis = []
        for ch in changes:
            name = esc(ch["conf"]["name"])
            if ch["conf"].get("url"):
                name = f'<a href="{esc(ch["conf"]["url"])}" style="color:#1d1b16">{name}</a>'
            lis.append(f'<div style="margin:8px 0 0;font-size:15px"><strong>{name}</strong> '
                       f'&middot; {esc(ch["label"])} {esc(change_phrase(ch))}</div>')
        change_block = ('<div style="background:#fff4d6;border:1px solid #e6c15a;border-radius:10px;'
                        'padding:14px 16px;margin:0 0 18px;color:#1d1b16">'
                        '<div style="font-size:13px;letter-spacing:.08em;text-transform:uppercase;'
                        'font-weight:700;color:#7a4b00">Date changes</div>' + "".join(lis) +
                        '<div style="font-size:12px;color:#6b665c;margin-top:10px">Picked up automatically '
                        'from a community tracker. Confirm on the official site.</div></div>')
    rows = []
    for ev in items:
        conf = ev["conf"]
        urgent = ev["days_left"] <= 3
        badge_bg = "#b3261e" if urgent else "#2f5fd0"
        name = esc(conf["name"])
        if conf.get("url"):
            name = f'<a href="{esc(conf["url"])}" style="color:#1d1b16;text-decoration:none">{name}</a>'
        extra = ""
        if time_note(ev):
            extra += f'<div style="color:#6b665c;font-size:13px">{esc(time_note(ev))}</div>'
        if ev["auto"]:
            extra += ('<div style="color:#9a5b00;font-size:13px">Auto-updated from a tracker '
                      '&ndash; check the official site.</div>')
        elif ev["unverified"]:
            extra += ('<div style="color:#9a5b00;font-size:13px">Unconfirmed date '
                      '&ndash; check the official site.</div>')
        rows.append(f"""
        <tr>
          <td style="padding:14px 0;border-bottom:1px solid #e4dfd3;vertical-align:top">
            <div style="font-size:17px;font-weight:600">{name}</div>
            <div style="font-size:15px;margin-top:2px">{esc(ev['label'])}</div>
            <div style="color:#6b665c;font-size:14px;margin-top:2px">{esc(nice_date(ev['date']))}</div>
            {extra}
          </td>
          <td style="padding:14px 0 14px 12px;border-bottom:1px solid #e4dfd3;vertical-align:top;text-align:right;white-space:nowrap">
            <span style="display:inline-block;background:{badge_bg};color:#ffffff;border-radius:999px;padding:4px 12px;font-size:13px;font-weight:600">{esc(days_phrase(ev['days_left']))}</span>
          </td>
        </tr>""")

    link = ""
    if settings.get("site_url"):
        link = (f'<p style="margin:20px 0 0"><a href="{esc(settings["site_url"])}" '
                f'style="color:#2f5fd0">See all conference dates</a></p>')
    title = esc(settings.get("site_title", "Conference deadlines"))
    return f"""<!DOCTYPE html>
<html><body style="margin:0;padding:0;background:#f6f4ef">
<div style="max-width:560px;margin:0 auto;padding:24px 20px;font-family:-apple-system,'Segoe UI',Helvetica,Arial,sans-serif;color:#1d1b16">
  <div style="font-size:13px;letter-spacing:.08em;text-transform:uppercase;color:#6b665c">{title}</div>
  <div style="height:12px"></div>
  {change_block}
  {'<h1 style="font-size:22px;margin:0 0 8px">Coming up</h1>' if rows else ''}
  <table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="border-collapse:collapse">{''.join(rows)}
  </table>
  {link}
  <p style="color:#6b665c;font-size:12px;margin-top:20px">Dates can move. Always confirm on the official conference site.</p>
</div>
</body></html>"""


def build_message(person, items, settings, sender, changes=()):
    msg = EmailMessage()
    msg["Subject"] = subject_line(items, changes)
    msg["From"] = formataddr((settings.get("email_from_name", "Conference Reminders"), sender))
    msg["To"] = person["email"]
    msg.set_content(plain_body(items, settings, changes))
    msg.add_alternative(html_body(items, settings, changes), subtype="html")
    return msg


# ---------------------------------------------------------------------------
# Step 4: send it
# ---------------------------------------------------------------------------

def smtp_settings():
    """Email login details. They come from secrets, never from files in the repo."""
    user = os.environ.get("SMTP_USER", "").strip()
    password = os.environ.get("SMTP_PASSWORD", "").replace(" ", "")
    if not user or not password:
        return None
    return {
        "host": os.environ.get("SMTP_HOST", "").strip() or "smtp.gmail.com",
        "port": int(os.environ.get("SMTP_PORT", "").strip() or 465),
        "user": user,
        "password": password,
        "sender": os.environ.get("MAIL_FROM", "").strip() or user,
    }


def open_connection(cfg):
    context = ssl.create_default_context()
    if cfg["port"] == 465:
        server = smtplib.SMTP_SSL(cfg["host"], cfg["port"], context=context, timeout=30)
    else:
        server = smtplib.SMTP(cfg["host"], cfg["port"], timeout=30)
        server.starttls(context=context)
    server.login(cfg["user"], cfg["password"])
    return server


def send_all(messages, cfg):
    """Send a list of messages. Returns the list of addresses that failed."""
    failed = []
    server = open_connection(cfg)
    try:
        for msg in messages:
            try:
                server.send_message(msg)
                print(f"  sent to {mask(msg['To'])}")
            except smtplib.SMTPException as e:
                failed.append(msg["To"])
                print(f"  FAILED for {mask(msg['To'])}: {e}")
    finally:
        try:
            server.quit()
        except smtplib.SMTPException:
            pass
    return failed


def mask(address):
    """Hide most of an address so logs on a public repo do not expose it."""
    name, _, domain = address.partition("@")
    return f"{name[:2]}***@{domain}"


# ---------------------------------------------------------------------------
# Main program
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Email conference deadline reminders.")
    parser.add_argument("--dry-run", action="store_true",
                        help="show what would be sent, but send nothing")
    parser.add_argument("--today", metavar="YYYY-MM-DD",
                        help="pretend today is this date (for testing)")
    parser.add_argument("--test", metavar="EMAIL",
                        help="send one sample email to this address")
    parser.add_argument("--preview", metavar="FILE",
                        help="also save the email as an HTML file you can open")
    args = parser.parse_args()

    try:
        settings, reminders, conferences = confdata.load()
    except confdata.DataError as e:
        confdata.fail(str(e))

    today = date.fromisoformat(args.today) if args.today else confdata.today_aoe()
    print(f"Today (Anywhere on Earth): {today}")

    # --- sample email to yourself ------------------------------------------
    if args.test:
        upcoming = [dict(ev, days_left=(ev["date"] - today).days, key="")
                    for c in conferences for ev in events(c) if ev["date"] >= today]
        upcoming.sort(key=lambda e: e["days_left"])
        sample = upcoming[:3]
        if not sample:
            confdata.fail("There are no upcoming dates to put in a test email.")
        cfg = smtp_settings()
        if not cfg:
            confdata.fail("SMTP_USER and SMTP_PASSWORD are not set, so no email can be sent.")
        msg = build_message({"email": args.test, "areas": set()}, sample, settings, cfg["sender"])
        failed = send_all([msg], cfg)
        sys.exit(1 if failed else 0)

    # --- the normal daily run ----------------------------------------------
    sent = load_sent_log()
    due = find_due(conferences, reminders, today, sent)
    changes = pending_changes(conferences, today)
    if not due and not changes:
        print("Nothing is due today and no dates changed. No emails sent.")
        return

    if changes:
        print(f"{len(changes)} date change(s) to announce:")
        for ch in changes:
            print(f"  - {ch['conf']['name']}: {ch['label']} {change_phrase(ch)}")
    print(f"{len(due)} reminder(s) due:")
    for ev in due:
        print(f"  - {ev['conf']['name']}: {ev['label']} on {ev['date']} "
              f"({days_phrase(ev['days_left'])})")

    if args.preview:
        with open(args.preview, "w", encoding="utf-8") as f:
            f.write(html_body(due, settings, changes))
        print(f"Preview saved to {args.preview}")

    people = read_subscribers()
    print(f"{len(people)} subscriber(s).")

    if args.dry_run:
        for person in people:
            mine, news = items_for(person, due), items_for(person, changes)
            print(f"  would email {mask(person['email'])}: {len(mine)} reminder(s), {len(news)} change(s)"
                  + (f" - subject: {subject_line(mine, news)}" if mine or news else " - nothing for their areas"))
        print("Dry run: nothing was sent and nothing was recorded.")
        return

    if not people:
        print("No subscribers yet. Add the SUBSCRIBERS secret (see README). Nothing sent.")
        return

    cfg = smtp_settings()
    if not cfg:
        print("Email is not set up yet (SMTP_USER / SMTP_PASSWORD missing). Nothing sent.")
        return

    messages = [build_message(p, items_for(p, due), settings, cfg["sender"], items_for(p, changes))
                for p in people if items_for(p, due) or items_for(p, changes)]
    failed = send_all(messages, cfg)

    # Record the nudges as done as long as at least one email went out, so the
    # people who did receive it are not emailed again tomorrow.
    if len(failed) < len(messages) or not messages:
        save_sent_log(sent | {ev["key"] for ev in due}, today)
        clear_changes()
    if failed:
        print(f"{len(failed)} email(s) failed.")
        sys.exit(1)
    print("Done.")


if __name__ == "__main__":
    main()

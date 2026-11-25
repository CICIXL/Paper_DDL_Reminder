"""
Automated checks for the whole app. Run them from the repository folder:

    uv run pytest

Each test gets its own empty database in a temporary folder, and emails are
captured in emailer.OUTBOX instead of being sent.
"""

import io
import json
import re
import tarfile
from datetime import date, datetime, timedelta, timezone

import pytest

from app import confdata, db, emailer, reminders, sync_dates
from app.main import create_app


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("BASE_URL", "https://paperddl.test")
    monkeypatch.setenv("TASK_TOKEN", "secret-token")
    monkeypatch.delenv("RESEND_API_KEY", raising=False)
    monkeypatch.delenv("DAILY_EMAIL_CAP", raising=False)
    monkeypatch.delenv("ENABLE_SCHEDULER", raising=False)
    emailer.OUTBOX.clear()
    app = create_app()
    app.testing = True
    return app.test_client()


def signup(client, email, areas=(), series=()):
    return client.post("/subscribe", data={"email": email, "area": list(areas), "series": list(series)})


def last_link(kind):
    """Pull the /confirm/... or /manage/... link out of the newest email."""
    return re.search(rf"https://paperddl\.test(/{kind}/[\w\-]+)", emailer.OUTBOX[-1]["text"]).group(1)


def subscribe_and_confirm(client, email, areas=(), series=()):
    signup(client, email, areas, series)
    path = last_link("confirm")
    client.get(path)
    return path.rsplit("/", 1)[1]          # the person's token


def user(email):
    with db.session() as conn:
        return db.user_by_email(conn, email)


# ------------------------------------------------------------------ pages

def test_public_pages_load(client):
    for path in ["/", "/c/icml-2027", "/subscribe", "/about", "/privacy",
                 "/deadlines.ics", "/sitemap.xml", "/robots.txt", "/healthz"]:
        assert client.get(path).status_code == 200, path
    assert client.get("/c/not-a-conference").status_code == 404
    home = client.get("/").get_data(as_text=True)
    assert "ICML 2027" in home and 'href="/c/icml-2027"' in home
    assert 'class="star"' in home and 'data-series="icml"' in home      # the favorite button
    assert 'id="search"' in home and 'id="view-timeline"' in home       # search box and timeline view
    assert "/subscribe?c=icml" in client.get("/c/icml-2027").get_data(as_text=True)
    assert 'rel="canonical" href="https://paperddl.test/"' in home


def test_subscribe_link_preselects_the_conference(client):
    page = client.get("/subscribe?c=icml").get_data(as_text=True)
    assert re.search(r'value="icml"\s+checked', page)
    assert not re.search(r'value="acl"\s+checked', page)


# ---------------------------------------------------------------- sign-up

def test_signup_confirm_and_manage(client):
    response = signup(client, "Alex@Example.com", areas=["NLP"], series=["icml"])
    assert response.status_code == 200 and "Check your inbox" in response.get_data(as_text=True)
    assert len(emailer.OUTBOX) == 1
    assert emailer.OUTBOX[0]["to"] == ["alex@example.com"]
    assert user("alex@example.com")["confirmed"] == 0

    response = client.get(last_link("confirm"))
    assert response.status_code == 302 and "/manage/" in response.headers["Location"]
    assert user("alex@example.com")["confirmed"] == 1

    token = user("alex@example.com")["token"]
    page = client.get(f"/manage/{token}")
    text = page.get_data(as_text=True)
    assert re.search(r'value="icml"\s+checked', text) and re.search(r'value="NLP"\s+checked', text)
    assert page.headers["Cache-Control"] == "no-store"
    assert page.headers["Referrer-Policy"] == "no-referrer"
    assert 'name="robots" content="noindex"' in text


def test_signup_rejects_bad_input(client):
    assert signup(client, "not-an-email", series=["icml"]).status_code == 400
    assert signup(client, "a@b.co").status_code == 400                      # nothing ticked
    assert signup(client, "a@b.co", series=["made-up"]).status_code == 400  # unknown option
    assert emailer.OUTBOX == [] and user("a@b.co") is None


def test_robot_trap_creates_nothing(client):
    response = client.post("/subscribe", data={"email": "bot@example.com", "series": ["icml"],
                                               "website": "http://spam.example"})
    assert response.status_code == 200
    assert user("bot@example.com") is None and emailer.OUTBOX == []


def test_repeat_signup_does_not_send_a_second_email_straight_away(client):
    signup(client, "alex@example.com", series=["icml"])
    signup(client, "alex@example.com", series=["acl"])
    assert len(emailer.OUTBOX) == 1
    with db.session() as conn:
        assert db.get_follows(conn, user("alex@example.com")["id"]) == (set(), {"acl"})


def test_confirmed_subscriber_cannot_be_changed_from_the_public_form(client):
    subscribe_and_confirm(client, "alex@example.com", series=["icml"])
    long_ago = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat(timespec="seconds")
    with db.session() as conn:
        conn.execute("UPDATE users SET last_mail_at = ?", (long_ago,))
    signup(client, "alex@example.com", series=["osdi"])       # someone else types Alex's address
    assert "manage" in emailer.OUTBOX[-1]["subject"].lower()
    with db.session() as conn:
        assert db.get_follows(conn, user("alex@example.com")["id"]) == (set(), {"icml"})


def test_signup_rate_limit_per_network(client):
    codes = [signup(client, f"p{i}@example.com", series=["icml"]).status_code for i in range(10)]
    assert codes[:8] == [200] * 8 and codes[8:] == [429, 429]


# ------------------------------------------------------ manage, unsubscribe

def test_manage_saves_new_choices(client):
    token = subscribe_and_confirm(client, "alex@example.com", series=["icml"])
    response = client.post(f"/manage/{token}", data={"area": ["Systems"], "series": ["acl", "bogus"]})
    assert "Saved." in response.get_data(as_text=True)
    with db.session() as conn:
        assert db.get_follows(conn, user("alex@example.com")["id"]) == ({"Systems"}, {"acl"})
    assert client.get("/manage/wrong-token").status_code == 404


def test_unsubscribe_asks_first_then_deletes_everything(client):
    token = subscribe_and_confirm(client, "alex@example.com", series=["icml"])
    assert client.get(f"/unsubscribe/{token}").status_code == 200
    assert user("alex@example.com") is not None            # opening the link deletes nothing
    client.post(f"/unsubscribe/{token}", data={"List-Unsubscribe": "One-Click"})
    assert user("alex@example.com") is None
    with db.session() as conn:
        assert conn.execute("SELECT COUNT(*) FROM follows").fetchone()[0] == 0
    assert client.get(f"/manage/{token}").status_code == 404


# -------------------------------------------------------------- reminders

def test_each_person_only_gets_what_they_follow(client):
    subscribe_and_confirm(client, "ml@example.com", series=["icml"])
    subscribe_and_confirm(client, "sys@example.com", areas=["Systems"])
    signup(client, "never-confirmed@example.com", series=["icml"])
    emailer.OUTBOX.clear()

    summary = reminders.send_reminders(today=date(2027, 1, 15))   # ICML paper deadline in 7 days
    assert summary["emails_sent"] == 1
    mail = emailer.OUTBOX[0]
    assert mail["to"] == ["ml@example.com"]
    assert "ICML 2027" in mail["subject"] and "in 7 days" in mail["subject"]
    assert "/unsubscribe/" in mail["headers"]["List-Unsubscribe"]
    assert "/manage/" in mail["text"]

    emailer.OUTBOX.clear()
    assert reminders.send_reminders(today=date(2027, 1, 16))["emails_sent"] == 0   # no repeats
    reminders.send_reminders(today=date(2027, 1, 21))                               # the 1-day nudge
    to_ml = [m for m in emailer.OUTBOX if m["to"] == ["ml@example.com"]]
    assert len(to_ml) == 1 and "tomorrow" in to_ml[0]["subject"]
    # The systems follower hears about MLSys, never about ICML.
    to_sys = [m for m in emailer.OUTBOX if m["to"] == ["sys@example.com"]]
    assert to_sys and all("ICML" not in m["text"] for m in to_sys)


def test_late_subscriber_still_gets_the_current_nudge(client):
    subscribe_and_confirm(client, "late@example.com", series=["icml"])
    emailer.OUTBOX.clear()
    reminders.send_reminders(today=date(2027, 1, 17))
    assert "in 5 days" in emailer.OUTBOX[0]["subject"]


def test_following_a_series_carries_over_to_next_year():
    next_year = {"id": "icml-2028", "areas": ["ML"]}
    assert reminders.follows_conf(next_year, set(), {"icml"})
    assert reminders.follows_conf(next_year, {"ML"}, set())
    assert not reminders.follows_conf(next_year, {"NLP"}, {"acl"})


def test_daily_cap_stops_and_resumes_next_day(client, monkeypatch):
    subscribe_and_confirm(client, "a@example.com", series=["icml"])
    subscribe_and_confirm(client, "b@example.com", series=["icml"])
    emailer.OUTBOX.clear()
    with db.session() as conn:
        used = db.emails_today(conn)
    monkeypatch.setenv("DAILY_EMAIL_CAP", str(used + 1))
    summary = reminders.send_reminders(today=date(2027, 1, 15))
    assert summary["emails_sent"] == 1 and summary["stopped_at_cap"]

    monkeypatch.setenv("DAILY_EMAIL_CAP", "95")            # "the next day"
    summary = reminders.send_reminders(today=date(2027, 1, 16))
    assert summary["emails_sent"] == 1
    assert emailer.OUTBOX[-1]["to"] == ["b@example.com"] and "in 6 days" in emailer.OUTBOX[-1]["subject"]


def test_date_change_goes_to_earlier_followers_only(client):
    subscribe_and_confirm(client, "old@example.com", series=["icml"])
    with db.session() as conn:
        conn.execute("UPDATE users SET confirmed_at = '2026-11-01T00:00:00+00:00'")
    subscribe_and_confirm(client, "new@example.com", series=["icml"])       # joins after the change
    subscribe_and_confirm(client, "other@example.com", series=["acl"])
    emailer.OUTBOX.clear()

    state = confdata.read_tracker_state()
    state.setdefault("overrides", {}).setdefault("icml-2027", {})["submission"] = \
        {"value": "2027-01-29", "base": "2027-01-22"}
    state["notices"] = [{"id": "icml-2027", "field": "submission", "old": "2027-01-22",
                         "new": "2027-01-29", "on": "2026-11-20"}]
    confdata.write_tracker_state(state)

    reminders.send_reminders(today=date(2026, 11, 26))
    assert [m["to"] for m in emailer.OUTBOX] == [["old@example.com"]]
    assert emailer.OUTBOX[0]["subject"].startswith("Date change: ICML 2027")
    assert "moved from Fri, Jan 22, 2027 to Fri, Jan 29, 2027" in emailer.OUTBOX[0]["text"]
    home = client.get("/c/icml-2027").get_data(as_text=True)
    assert "Jan 29, 2027" in home and "auto-updated" in home


# ----------------------------------------------------------- daily task

def fake_tracker():
    """A tiny copy of the tracker in its real file format."""
    text = """
- title: NeurIPS
  description: Conference on Neural Information Processing Systems
  sub: AI
  rank: {ccf: A}
  dblp: nips
  confs:
    - year: 2027
      id: nips27
      link: https://neurips.cc/
      timeline:
        - abstract_deadline: '2027-05-12 23:59:59'
          deadline: '2027-05-19 23:59:59'
      timezone: UTC-12
      date: December 5-11, 2027
      place: Somewhere, Europe
"""
    raw = io.BytesIO()
    with tarfile.open(fileobj=raw, mode="w:gz") as archive:
        data = text.encode()
        info = tarfile.TarInfo("ccf-deadlines-main/conference/AI/nips.yml")
        info.size = len(data)
        archive.addfile(info, io.BytesIO(data))
    return raw.getvalue()


def test_daily_task_syncs_and_reports(client, tmp_path):
    source = tmp_path / "tracker.tar.gz"
    source.write_bytes(fake_tracker())
    summary = reminders.run_daily(today=date(2026, 11, 26), tracker_source=str(source))
    assert summary["sync"]["ok"]
    assert any("NeurIPS 2027 submission" in line for line in summary["sync"]["changes"])
    page = client.get("/c/neurips-2027").get_data(as_text=True)
    assert "May 19, 2027" in page and "auto-updated" in page
    with db.session() as conn:
        assert db.meta_get(conn, "last_daily_run") is not None


def test_task_pages_need_the_token(client, monkeypatch):
    monkeypatch.setattr(sync_dates, "run", lambda **_: {"ok": True, "message": "skipped", "log": [], "applied": 0})
    assert client.post("/tasks/daily").status_code == 401
    assert client.post("/tasks/daily", headers={"Authorization": "Bearer wrong"}).status_code == 401
    good = {"Authorization": "Bearer secret-token"}
    response = client.post("/tasks/daily", headers=good)
    assert response.status_code == 200 and "reminders" in response.get_json()
    stats = client.get("/tasks/stats", headers=good).get_json()
    assert stats["confirmed_users"] == 0 and stats["last_daily_run"] != "never"


def test_unconfirmed_signups_are_forgotten_after_a_week(client):
    signup(client, "ghost@example.com", series=["icml"])
    with db.session() as conn:
        conn.execute("UPDATE users SET created_at = '2026-01-01T00:00:00+00:00'")
        assert db.purge_unconfirmed(conn) == 1
    assert user("ghost@example.com") is None

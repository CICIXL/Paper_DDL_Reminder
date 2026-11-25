"""
reminders.py - decides who should be emailed about what, and runs the daily task.

THE DAILY TASK (run_daily) does four things, in order:
    1. sync dates from the community tracker
    2. work out which reminders are "in season" today
    3. for each confirmed person, pick the ones for conferences they follow
       and have not been sent yet, and email them as one digest
    4. tidy up (forget sign-ups that were never confirmed)

HOW "IN SEASON" WORKS
    conferences.toml has a [reminders] section, e.g.  submission = [30, 7, 1]
    meaning: nudge 30, 7 and 1 days before a paper deadline. With 5 days left
    we are inside the "7" nudge. Each person gets each nudge once. Someone who
    subscribes with 5 days to go still gets that nudge on the next run, and the
    email always states the true number of days left.
"""

import json
import traceback
from datetime import date

from . import confdata, db, emailer, sync_dates
from .confdata import event_label, events, series


def candidates(conferences, reminders, today):
    """Every (milestone, nudge) that is in season today, for all conferences."""
    out = []
    for conf in conferences:
        for ev in events(conf):
            days_left = (ev["date"] - today).days
            if days_left < 0:
                continue
            inside = [n for n in reminders.get(ev["kind"], []) if days_left <= n]
            if not inside:
                continue
            nudge = min(inside)
            out.append({**ev, "days_left": days_left,
                        "key": f"{conf['id']}|{ev['kind']}|{ev['date']}|{nudge}"})
    out.sort(key=lambda e: (e["days_left"], e["conf"]["name"]))
    return out


def date_changes(conferences):
    """Date changes the tracker sync queued that are still true right now."""
    by_id = {c["id"]: c for c in conferences}
    out = []
    for n in confdata.read_tracker_state().get("notices", []):
        conf = by_id.get(n.get("id"))
        try:
            new = date.fromisoformat(n["new"])
            old = date.fromisoformat(n["old"]) if n.get("old") else None
        except (KeyError, ValueError):
            continue
        if conf is None or conf.get(n.get("field")) != new:
            continue   # it changed again since; skip the stale notice
        out.append({"conf": conf, "kind": n["field"], "label": event_label(conf, n["field"]),
                    "old": old, "new": new, "on": n.get("on", ""),
                    "key": f"change|{conf['id']}|{n['field']}|{new}"})
    out.sort(key=lambda c: c["new"])
    return out


def follows_conf(conf, areas, series_set):
    """Does a person with these choices follow this conference?"""
    return series(conf) in series_set or bool(set(conf["areas"]) & areas)


def for_person(things, areas, series_set, already_sent):
    return [t for t in things
            if t["key"] not in already_sent and follows_conf(t["conf"], areas, series_set)]


def send_reminders(today=None):
    """Steps 2 and 3 of the daily task. Returns a summary dictionary."""
    _, reminders, conferences = confdata.load()
    today = today or confdata.today_aoe()
    due = candidates(conferences, reminders, today)
    changes = date_changes(conferences)
    summary = {"in_season": len(due), "date_changes": len(changes),
               "emails_sent": 0, "emails_failed": 0, "stopped_at_cap": False}
    if not due and not changes:
        return summary

    with db.session() as conn:
        people = db.confirmed_users(conn)
    for person in people:
        # One short database session per person, so a crash halfway through
        # never loses the record of emails that already went out.
        with db.session() as conn:
            areas, series_set = db.get_follows(conn, person["id"])
            already = db.sent_keys(conn, person["id"])
            items = for_person(due, areas, series_set, already)
            # A change is only news to people who had subscribed before it happened.
            joined = (person["confirmed_at"] or "")[:10]
            news = [c for c in for_person(changes, areas, series_set, already)
                    if not c["on"] or joined <= c["on"]]
            if not items and not news:
                continue
            subject, html_body, text_body, unsub = emailer.digest(person["token"], items, news)
            try:
                ok = emailer.send(conn, person["email"], subject, html_body, text_body, unsub)
            except emailer.QuotaReached:
                summary["stopped_at_cap"] = True
                break   # the rest go out on tomorrow's run
            if ok:
                db.mark_sent(conn, person["id"], [t["key"] for t in items + news])
                summary["emails_sent"] += 1
            else:
                summary["emails_failed"] += 1
    return summary


def run_daily(today=None, do_sync=True, tracker_source=None):
    """The whole daily task. Never raises; problems are reported in the summary."""
    summary = {"ran_at": db.now()}
    if do_sync:
        try:
            result = sync_dates.run(today=today, source=tracker_source)
            summary["sync"] = {"ok": result["ok"], "message": result["message"],
                               "changes": [l.strip() for l in result["log"] if l.endswith("(applied)")]}
        except Exception as e:
            summary["sync"] = {"ok": False, "message": f"error: {e}"}
            traceback.print_exc()
    try:
        summary["reminders"] = send_reminders(today)
    except Exception as e:
        summary["reminders"] = {"error": str(e)}
        traceback.print_exc()
    try:
        with db.session() as conn:
            summary["unconfirmed_removed"] = db.purge_unconfirmed(conn)
            db.prune_sent(conn)
            db.meta_set(conn, "last_daily_run", summary["ran_at"])
            db.meta_set(conn, "last_daily_summary", json.dumps(summary))
    except Exception as e:
        summary["cleanup_error"] = str(e)
    print("[daily task]", json.dumps(summary))
    return summary

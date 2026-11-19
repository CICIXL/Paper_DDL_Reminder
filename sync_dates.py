"""
sync_dates.py - keeps the dates up to date automatically.

Once a day this script downloads the community-maintained conference tracker
"ccfddl" (https://github.com/ccfddl/ccf-deadlines - volunteers update it when
a conference announces or moves a date) and compares it with our own data.

WHAT IT DOES WITH WHAT IT FINDS
  - A date we list as TBA that the tracker now has   -> filled in automatically
  - A date the tracker has CHANGED since yesterday   -> updated automatically
  - A date where the tracker simply disagrees with what a person typed into
    conferences.toml the first time we look          -> left alone, and reported
    in the log (the hand-checked value is trusted until the tracker moves)
  - The tracker says "TBD" or has nothing            -> nothing changes

Automatic changes are NOT written into conferences.toml. They are kept in
state/tracker.json and layered on top when the site and emails are built.
They show an "auto-updated" tag so everyone knows nobody has checked them
against the official site yet.

To confirm an auto-updated date, type it into conferences.toml yourself.
Whatever a person types there always wins over the tracker.

Every change to a future date is also queued as a "date changed" notice,
which send_reminders.py emails to subscribers on its next run.

Commands:
    python3 sync_dates.py              normal run
    python3 sync_dates.py --dry-run    show what would change, save nothing

This is the one script that needs an extra package:  pip install pyyaml
(GitHub installs it automatically - see .github/workflows/daily.yml.)
"""

import argparse
import io
import json
import re
import sys
import tarfile
import urllib.request
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import confdata
from confdata import AOE, DATE_FIELDS, TRACKER_STATE, event_label, nice_date

TRACKER_URL = "https://github.com/ccfddl/ccf-deadlines/archive/refs/heads/main.tar.gz"

# Tracker field name -> our field name
ROUND_FIELDS = {
    "abstract_deadline": "abstract",
    "deadline": "submission",
    "rebuttal_deadline": "rebuttal_end",
    "decision_deadline": "decision",
}

MONTHS = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], start=1)}


# ---------------------------------------------------------------------------
# Reading the tracker
# ---------------------------------------------------------------------------

def norm(text):
    """'EuroSys' -> 'eurosys' so names can be compared loosely."""
    return re.sub(r"[^a-z0-9]", "", str(text).lower())


def download_tracker(source=None):
    """
    Return {normalised name: tracker entry}. One download gets every file.
    'source' can point at a local .tar.gz or folder, which is used for testing.
    """
    import yaml  # only needed here, so the rest of the project stays install-free

    texts = []
    if source and Path(source).is_dir():
        for path in Path(source).rglob("*.yml"):
            if "conference" in path.parts:
                texts.append(path.read_text(encoding="utf-8"))
    else:
        if source:
            raw = Path(source).read_bytes()
        else:
            request = urllib.request.Request(TRACKER_URL, headers={"User-Agent": "conference-reminders"})
            with urllib.request.urlopen(request, timeout=90) as response:
                raw = response.read()
        with tarfile.open(fileobj=io.BytesIO(raw), mode="r:gz") as archive:
            for member in archive.getmembers():
                parts = member.name.split("/")
                if member.isfile() and member.name.endswith(".yml") and "conference" in parts:
                    texts.append(archive.extractfile(member).read().decode("utf-8", "replace"))

    index = {}
    for text in texts:
        try:
            entries = yaml.safe_load(text)
        except yaml.YAMLError:
            continue  # one broken file in the tracker should not stop everything
        for entry in entries if isinstance(entries, list) else []:
            if isinstance(entry, dict) and entry.get("title"):
                index.setdefault(norm(entry["title"]), entry)
                if entry.get("dblp"):
                    index.setdefault(norm(entry["dblp"]), entry)
    return index


def utc_offset(name):
    """Tracker timezone text ('UTC-12', 'AoE', 'UTC+8', 'PT') -> a timezone."""
    name = str(name or "AoE").strip()
    if name.upper() == "AOE":
        return AOE
    if name.upper() == "PT":
        try:
            from zoneinfo import ZoneInfo
            return ZoneInfo("America/Los_Angeles")
        except Exception:
            return timezone(timedelta(hours=-8))
    match = re.fullmatch(r"UTC([+-]\d{1,2})(?::?(\d{2}))?", name, re.I)
    if match:
        hours = int(match.group(1))
        minutes = int(match.group(2) or 0) * (1 if hours >= 0 else -1)
        return timezone(timedelta(hours=hours, minutes=minutes))
    return AOE


def to_aoe_date(value, tz_name):
    """
    A tracker deadline -> the calendar date it falls on Anywhere on Earth.
    Returns None for 'TBD' or anything unreadable.
    """
    if isinstance(value, datetime):
        moment = value
    elif isinstance(value, date):
        moment = datetime(value.year, value.month, value.day, 23, 59, 59)
    else:
        text = str(value or "").strip()
        moment = None
        for pattern in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
            try:
                moment = datetime.strptime(text, pattern)
                break
            except ValueError:
                pass
        if moment is None:
            return None
        if len(text) <= 10:
            moment = moment.replace(hour=23, minute=59, second=59)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=utc_offset(tz_name))
    return moment.astimezone(AOE).date()


def parse_conference_dates(text, year):
    """
    'July 31 - August 6, 2027' or 'June 12-17, 2027' -> (start, end).
    Returns (None, None) if the text is in a shape we do not recognise.
    """
    text = re.sub(r"[–—]|\bto\b", "-", str(text or ""))
    month = r"([A-Za-z]{3,9})\.?"
    found_year = re.search(r"(20\d{2})", text)
    y = int(found_year.group(1)) if found_year else year

    def make(month_name, day):
        number = MONTHS.get(month_name[:3].lower())
        try:
            return date(y, number, int(day)) if number else None
        except ValueError:
            return None

    m = re.search(rf"{month}\s+(\d{{1,2}})\s*-\s*{month}\s+(\d{{1,2}})", text)
    if m:
        start, end = make(m.group(1), m.group(2)), make(m.group(3), m.group(4))
    else:
        m = re.search(rf"{month}\s+(\d{{1,2}})\s*-\s*(\d{{1,2}})", text)
        if m:
            start, end = make(m.group(1), m.group(2)), make(m.group(1), m.group(3))
        else:
            m = re.search(rf"{month}\s+(\d{{1,2}})\b", text)
            start = end = make(m.group(1), m.group(2)) if m else None
    if not start or not end or end < start:
        return None, None
    return start, end


def pick_round(rounds, our_submission, today):
    """
    Conferences with several submission rounds list each one. Choose the round
    that matches the one we track; failing that, the next upcoming round.
    """
    dated = [(r["submission"], r) for r in rounds if r.get("submission")]
    if not dated:
        return rounds[0] if rounds else {}
    if our_submission:
        gap, best = min(((abs((d - our_submission).days), r) for d, r in dated), key=lambda x: x[0])
        if gap <= 21:
            return best
    upcoming = [(d, r) for d, r in dated if d >= today]
    if upcoming:
        return min(upcoming, key=lambda x: x[0])[1]
    return max(dated, key=lambda x: x[0])[1]


def tracker_dates(entry, year, our_submission, today):
    """Pull the dates for one conference-year out of a tracker entry."""
    edition = next((c for c in entry.get("confs") or []
                    if isinstance(c, dict) and str(c.get("year")) == str(year)), None)
    if edition is None:
        return None
    tz = edition.get("timezone")
    rounds = []
    for item in edition.get("timeline") or []:
        if isinstance(item, dict):
            rounds.append({ours: to_aoe_date(item.get(theirs), tz)
                           for theirs, ours in ROUND_FIELDS.items() if item.get(theirs)})
    chosen = pick_round(rounds, our_submission, today)
    result = {k: v for k, v in chosen.items() if v}
    start, end = parse_conference_dates(edition.get("date"), int(year))
    if start:
        result["conference_start"], result["conference_end"] = start, end
    # Throw away anything wildly off, e.g. a typo in the tracker's year.
    return {k: v for k, v in result.items() if int(year) - 2 <= v.year <= int(year)}


# ---------------------------------------------------------------------------
# Comparing and applying
# ---------------------------------------------------------------------------

def conference_year(conf):
    if conf.get("year"):
        return int(conf["year"])
    match = re.search(r"(20\d{2})", conf["id"])
    if match:
        return int(match.group(1))
    return conf["conference_start"].year if conf.get("conference_start") else None


def tracker_names(conf):
    names = conf.get("tracker")
    if isinstance(names, str):
        names = [names]
    if not names:
        names = [re.split(r"[\s'\-/]", conf["name"])[0]]
    return [norm(n) for n in names]


def sync(conferences_raw, conferences_live, index, state, today):
    """
    conferences_raw   what is typed in conferences.toml
    conferences_live  the same, with earlier automatic updates layered on top
    Returns a list of human-readable log lines. Updates 'state' in place.
    """
    seen = state.setdefault("seen", {})
    overrides = state.setdefault("overrides", {})
    notices = state.setdefault("notices", [])
    conflicts = state["conflicts"] = {}   # rebuilt from scratch on every run
    live_by_id = {c["id"]: c for c in conferences_live}
    log = []

    for conf in conferences_raw:
        cid, year = conf["id"], conference_year(conf)
        entry = next((index[n] for n in tracker_names(conf) if n in index), None)
        if entry is None or year is None:
            log.append(f"  {conf['name']}: not found in the tracker")
            continue
        live = live_by_id[cid]
        found = tracker_dates(entry, year, live.get("submission"), today)
        if found is None:
            log.append(f"  {conf['name']}: the tracker has no {year} entry yet")
            continue

        conf_seen = seen.setdefault(cid, {})
        for field, theirs in found.items():
            typed = conf.get(field)            # in conferences.toml (may be None)
            showing = live.get(field)          # what the site shows right now
            before = conf_seen.get(field)      # what the tracker said last time
            conf_seen[field] = theirs.isoformat()
            if theirs == showing:
                continue

            first_look = before is None
            tracker_moved = before is not None and before != theirs.isoformat()
            if not (tracker_moved or (first_look and typed is None)):
                # The tracker disagrees with a date a person typed, and has not
                # moved since we first saw it. Keep the typed date, but flag
                # the disagreement on the site so someone checks.
                conflicts.setdefault(cid, {})[field] = theirs.isoformat()
                if first_look:
                    log.append(f"  {conf['name']} {field}: tracker says {theirs}, file says "
                               f"{showing} - left as typed, flagged on the site")
                continue

            conf_over = overrides.setdefault(cid, {})
            if theirs == typed:
                conf_over.pop(field, None)     # tracker came back to the typed value
            else:
                conf_over[field] = {"value": theirs.isoformat(),
                                    "base": typed.isoformat() if typed else None}
            what = "announced" if showing is None else f"moved from {showing}"
            log.append(f"  {conf['name']} {field}: {what} -> {theirs}  (applied)")

            still_matters = theirs >= today or (showing is not None and showing >= today)
            if still_matters and field != "conference_end":
                # If an earlier notice for this date was never emailed, replace
                # it and keep its "old" value, so people see one clean change.
                old_value = showing.isoformat() if showing else None
                for earlier in [n for n in notices if n["id"] == cid and n["field"] == field]:
                    old_value = earlier.get("old")
                    notices.remove(earlier)
                notices.append({"id": cid, "field": field,
                                "old": old_value,
                                "new": theirs.isoformat(), "on": today.isoformat()})

    # Tidy up: forget conferences that were removed, and stale notices.
    ids = {c["id"] for c in conferences_raw}
    for store in (seen, overrides, conflicts):
        for cid in [k for k in store if k not in ids]:
            del store[cid]
    for cid in [k for k, v in overrides.items() if not v]:
        del overrides[cid]
    cutoff = (today - timedelta(days=7)).isoformat()
    state["notices"] = [n for n in notices if n.get("on", "") >= cutoff and n["id"] in ids
                        and n.get("old") != n.get("new")]
    return log


def main():
    parser = argparse.ArgumentParser(description="Sync dates from the ccfddl tracker.")
    parser.add_argument("--dry-run", action="store_true", help="show changes, save nothing")
    parser.add_argument("--today", metavar="YYYY-MM-DD", help="pretend today is this date")
    parser.add_argument("--source", metavar="PATH",
                        help="use a local copy of the tracker instead of downloading (for testing)")
    args = parser.parse_args()

    try:
        import yaml  # noqa: F401
    except ImportError:
        print("Skipping the sync: the 'pyyaml' package is not installed.\n"
              "Install it once with:  pip install pyyaml")
        return

    try:
        _, _, raw = confdata.load(with_overrides=False)
        _, _, live = confdata.load()
    except confdata.DataError as e:
        confdata.fail(str(e))
    today = date.fromisoformat(args.today) if args.today else confdata.today_aoe()

    try:
        index = download_tracker(args.source)
    except Exception as e:  # network trouble must never break the daily run
        print(f"Skipping the sync: could not get the tracker ({e}).")
        return
    if len(index) < 5 and not args.source:
        print("Skipping the sync: the tracker download looks empty.")
        return

    state = confdata.read_tracker_state()
    log = sync(raw, live, index, state, today)
    applied = [line for line in log if line.endswith("(applied)")]
    print(f"Checked {len(raw)} conferences against the tracker ({len(index)} names known).")
    print("\n".join(log) if log else "  Everything matches.")
    print(f"{len(applied)} date(s) updated automatically.")

    if args.dry_run:
        print("Dry run: nothing was saved.")
        return
    state["last_sync"] = today.isoformat()
    TRACKER_STATE.parent.mkdir(exist_ok=True)
    TRACKER_STATE.write_text(json.dumps(state, indent=1, sort_keys=True) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()

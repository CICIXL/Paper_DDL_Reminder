"""
confdata.py - shared helper code.

Both build_site.py (makes the web page) and send_reminders.py (sends the
emails) need to read conferences.toml and work out "how many days until X?".
That logic lives here once, so the two scripts can never disagree.

You should not need to edit this file.
"""

import json
import sys
import tomllib  # built into Python 3.11+, reads .toml files
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

# The folder this file lives in. Every other path is worked out from here,
# so the scripts behave the same no matter where you run them from.
ROOT = Path(__file__).resolve().parent
DATA_FILE = ROOT / "conferences.toml"
# Where sync_dates.py keeps the dates it updated automatically.
TRACKER_STATE = ROOT / "state" / "tracker.json"

# "Anywhere on Earth" is the timezone most conferences use for deadlines.
# It is 12 hours behind UTC: a deadline has not passed until it has passed
# everywhere in the world.
AOE = timezone(timedelta(hours=-12), name="AoE")

# Every kind of date we track:  field name -> (label, is it a hard deadline?)
# "Hard deadline" just changes the wording ("due in 3 days" vs "in 3 days").
EVENT_TYPES = {
    "abstract":         ("Abstract deadline", True),
    "submission":       ("Paper submission deadline", True),
    "commitment":       ("Commitment deadline", True),
    "rebuttal_start":   ("Rebuttal opens", False),
    "rebuttal_end":     ("Rebuttal closes", True),
    "decision":         ("Decision notification", False),
    "conference_start": ("Conference starts", False),
}
DATE_FIELDS = list(EVENT_TYPES) + ["conference_end"]

ALLOWED_FIELDS = set(DATE_FIELDS) | {
    "id", "name", "full_name", "areas", "location", "url", "note",
    "unverified", "time_note", "deadline_time_utc", "submission_label",
    "tracker", "year",
}

DEFAULT_REMINDERS = {
    "abstract": [7, 1],
    "submission": [30, 7, 1],
    "commitment": [7, 1],
    "rebuttal_start": [0],
    "rebuttal_end": [1],
    "decision": [0],
    "conference_start": [30],
}


class DataError(Exception):
    """Raised when conferences.toml has a mistake, with a readable message."""


def _to_date(value, where):
    """Accept a date written with or without quotes and return a date object."""
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value.strip())
        except ValueError:
            pass
    raise DataError(f"{where}: '{value}' is not a date. Write it like 2027-01-22")


def read_tracker_state():
    """What sync_dates.py saved last time (empty if it has never run)."""
    try:
        return json.loads(TRACKER_STATE.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def apply_overrides(conf, overrides):
    """
    Layer the automatically synced dates on top of what is typed in
    conferences.toml. An automatic date is only used while the typed value is
    still what it was when the update was made - so the moment a person edits
    that date by hand, the hand-typed value wins.
    """
    conf["auto"] = []
    for field, item in overrides.get(conf["id"], {}).items():
        if field not in DATE_FIELDS:
            continue
        typed = conf.get(field)
        if (typed.isoformat() if typed else None) != item.get("base"):
            continue
        try:
            conf[field] = date.fromisoformat(item["value"])
        except (KeyError, ValueError):
            continue
        conf["auto"].append(field)
        if field not in conf["unverified"]:
            conf["unverified"].append(field)


def load(path=DATA_FILE, with_overrides=True):
    """
    Read conferences.toml and return (settings, reminders, conferences).

    conferences is a list of dictionaries, one per conference, with every
    date already turned into a proper date object.
    """
    try:
        with open(path, "rb") as f:
            raw = tomllib.load(f)
    except tomllib.TOMLDecodeError as e:
        raise DataError(f"conferences.toml could not be read: {e}") from e

    settings = raw.get("settings", {})
    reminders = dict(DEFAULT_REMINDERS)
    for key, days in raw.get("reminders", {}).items():
        if key not in EVENT_TYPES:
            raise DataError(f"[reminders]: '{key}' is not a known date type")
        reminders[key] = sorted({int(d) for d in days}, reverse=True)

    conferences, seen_ids = [], set()
    for i, c in enumerate(raw.get("conference", []), start=1):
        where = f"Conference #{i} ({c.get('name', 'no name')})"
        for needed in ("id", "name"):
            if not c.get(needed):
                raise DataError(f"{where}: missing '{needed}'")
        unknown = set(c) - ALLOWED_FIELDS
        if unknown:
            raise DataError(
                f"{where}: unknown field(s) {sorted(unknown)} - is it a typo?"
            )
        if c["id"] in seen_ids:
            raise DataError(f"{where}: the id '{c['id']}' is used twice")
        seen_ids.add(c["id"])

        conf = dict(c)
        for field in DATE_FIELDS:
            if field in conf:
                conf[field] = _to_date(conf[field], f"{where}, {field}")
        conf.setdefault("areas", [])
        conf.setdefault("unverified", [])
        for field in conf["unverified"]:
            if field not in DATE_FIELDS:
                raise DataError(f"{where}: unverified lists unknown field '{field}'")
        conf["unverified"] = list(conf["unverified"])
        conf["auto"] = []
        conf["conflicts"] = {}
        conferences.append(conf)

    if with_overrides:
        tracker = read_tracker_state()
        for conf in conferences:
            apply_overrides(conf, tracker.get("overrides", {}))
            # Dates where the tracker disagrees with what a person typed.
            conf["conflicts"] = {}
            for field, value in tracker.get("conflicts", {}).get(conf["id"], {}).items():
                try:
                    other = date.fromisoformat(value)
                except ValueError:
                    continue
                if field in DATE_FIELDS and conf.get(field) not in (None, other):
                    conf["conflicts"][field] = other

    return settings, reminders, conferences


def today_aoe():
    """Today's date in the Anywhere-on-Earth timezone."""
    return datetime.now(AOE).date()


def event_label(conf, kind):
    """Human wording for one kind of date, e.g. 'Paper submission deadline'."""
    if kind == "submission" and conf.get("submission_label"):
        return conf["submission_label"]
    return EVENT_TYPES[kind][0]


def events(conf):
    """
    List every dated milestone of one conference, in calendar order.
    Each item is a small dictionary describing that milestone.
    """
    out = []
    for kind, (_, is_deadline) in EVENT_TYPES.items():
        if kind in conf:
            out.append({
                "conf": conf,
                "kind": kind,
                "label": event_label(conf, kind),
                "date": conf[kind],
                "is_deadline": is_deadline,
                "unverified": kind in conf["unverified"],
                "auto": kind in conf["auto"],
            })
    out.sort(key=lambda e: e["date"])
    return out


def moment_utc(conf, kind):
    """
    The exact instant a milestone happens, in UTC - used for live countdowns.

    Deadlines count down to the end of the day Anywhere on Earth, unless the
    conference sets deadline_time_utc. Other milestones (decision, conference
    start) count down to the start of that day.
    """
    d = conf[kind]
    is_deadline = EVENT_TYPES[kind][1] if kind in EVENT_TYPES else False
    if is_deadline and conf.get("deadline_time_utc"):
        hh, mm = conf["deadline_time_utc"].split(":")
        return datetime(d.year, d.month, d.day, int(hh), int(mm), 59, tzinfo=timezone.utc)
    if is_deadline or kind == "conference_end":
        local = datetime(d.year, d.month, d.day, 23, 59, 59, tzinfo=AOE)
    else:
        local = datetime(d.year, d.month, d.day, 0, 0, 0, tzinfo=AOE)
    return local.astimezone(timezone.utc)


def nice_date(d, with_year=True):
    """2027-01-22 -> 'Fri, Jan 22, 2027'"""
    text = f"{d:%a}, {d:%b} {d.day}"
    return f"{text}, {d.year}" if with_year else text


def date_range(start, end):
    """Two dates -> 'Jul 31 - Aug 6, 2027'"""
    if end is None or end == start:
        return f"{start:%b} {start.day}, {start.year}"
    if start.year == end.year and start.month == end.month:
        return f"{start:%b} {start.day}–{end.day}, {end.year}"
    if start.year == end.year:
        return f"{start:%b} {start.day} – {end:%b} {end.day}, {end.year}"
    return f"{start:%b} {start.day}, {start.year} – {end:%b} {end.day}, {end.year}"


def days_phrase(n):
    """Number of days -> 'today' / 'tomorrow' / 'in 7 days'"""
    if n == 0:
        return "today"
    if n == 1:
        return "tomorrow"
    return f"in {n} days"


def fail(message):
    """Print a readable error and stop."""
    print(f"\nERROR: {message}\n", file=sys.stderr)
    sys.exit(1)

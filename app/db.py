"""
db.py - the database: who subscribed, what they follow, what they were sent.

It uses SQLite, a database that lives in a single file (paperddl.db in the
data folder). Conference dates are NOT stored here; they come from
conferences.toml. The database only holds things people create.

TABLES
    users    one row per person:  email, a secret token, confirmed or not
    follows  one row per choice:  (user, 'area', 'ML') or (user, 'series', 'icml')
    sent     one row per email item already sent to a person, to avoid repeats
    meta     a few named values, such as today's email count
"""

import secrets
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

from . import confdata

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id                INTEGER PRIMARY KEY,
    email             TEXT NOT NULL UNIQUE,
    token             TEXT NOT NULL UNIQUE,
    confirmed         INTEGER NOT NULL DEFAULT 0,
    created_at        TEXT NOT NULL,
    confirmed_at      TEXT,
    last_mail_at      TEXT,
    signup_mails      INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS follows (
    user_id  INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    kind     TEXT NOT NULL CHECK (kind IN ('area', 'series')),
    value    TEXT NOT NULL,
    PRIMARY KEY (user_id, kind, value)
);
CREATE TABLE IF NOT EXISTS sent (
    user_id  INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    key      TEXT NOT NULL,
    sent_at  TEXT NOT NULL,
    PRIMARY KEY (user_id, key)
);
CREATE TABLE IF NOT EXISTS meta (
    key    TEXT PRIMARY KEY,
    value  TEXT NOT NULL
);
"""


def db_path():
    return confdata.state_dir() / "paperddl.db"


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@contextmanager
def session():
    """
    Open the database, hand it to the caller, then save and close.
    Used as:   with db.session() as conn: ...
    If anything goes wrong inside the block, nothing is saved.
    """
    conn = sqlite3.connect(db_path(), timeout=30)
    conn.row_factory = sqlite3.Row            # rows behave like dictionaries
    conn.execute("PRAGMA foreign_keys = ON")  # deleting a user deletes their rows
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init():
    """Create the tables if they do not exist yet. Safe to call every start."""
    with session() as conn:
        conn.execute("PRAGMA journal_mode = WAL")
        conn.executescript(SCHEMA)


# ---------------------------------------------------------------------------
# Users
# ---------------------------------------------------------------------------

def user_by_email(conn, email):
    return conn.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()


def user_by_token(conn, token):
    if not token or len(token) > 200:
        return None
    return conn.execute("SELECT * FROM users WHERE token = ?", (token,)).fetchone()


def create_user(conn, email):
    token = secrets.token_urlsafe(32)   # a long random string nobody can guess
    conn.execute("INSERT INTO users (email, token, created_at) VALUES (?, ?, ?)",
                 (email, token, now()))
    return user_by_email(conn, email)


def confirm_user(conn, user_id):
    conn.execute("UPDATE users SET confirmed = 1, confirmed_at = COALESCE(confirmed_at, ?) "
                 "WHERE id = ?", (now(), user_id))


def delete_user(conn, user_id):
    conn.execute("DELETE FROM users WHERE id = ?", (user_id,))


def note_signup_mail(conn, user_id):
    conn.execute("UPDATE users SET last_mail_at = ?, signup_mails = signup_mails + 1 "
                 "WHERE id = ?", (now(), user_id))


def confirmed_users(conn):
    return conn.execute("SELECT * FROM users WHERE confirmed = 1 ORDER BY id").fetchall()


def purge_unconfirmed(conn, days=7):
    """Forget sign-ups that were never confirmed."""
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat(timespec="seconds")
    cur = conn.execute("DELETE FROM users WHERE confirmed = 0 AND created_at < ?", (cutoff,))
    return cur.rowcount


# ---------------------------------------------------------------------------
# Follows
# ---------------------------------------------------------------------------

def set_follows(conn, user_id, areas, series):
    """Replace everything a person follows with a new selection."""
    conn.execute("DELETE FROM follows WHERE user_id = ?", (user_id,))
    rows = [(user_id, "area", a) for a in sorted(areas)] + \
           [(user_id, "series", s) for s in sorted(series)]
    conn.executemany("INSERT INTO follows (user_id, kind, value) VALUES (?, ?, ?)", rows)


def get_follows(conn, user_id):
    """Return (set of areas, set of series) for one person."""
    areas, series = set(), set()
    for row in conn.execute("SELECT kind, value FROM follows WHERE user_id = ?", (user_id,)):
        (areas if row["kind"] == "area" else series).add(row["value"])
    return areas, series


# ---------------------------------------------------------------------------
# What was already sent
# ---------------------------------------------------------------------------

def sent_keys(conn, user_id):
    return {r["key"] for r in conn.execute("SELECT key FROM sent WHERE user_id = ?", (user_id,))}


def mark_sent(conn, user_id, keys):
    conn.executemany("INSERT OR IGNORE INTO sent (user_id, key, sent_at) VALUES (?, ?, ?)",
                     [(user_id, k, now()) for k in keys])


def prune_sent(conn, days=120):
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat(timespec="seconds")
    conn.execute("DELETE FROM sent WHERE sent_at < ?", (cutoff,))


# ---------------------------------------------------------------------------
# Small named values
# ---------------------------------------------------------------------------

def meta_get(conn, key, default=None):
    row = conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else default


def meta_set(conn, key, value):
    conn.execute("INSERT INTO meta (key, value) VALUES (?, ?) "
                 "ON CONFLICT(key) DO UPDATE SET value = excluded.value", (key, str(value)))


def emails_today(conn):
    day = datetime.now(timezone.utc).date().isoformat()
    return int(meta_get(conn, f"emails:{day}", "0"))


def count_email(conn):
    day = datetime.now(timezone.utc).date().isoformat()
    meta_set(conn, f"emails:{day}", emails_today(conn) + 1)


def stats(conn):
    one = lambda sql: conn.execute(sql).fetchone()[0]
    return {
        "confirmed_users": one("SELECT COUNT(*) FROM users WHERE confirmed = 1"),
        "unconfirmed_users": one("SELECT COUNT(*) FROM users WHERE confirmed = 0"),
        "follows": one("SELECT COUNT(*) FROM follows"),
        "emails_sent_today": emails_today(conn),
        "last_daily_run": meta_get(conn, "last_daily_run", "never"),
        "last_daily_summary": meta_get(conn, "last_daily_summary", ""),
    }

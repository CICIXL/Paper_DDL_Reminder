"""
config.py - every setting the app reads from its environment, in one place.

On the server these are set in the hosting dashboard (Render calls them
"environment variables"). On your own computer none of them are required:
the app then runs in a safe local mode that prints emails instead of sending.
"""

import os

SITE_NAME = "PaperDDL"

# The daily task (sync dates, send reminders) runs at this time, in UTC.
# 14:17 UTC is early morning on the US and Canadian west coast.
DAILY_RUN_UTC = (14, 17)


def _env(name, default=""):
    return (os.environ.get(name) or default).strip()


def base_url():
    """The site's public address, used to build the links inside emails."""
    # BASE_URL is what you set once you have your own domain. Until then the
    # address Render gives the app (RENDER_EXTERNAL_URL, set by Render) is used.
    return (_env("BASE_URL") or _env("RENDER_EXTERNAL_URL") or "http://127.0.0.1:5000").rstrip("/")


def mail_from():
    """The sender shown on emails. Its domain must be verified in Resend."""
    return _env("MAIL_FROM", "PaperDDL <reminders@paperddl.org>")


def resend_key():
    """The Resend API key. Empty means: do not send, just print the email."""
    return _env("RESEND_API_KEY")


def task_token():
    """A password that protects the /tasks pages."""
    return _env("TASK_TOKEN")


def contact_email():
    return _env("CONTACT_EMAIL")


def daily_email_cap():
    """Stop sending for the day at this many emails (Resend's free plan allows 100)."""
    return int(_env("DAILY_EMAIL_CAP", "95"))


def scheduler_on():
    """Set ENABLE_SCHEDULER=1 on the server so the daily task runs by itself."""
    return _env("ENABLE_SCHEDULER") == "1"

"""
emailer.py - writes the emails and hands them to Resend for delivery.

Three kinds of email leave the app:
    1. "Confirm your subscription"  - sent when someone signs up
    2. "Your manage link"           - sent when an existing subscriber signs up again
    3. The reminder digest          - sent by the daily task

If no RESEND_API_KEY is set (for example on your own computer), nothing is
sent. The email is printed to the terminal and kept in OUTBOX instead, which
is also how the automated tests check what would have been sent.
"""

import html
import json
import time
import urllib.error
import urllib.request

from . import config, db
from .confdata import days_phrase, nice_date

RESEND_URL = "https://api.resend.com/emails"
OUTBOX = []          # emails "sent" in local mode, newest last
esc = html.escape


class QuotaReached(Exception):
    """Raised when today's email allowance is used up."""


# ---------------------------------------------------------------------------
# Sending
# ---------------------------------------------------------------------------

def send(conn, to, subject, html_body, text_body, unsubscribe_url=None):
    """
    Send one email. Returns True if it went out.
    Raises QuotaReached when the daily allowance is spent, so the caller can
    stop for the day instead of failing one email at a time.
    """
    if db.emails_today(conn) >= config.daily_email_cap():
        raise QuotaReached("daily email cap reached")

    message = {
        "from": config.mail_from(),
        "to": [to],
        "subject": subject,
        "html": html_body,
        "text": text_body,
    }
    if unsubscribe_url:
        # Lets Gmail, Apple Mail and others show their own "Unsubscribe" button.
        message["headers"] = {
            "List-Unsubscribe": f"<{unsubscribe_url}>",
            "List-Unsubscribe-Post": "List-Unsubscribe=One-Click",
        }

    key = config.resend_key()
    if not key:
        OUTBOX.append(message)
        print(f"[email not sent - local mode] to={to} subject={subject!r}")
        db.count_email(conn)
        return True

    request = urllib.request.Request(
        RESEND_URL,
        data=json.dumps(message).encode("utf-8"),
        method="POST",
        headers={
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
            "User-Agent": "paperddl/1.0",
        },
    )
    for attempt in (1, 2):
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                response.read()
            db.count_email(conn)
            time.sleep(0.6)       # stay well under Resend's requests-per-second limit
            return True
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", "replace")[:300]
            if e.code == 429 and "quota" in detail.lower():
                raise QuotaReached(detail) from e
            if e.code == 429 and attempt == 1:
                time.sleep(2)     # going too fast: wait and try once more
                continue
            print(f"[email FAILED] to={mask(to)} status={e.code} {detail}")
            return False
        except (urllib.error.URLError, TimeoutError) as e:
            print(f"[email FAILED] to={mask(to)} {e}")
            return False
    return False


def mask(address):
    """Hide most of an address in logs."""
    name, _, domain = address.partition("@")
    return f"{name[:2]}***@{domain}"


# ---------------------------------------------------------------------------
# The shared look of every email
# ---------------------------------------------------------------------------

def _wrap(title, inner, footer):
    return f"""<!DOCTYPE html>
<html><body style="margin:0;padding:0;background:#f6f4ef">
<div style="max-width:560px;margin:0 auto;padding:24px 20px;font-family:-apple-system,'Segoe UI',Helvetica,Arial,sans-serif;color:#1d1b16">
  <div style="font-size:13px;letter-spacing:.08em;text-transform:uppercase;color:#6b665c">{esc(config.SITE_NAME)}</div>
  <h1 style="font-size:22px;margin:6px 0 14px">{esc(title)}</h1>
  {inner}
  <p style="color:#6b665c;font-size:12px;margin-top:24px;border-top:1px solid #e4dfd3;padding-top:14px">{footer}</p>
</div>
</body></html>"""


def _button(url, label):
    return (f'<p style="margin:18px 0"><a href="{esc(url)}" style="display:inline-block;background:#1d1b16;'
            f'color:#ffffff;text-decoration:none;font-weight:600;padding:11px 20px;border-radius:999px">'
            f'{esc(label)}</a></p>')


# ---------------------------------------------------------------------------
# 1 and 2: sign-up emails
# ---------------------------------------------------------------------------

def confirmation(token):
    url = f"{config.base_url()}/confirm/{token}"
    subject = "Confirm your PaperDDL reminders"
    inner = ('<p style="font-size:15px;line-height:1.5">Someone asked for conference deadline '
             'reminders at this address. If that was you, confirm below and you are set.</p>'
             + _button(url, "Confirm my subscription") +
             '<p style="font-size:13px;color:#6b665c">If the button does not work, copy this link '
             f'into your browser:<br>{esc(url)}</p>')
    footer = ("If this was not you, ignore this email. You will not hear from us again "
              "and the address is forgotten after 7 days.")
    text = (f"Confirm your PaperDDL reminders\n\nSomeone asked for conference deadline reminders at "
            f"this address. If that was you, open this link to confirm:\n{url}\n\n{footer}")
    return subject, _wrap("One click to confirm", inner, footer), text


def manage_link(token):
    url = f"{config.base_url()}/manage/{token}"
    subject = "Your PaperDDL manage link"
    inner = ('<p style="font-size:15px;line-height:1.5">You are already subscribed. Use your private '
             'link to change which conferences you follow.</p>' + _button(url, "Manage my reminders"))
    footer = "Keep this link to yourself: anyone who has it can change your choices."
    text = f"You are already subscribed to PaperDDL.\n\nManage your reminders here:\n{url}\n\n{footer}"
    return subject, _wrap("You are already subscribed", inner, footer), text


# ---------------------------------------------------------------------------
# 3: the reminder digest
# ---------------------------------------------------------------------------

def change_phrase(change):
    if change["old"]:
        return f"moved from {nice_date(change['old'])} to {nice_date(change['new'])}"
    return f"announced: {nice_date(change['new'])}"


def _lower_first(text):
    return text[0].lower() + text[1:] if text else text


def digest_subject(items, changes):
    if not items:
        first = changes[0]
        text = (f"Date change: {first['conf']['name']} {_lower_first(first['label'])} "
                f"is now {nice_date(first['new'])}")
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


def _time_note(ev):
    if not ev["is_deadline"]:
        return ""
    return ev["conf"].get("time_note") or "end of day, Anywhere on Earth"


def _caution(ev):
    if ev["auto"]:
        return "Auto-updated from a tracker - check the official site."
    if ev["unverified"]:
        return "Unconfirmed date - check the official site."
    return ""


def digest(token, items, changes):
    """Build (subject, html, text, unsubscribe link) for one person's reminders."""
    base = config.base_url()
    manage_url = f"{base}/manage/{token}"
    unsubscribe_url = f"{base}/unsubscribe/{token}"

    # ---- plain-text version --------------------------------------------
    lines = []
    if changes:
        lines += ["DATE CHANGES", ""]
        for ch in changes:
            lines.append(f"* {ch['conf']['name']} - {ch['label']} {change_phrase(ch)}")
        lines += ["  (Picked up automatically from a community tracker - confirm on the official site.)", ""]
    if items:
        lines += ["COMING UP", ""]
    for ev in items:
        conf = ev["conf"]
        lines.append(f"* {conf['name']} - {ev['label']}")
        when = f"  {nice_date(ev['date'])} ({days_phrase(ev['days_left'])})"
        if _time_note(ev):
            when += f" - {_time_note(ev)}"
        lines.append(when)
        if _caution(ev):
            lines.append(f"  {_caution(ev)}")
        lines.append(f"  {base}/c/{conf['id']}")
        lines.append("")
    lines += ["Dates can move. Always confirm on the official conference site.", "",
              f"Change what you follow: {manage_url}", f"Unsubscribe: {unsubscribe_url}"]
    text = "\n".join(lines)

    # ---- HTML version ---------------------------------------------------
    blocks = ""
    if changes:
        rows = "".join(
            f'<div style="margin:8px 0 0;font-size:15px"><a href="{esc(base)}/c/{esc(ch["conf"]["id"])}" '
            f'style="color:#1d1b16;font-weight:600">{esc(ch["conf"]["name"])}</a> &middot; '
            f'{esc(ch["label"])} {esc(change_phrase(ch))}</div>' for ch in changes)
        blocks += ('<div style="background:#fff4d6;border:1px solid #e6c15a;border-radius:10px;'
                   'padding:14px 16px;margin:0 0 18px;color:#1d1b16">'
                   '<div style="font-size:13px;letter-spacing:.08em;text-transform:uppercase;'
                   'font-weight:700;color:#7a4b00">Date changes</div>' + rows +
                   '<div style="font-size:12px;color:#6b665c;margin-top:10px">Picked up automatically '
                   'from a community tracker. Confirm on the official site.</div></div>')
    if items:
        rows = ""
        for ev in items:
            conf = ev["conf"]
            badge = "#b3261e" if ev["days_left"] <= 3 else "#2f5fd0"
            extra = ""
            if _time_note(ev):
                extra += f'<div style="color:#6b665c;font-size:13px">{esc(_time_note(ev))}</div>'
            if _caution(ev):
                extra += f'<div style="color:#9a5b00;font-size:13px">{esc(_caution(ev))}</div>'
            rows += f"""
        <tr>
          <td style="padding:14px 0;border-bottom:1px solid #e4dfd3;vertical-align:top">
            <div style="font-size:17px;font-weight:600"><a href="{esc(base)}/c/{esc(conf['id'])}" style="color:#1d1b16;text-decoration:none">{esc(conf['name'])}</a></div>
            <div style="font-size:15px;margin-top:2px">{esc(ev['label'])}</div>
            <div style="color:#6b665c;font-size:14px;margin-top:2px">{esc(nice_date(ev['date']))}</div>
            {extra}
          </td>
          <td style="padding:14px 0 14px 12px;border-bottom:1px solid #e4dfd3;vertical-align:top;text-align:right;white-space:nowrap">
            <span style="display:inline-block;background:{badge};color:#ffffff;border-radius:999px;padding:4px 12px;font-size:13px;font-weight:600">{esc(days_phrase(ev['days_left']))}</span>
          </td>
        </tr>"""
        blocks += ('<table role="presentation" width="100%" cellspacing="0" cellpadding="0" '
                   f'style="border-collapse:collapse">{rows}\n  </table>')

    footer = ('Dates can move. Always confirm on the official conference site.<br><br>'
              f'You get this because you follow these conferences on {esc(config.SITE_NAME)}. '
              f'<a href="{esc(manage_url)}" style="color:#6b665c">Change what you follow</a> &middot; '
              f'<a href="{esc(unsubscribe_url)}" style="color:#6b665c">Unsubscribe</a>')
    title = "Coming up" if items else "A date changed"
    return digest_subject(items, changes), _wrap(title, blocks, footer), text, unsubscribe_url

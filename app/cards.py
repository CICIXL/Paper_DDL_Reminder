"""
cards.py - turns conference data into the HTML cards and the calendar feed.

The web pages (app/templates) place these cards into the page layout.
Styles live in app/static/site.css and the countdown script in app/static/site.js.
"""

import html
import json
import re
from datetime import timedelta

from .confdata import (EVENT_TYPES, date_range, event_label, events, moment_utc,
                       nice_date, series)

esc = html.escape


def slug(text):
    """'AI for Science' -> 'ai-for-science' (used for CSS class names)."""
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def ms(moment):
    """A datetime -> milliseconds since 1970, which is what JavaScript uses."""
    return int(moment.timestamp() * 1000)


def unconfirmed_tag(conf, *fields):
    if any(f in conf["auto"] for f in fields):
        return ' <span class="tag">auto-updated</span>'
    if any(f in conf["unverified"] for f in fields):
        return ' <span class="tag">unconfirmed</span>'
    return ""


# ---------------------------------------------------------------------------
# One conference card
# ---------------------------------------------------------------------------

def row(label, value_html, kind, end_ms=None, start_ms=None, tba=False):
    attrs = f' data-kind="{kind}"'
    if end_ms is not None:
        attrs += f' data-ts="{end_ms}"'
    if start_ms is not None:
        attrs += f' data-start="{start_ms}"'
    cls = "row tba" if tba else "row"
    return (f'<li class="{cls}"{attrs}><span class="k">{esc(label)}</span>'
            f'<span class="v">{value_html}</span><span class="rel"></span></li>')


def simple_row(conf, kind, short_label, always=True):
    """A row with a single date, e.g. the paper deadline."""
    if kind in conf:
        value = esc(nice_date(conf[kind])) + unconfirmed_tag(conf, kind)
        return row(short_label, value, kind, end_ms=ms(moment_utc(conf, kind)))
    if always:
        return row(short_label, "TBA", kind, tba=True)
    return ""


def rebuttal_row(conf):
    start, end = conf.get("rebuttal_start"), conf.get("rebuttal_end")
    tag = unconfirmed_tag(conf, "rebuttal_start", "rebuttal_end")
    if start and end:
        return row("Rebuttal", esc(date_range(start, end)) + tag, "rebuttal",
                   end_ms=ms(moment_utc(conf, "rebuttal_end")),
                   start_ms=ms(moment_utc(conf, "rebuttal_start")))
    if start:
        return row("Rebuttal", "Opens " + esc(nice_date(start)) + tag, "rebuttal_open",
                   end_ms=ms(moment_utc(conf, "rebuttal_start")))
    if end:
        return row("Rebuttal", "Closes " + esc(nice_date(end)) + tag, "rebuttal",
                   end_ms=ms(moment_utc(conf, "rebuttal_end")))
    return row("Rebuttal", "TBA", "rebuttal", tba=True)


def conference_row(conf):
    start, end = conf.get("conference_start"), conf.get("conference_end")
    if not start:
        return row("Conference", "TBA", "conference", tba=True)
    end = end or start
    conf_for_end = dict(conf, conference_end=end)
    return row("Conference", esc(date_range(start, end)) + unconfirmed_tag(conf, "conference_start", "conference_end"),
               "conference",
               end_ms=ms(moment_utc(conf_for_end, "conference_end")),
               start_ms=ms(moment_utc(conf, "conference_start")))


def card(conf):
    areas = conf["areas"]
    chips = "".join(f'<span class="chip a-{slug(a)}">{esc(a)}</span>' for a in areas)
    accent = f"a-{slug(areas[0])}" if areas else ""

    # The list of future milestones the card's countdown walks through.
    milestones = [{"l": ev["label"], "t": ms(moment_utc(conf, ev["kind"])),
                   "d": ev["is_deadline"]} for ev in events(conf)]
    conf_end = ""
    if conf.get("conference_start"):
        end = conf.get("conference_end") or conf["conference_start"]
        conf_end = ms(moment_utc(dict(conf, conference_end=end), "conference_end"))

    rows = "".join([
        simple_row(conf, "abstract", "Abstract", always=False),
        simple_row(conf, "submission", conf.get("submission_label", "Submission").replace(" deadline", "")),
        simple_row(conf, "commitment", "Commitment", always=False),
        rebuttal_row(conf),
        simple_row(conf, "decision", "Decision"),
        conference_row(conf),
    ])

    name = f'<a href="/c/{esc(conf["id"])}">{esc(conf["name"])}</a>'
    meta = []
    if conf.get("location"):
        meta.append(esc(conf["location"]))
    time_note = conf.get("time_note")
    notes = ""
    if time_note:
        notes += f'<p class="note"><strong>Deadline time:</strong> {esc(time_note)}</p>'
    for field, other in conf["conflicts"].items():
        if field == "conference_end":
            continue
        label = event_label(conf, field) if field in EVENT_TYPES else field
        notes += (f'<p class="note warn"><strong>Check this:</strong> the community tracker lists '
                  f'{esc(nice_date(other))} for the {esc(label[0].lower() + label[1:])}.</p>')
    if conf.get("note"):
        notes += f'<p class="note">{esc(conf["note"])}</p>'
    site_link = f'<a class="follow" href="/subscribe?c={esc(series(conf))}">Get reminders</a>'
    if conf.get("url"):
        site_link += (f'<a class="official" href="{esc(conf["url"])}" target="_blank" '
                      f'rel="noopener">Official site &rarr;</a>')

    return f"""
<article class="card {accent}" data-areas="{esc('|'.join(areas))}"
         data-milestones='{esc(json.dumps(milestones), quote=True)}' data-conf-end="{conf_end}">
  <header>
    <div class="titleline"><h2>{name}</h2><div class="chips">{chips}</div></div>
    <p class="full">{esc(conf.get('full_name', ''))}</p>
    <p class="where">{' &middot; '.join(meta)}</p>
  </header>
  <div class="next"><span class="next-label">&nbsp;</span><span class="next-count">&nbsp;</span></div>
  <ul class="rows">{rows}</ul>
  {notes}
  <div class="cardlinks">{site_link}</div>
</article>"""


# ---------------------------------------------------------------------------
# The whole page
# ---------------------------------------------------------------------------

def sort_key(conf, now_ms):
    """Cards with the soonest upcoming date first, then TBA ones, then finished."""
    upcoming = [ms(moment_utc(conf, ev["kind"])) for ev in events(conf)]
    upcoming = [t for t in upcoming if t > now_ms]
    if upcoming:
        return (0, min(upcoming), conf["name"])
    end = conf.get("conference_end") or conf.get("conference_start")
    if end is None or ms(moment_utc(dict(conf, conference_end=end), "conference_end")) > now_ms:
        return (1, 0, conf["name"])
    return (2, 0, conf["name"])


def next_up(conferences, now_ms, limit=6):
    items = []
    for conf in conferences:
        for ev in events(conf):
            t = ms(moment_utc(conf, ev["kind"]))
            if t > now_ms:
                items.append((t, conf, ev))
    items.sort(key=lambda x: (x[0], x[1]["name"]))
    out = []
    for t, conf, ev in items[:limit + 6]:  # a few spares; the page hides passed ones
        accent = f"a-{slug(conf['areas'][0])}" if conf["areas"] else ""
        out.append(
            f'<li class="{accent}" data-t="{t}" data-d="{1 if ev["is_deadline"] else 0}" '
            f'data-areas="{esc("|".join(conf["areas"]))}">'
            f'<span class="nu-date">{ev["date"]:%b} {ev["date"].day}</span>'
            f'<span class="nu-what"><a href="/c/{esc(conf["id"])}"><strong>{esc(conf["name"])}</strong></a> {esc(ev["label"])}</span>'
            f'<span class="nu-count"></span></li>')
    return "".join(out)


# ---------------------------------------------------------------------------
# Home page: the searchable list and the timeline
# ---------------------------------------------------------------------------

STAR = ('<svg aria-hidden="true" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" '
        'stroke-width="1.8" stroke-linejoin="round"><path d="M12 3.5l2.6 5.4 5.9.8-4.3 4.1 1 5.8L12 16.9 '
        '6.8 19.6l1-5.8-4.3-4.1 5.9-.8z"/></svg>')


def star_button(conf):
    """The favourite toggle. The script in site.js makes it work."""
    return (f'<button class="star" type="button" aria-pressed="false" data-series="{esc(series(conf))}" '
            f'aria-label="Favorite {esc(conf["name"])}" title="Add to favorites">{STAR}</button>')


def search_text(conf):
    """Everything the search box should match for one conference, in lower case."""
    parts = [conf["name"], conf.get("full_name", ""), conf.get("location", ""), series(conf)] + conf["areas"]
    return esc(" ".join(parts).lower())


def _shared_attrs(conf):
    return (f'data-series="{esc(series(conf))}" data-areas="{esc("|".join(conf["areas"]))}" '
            f'data-search="{search_text(conf)}"')


def list_row(conf):
    """One conference as one line of the home page list."""
    accent = f"a-{slug(conf['areas'][0])}" if conf["areas"] else ""
    milestones = [{"l": ev["label"], "t": ms(moment_utc(conf, ev["kind"])), "d": ev["is_deadline"],
                   "w": nice_date(ev["date"]),
                   "g": "auto-updated" if ev["auto"] else ("unconfirmed" if ev["unverified"] else "")}
                  for ev in events(conf)]
    conf_end = ""
    if conf.get("conference_start"):
        end = conf.get("conference_end") or conf["conference_start"]
        conf_end = ms(moment_utc(dict(conf, conference_end=end), "conference_end"))
    areas = "".join(f'<span class="area a-{slug(a)}">{esc(a)}</span>' for a in conf["areas"])
    return f"""
<li class="conf {accent}" {_shared_attrs(conf)} data-conf-end="{conf_end}"
    data-milestones='{esc(json.dumps(milestones), quote=True)}'>
  {star_button(conf)}
  <div class="who">
    <div class="nameline"><a class="name" href="/c/{esc(conf["id"])}">{esc(conf["name"])}</a>{areas}</div>
    <div class="sub">{esc(conf.get("location", ""))}</div>
  </div>
  <div class="nextdate"><div class="what">&nbsp;</div><div class="when sub">&nbsp;</div></div>
  <div class="left">&nbsp;</div>
</li>"""


def timeline(conferences, now_ms):
    """Every upcoming date of every conference, soonest first, grouped by month."""
    items = []
    for conf in conferences:
        for ev in events(conf):
            t = ms(moment_utc(conf, ev["kind"]))
            if t > now_ms:
                items.append((t, conf, ev))
    items.sort(key=lambda x: (x[0], x[1]["name"]))

    out, current = [], None
    for t, conf, ev in items:
        month = f"{ev['date']:%B %Y}"
        if month != current:
            if current is not None:
                out.append("</ol></section>")
            out.append(f'<section class="month"><h2>{esc(month)}</h2><ol class="tl-list">')
            current = month
        accent = f"a-{slug(conf['areas'][0])}" if conf["areas"] else ""
        areas = "".join(f'<span class="area a-{slug(a)}">{esc(a)}</span>' for a in conf["areas"])
        tag = ""
        if ev["auto"]:
            tag = ' <span class="tag">auto-updated</span>'
        elif ev["unverified"]:
            tag = ' <span class="tag">unconfirmed</span>'
        out.append(f"""
<li class="tl {accent}" {_shared_attrs(conf)} data-t="{t}" data-d="{1 if ev['is_deadline'] else 0}">
  <div class="tl-date"><span class="day">{ev['date'].day}</span><span class="dow">{ev['date']:%a}</span></div>
  <div class="who">
    <div class="nameline"><a class="name" href="/c/{esc(conf["id"])}">{esc(conf["name"])}</a>{areas}</div>
    <div class="sub">{esc(ev["label"])}{tag}</div>
  </div>
  <div class="left">&nbsp;</div>
  {star_button(conf)}
</li>""")
    if current is not None:
        out.append("</ol></section>")
    return "".join(out)


# ---------------------------------------------------------------------------
# Calendar feed (.ics)
# ---------------------------------------------------------------------------

def ics_text(value):
    return (value.replace("\\", "\\\\").replace(";", "\\;")
                 .replace(",", "\\,").replace("\n", "\\n"))


def fold(line):
    """Calendar files must not have lines longer than 75 bytes."""
    out, current = [], ""
    for ch in line:
        if len((current + ch).encode("utf-8")) > 73:
            out.append(current)
            current = " " + ch
        else:
            current += ch
    out.append(current)
    return "\r\n".join(out)


def build_ics(settings, conferences, today):
    stamp = f"{today:%Y%m%d}T000000Z"
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//conference-reminders//EN",
             "CALSCALE:GREGORIAN", "METHOD:PUBLISH",
             "X-WR-CALNAME:" + ics_text(settings.get("site_title", "Conference Deadlines"))]
    for conf in conferences:
        for ev in events(conf):
            start = ev["date"]
            end = start + timedelta(days=1)
            title = f"{conf['name']}: {ev['label']}"
            if ev["kind"] == "conference_start":
                title = conf["name"]
                end = (conf.get("conference_end") or start) + timedelta(days=1)
            details = []
            if ev["is_deadline"]:
                details.append(conf.get("time_note") or "End of day, Anywhere on Earth")
            if ev["auto"]:
                details.append("Auto-updated from a tracker - check the official site")
            elif ev["unverified"]:
                details.append("Unconfirmed date - check the official site")
            if conf.get("url"):
                details.append(conf["url"])
            lines += ["BEGIN:VEVENT",
                      f"UID:{conf['id']}-{ev['kind']}@conference-reminders",
                      f"DTSTAMP:{stamp}",
                      f"DTSTART;VALUE=DATE:{start:%Y%m%d}",
                      f"DTEND;VALUE=DATE:{end:%Y%m%d}",
                      "SUMMARY:" + ics_text(title)]
            if ev["kind"] == "conference_start" and conf.get("location"):
                lines.append("LOCATION:" + ics_text(conf["location"]))
            if details:
                lines.append("DESCRIPTION:" + ics_text("\n".join(details)))
            lines += ["TRANSP:TRANSPARENT", "END:VEVENT"]
    lines.append("END:VCALENDAR")
    return "\r\n".join(fold(l) for l in lines) + "\r\n"

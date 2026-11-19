"""
build_site.py - turns conferences.toml into the website.

It writes two files into the docs/ folder:
    docs/index.html      the web page (GitHub Pages serves this)
    docs/deadlines.ics   a calendar feed people can subscribe to

Run it any time you change conferences.toml:

    python3 build_site.py

Then open docs/index.html in your browser to see the result.
You should not need to edit this file unless you want to change the design.
"""

import argparse
import html
import json
import re
from datetime import date, timedelta

import confdata
from confdata import (EVENT_TYPES, ROOT, date_range, days_phrase, event_label,
                      events, moment_utc, nice_date)

OUT_DIR = ROOT / "docs"
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

    name = esc(conf["name"])
    if conf.get("url"):
        name = f'<a href="{esc(conf["url"])}" target="_blank" rel="noopener">{name}</a>'
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
    site_link = ""
    if conf.get("url"):
        site_link = (f'<a class="official" href="{esc(conf["url"])}" target="_blank" '
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
  {site_link}
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
            f'<span class="nu-what"><strong>{esc(conf["name"])}</strong> {esc(ev["label"])}</span>'
            f'<span class="nu-count"></span></li>')
    return "".join(out)


def build_html(settings, conferences, today):
    from datetime import datetime, timezone
    now_ms = ms(datetime.now(timezone.utc)) if today == confdata.today_aoe() else \
        ms(datetime(today.year, today.month, today.day, 12, tzinfo=timezone.utc))

    ordered = sorted(conferences, key=lambda c: sort_key(c, now_ms))
    all_areas = []
    for c in conferences:
        for a in c["areas"]:
            if a not in all_areas:
                all_areas.append(a)
    filters = '<button class="filter on" data-area="">All</button>' + "".join(
        f'<button class="filter a-{slug(a)}" data-area="{esc(a)}">{esc(a)}</button>'
        for a in all_areas)

    signup = ""
    if settings.get("signup_url"):
        signup = (f'<a class="btn primary" href="{esc(settings["signup_url"])}" '
                  f'target="_blank" rel="noopener">Get email reminders</a>')

    page = PAGE
    replacements = {
        "%%TITLE%%": esc(settings.get("site_title", "Conference Deadlines")),
        "%%TAGLINE%%": esc(settings.get("site_tagline", "")),
        "%%COUNT%%": str(len(conferences)),
        "%%CHECKED%%": esc(nice_date(today)),
        "%%VERIFIED%%": esc(str(settings.get("dates_verified", "not recorded"))),
        "%%SYNCED%%": esc(confdata.read_tracker_state().get("last_sync", "not yet run")),
        "%%FILTERS%%": filters,
        "%%SIGNUP%%": signup,
        "%%NEXTUP%%": next_up(conferences, now_ms),
        "%%CARDS%%": "".join(card(c) for c in ordered),
    }
    for token, value in replacements.items():
        page = page.replace(token, value)
    return page


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


# ---------------------------------------------------------------------------
# Page template. %%TOKENS%% are filled in by build_html above.
# ---------------------------------------------------------------------------

PAGE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>%%TITLE%%</title>
<meta name="description" content="%%TAGLINE%%">
<style>
:root {
  --bg: #f6f4ef; --card: #fffdf8; --ink: #1d1b16; --muted: #6b665c; --line: #e4dfd3;
  --soft: #efebe2; --warn: #9a5b00; --urgent: #b3261e;
  --ml: #2f5fd0; --ai: #7a4fd1; --nlp: #0f7f66; --vision: #b8500f; --systems: #ab3358; --sci: #55750f;
}
@media (prefers-color-scheme: dark) {
  :root {
    --bg: #14130f; --card: #1d1c17; --ink: #ece8dd; --muted: #a39d8f; --line: #34322a;
    --soft: #26241e; --warn: #e2a84b; --urgent: #ff8a80;
    --ml: #8fb0ff; --ai: #c0a2ff; --nlp: #5fd3b6; --vision: #ffa66b; --systems: #ff8fb0; --sci: #b5d66a;
  }
}
* { box-sizing: border-box; }
html { -webkit-text-size-adjust: 100%; }
body { margin: 0; background: var(--bg); color: var(--ink);
  font: 16px/1.5 system-ui, -apple-system, "Segoe UI", Roboto, Helvetica, Arial, sans-serif; }
a { color: inherit; }
.wrap { max-width: 1120px; margin: 0 auto; padding: 0 20px; }

.a-ml { --accent: var(--ml); } .a-ai { --accent: var(--ai); } .a-nlp { --accent: var(--nlp); }
.a-vision { --accent: var(--vision); } .a-systems { --accent: var(--systems); }
.a-ai-for-science { --accent: var(--sci); }

.top { padding: 44px 0 20px; }
.eyebrow { font-size: 13px; letter-spacing: .1em; text-transform: uppercase; color: var(--muted); margin: 0 0 8px; }
h1 { font-family: "Iowan Old Style", "Palatino Linotype", Palatino, Georgia, serif;
  font-size: clamp(32px, 6vw, 52px); line-height: 1.05; margin: 0 0 12px; font-weight: 600; letter-spacing: -.01em; text-wrap: balance; }
.tagline { font-size: 18px; color: var(--muted); max-width: 46em; margin: 0 0 18px; }
.actions { display: flex; flex-wrap: wrap; gap: 10px; }
.btn { display: inline-block; padding: 9px 16px; border-radius: 999px; border: 1px solid var(--line);
  background: var(--card); text-decoration: none; font-size: 15px; font-weight: 600; }
.btn.primary { background: var(--ink); color: var(--bg); border-color: var(--ink); }
.btn:hover { border-color: var(--ink); }

.nextup { background: var(--card); border: 1px solid var(--line); border-radius: 14px; padding: 16px 18px; margin: 14px 0 22px; }
.nextup h2 { font-size: 13px; letter-spacing: .1em; text-transform: uppercase; color: var(--muted); margin: 0 0 6px; font-weight: 600; }
.nextup ol { list-style: none; margin: 0; padding: 0; }
.nextup li { display: grid; grid-template-columns: 4.2em 1fr auto; gap: 12px; align-items: baseline;
  padding: 9px 0; border-top: 1px solid var(--line); }
.nextup li:first-child { border-top: 0; }
.nu-date { font-weight: 700; color: var(--accent, var(--ink)); font-variant-numeric: tabular-nums; }
.nu-what { min-width: 0; }
.nu-count { font: 600 14px/1.4 ui-monospace, "SF Mono", Menlo, Consolas, monospace; white-space: nowrap; color: var(--muted); }
.nu-count.soon { color: var(--urgent); }
.nextup .empty { color: var(--muted); padding: 6px 0; }

.filters { display: flex; flex-wrap: wrap; gap: 8px; margin: 0 0 18px; }
.filter { font: inherit; font-size: 14px; font-weight: 600; cursor: pointer; padding: 7px 14px; border-radius: 999px;
  border: 1px solid var(--line); background: transparent; color: var(--accent, var(--ink)); }
.filter.on { background: var(--accent, var(--ink)); color: var(--bg); border-color: var(--accent, var(--ink)); }
.filter:focus-visible, .btn:focus-visible, a:focus-visible { outline: 2px solid var(--ink); outline-offset: 2px; }

.grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(min(100%, 330px), 1fr)); gap: 16px; }
.card { background: var(--card); border: 1px solid var(--line); border-top: 4px solid var(--accent, var(--muted));
  border-radius: 14px; padding: 18px 18px 16px; display: flex; flex-direction: column; min-width: 0; }
.card.done { opacity: .62; }
.titleline { display: flex; align-items: flex-start; justify-content: space-between; gap: 10px; }
.card h2 { font-family: "Iowan Old Style", "Palatino Linotype", Palatino, Georgia, serif; font-size: 25px; line-height: 1.15; margin: 0; font-weight: 600; }
.card h2 a { text-decoration: none; }
.card h2 a:hover { text-decoration: underline; }
.chips { display: flex; flex-wrap: wrap; gap: 5px; justify-content: flex-end; padding-top: 4px; }
.chip { font-size: 11px; font-weight: 700; letter-spacing: .04em; text-transform: uppercase; white-space: nowrap;
  color: var(--accent, var(--muted)); border: 1px solid currentColor; border-radius: 999px; padding: 1px 8px; }
.full { margin: 4px 0 0; font-size: 14px; color: var(--muted); }
.where { margin: 2px 0 0; font-size: 14px; }
.next { margin: 14px 0 12px; padding: 10px 12px; background: var(--soft); border-radius: 10px; display: flex; flex-direction: column; }
.next-label { font-size: 12px; letter-spacing: .06em; text-transform: uppercase; color: var(--muted); font-weight: 600; }
.next-count { font: 700 21px/1.3 ui-monospace, "SF Mono", Menlo, Consolas, monospace; font-variant-numeric: tabular-nums; }
.next.soon .next-count { color: var(--urgent); }
.next.quiet .next-count { font: 600 16px/1.5 system-ui, -apple-system, "Segoe UI", sans-serif; color: var(--muted); }
.rows { list-style: none; margin: 0; padding: 0; }
.row { display: grid; grid-template-columns: 7.2em 1fr; column-gap: 10px; padding: 7px 0; border-top: 1px solid var(--line); font-size: 15px; }
.row .k { color: var(--muted); grid-row: 1 / span 2; }
.row .v { font-weight: 600; min-width: 0; }
.row .rel { grid-column: 2; font-size: 13px; color: var(--muted); }
.row.tba .v { font-weight: 400; color: var(--muted); }
.row.past .v { font-weight: 400; color: var(--muted); text-decoration: line-through; text-decoration-thickness: 1px; }
.row.live .rel { color: var(--accent, var(--ink)); font-weight: 700; }
.tag { font-size: 11px; font-weight: 700; letter-spacing: .03em; text-transform: uppercase; color: var(--warn);
  border: 1px solid currentColor; border-radius: 4px; padding: 0 5px; margin-left: 4px; white-space: nowrap; text-decoration: none; display: inline-block; }
.note { font-size: 13.5px; color: var(--muted); margin: 10px 0 0; }
.note.warn { color: var(--warn); }
.official { margin-top: auto; padding-top: 12px; font-size: 14px; font-weight: 600; text-decoration: none; color: var(--accent, var(--ink)); }
.official:hover { text-decoration: underline; }
[hidden] { display: none !important; }

footer { color: var(--muted); font-size: 13.5px; padding: 30px 0 50px; }
footer p { margin: 0 0 6px; max-width: 60em; }
@media (max-width: 520px) {
  .top { padding-top: 28px; }
  .nextup li { grid-template-columns: 3.8em 1fr; }
  .nu-count { grid-column: 2; }
}
</style>
</head>
<body>
<div class="wrap">
  <section class="top">
    <p class="eyebrow">%%COUNT%% conferences tracked</p>
    <h1>%%TITLE%%</h1>
    <p class="tagline">%%TAGLINE%%</p>
    <div class="actions">
      %%SIGNUP%%
      <a class="btn" href="deadlines.ics">Add to calendar (.ics)</a>
    </div>
  </section>

  <section class="nextup" aria-label="Next deadlines">
    <h2>Next up</h2>
    <ol id="nextup">%%NEXTUP%%</ol>
  </section>

  <nav class="filters" aria-label="Filter by area">%%FILTERS%%</nav>

  <main class="grid" id="grid">%%CARDS%%</main>

  <footer>
    <p>Deadlines are end of day <strong>Anywhere on Earth</strong> (UTC&minus;12) unless a card says otherwise. Countdowns use your device clock.</p>
    <p>Dates are synced every day from the community tracker <a href="https://ccfddl.com/">ccfddl</a>. Dates tagged <em>auto-updated</em> or <em>unconfirmed</em> came from a tracker and have not been checked by a person. Dates can move, so always confirm on the official conference site before you rely on one.</p>
    <p>Dates last verified by hand against official sites: %%VERIFIED%%. Last tracker sync: %%SYNCED%%. Page refreshed %%CHECKED%%.</p>
  </footer>
</div>

<script>
(function () {
  var DAY = 86400000;
  function pad(n) { return (n < 10 ? "0" : "") + n; }
  function clock(ms) {
    var s = Math.floor(ms / 1000), d = Math.floor(s / 86400);
    var h = Math.floor((s % 86400) / 3600), m = Math.floor((s % 3600) / 60);
    return d + "d " + pad(h) + "h " + pad(m) + "m " + pad(s % 60) + "s";
  }
  function rough(ms) {
    var d = Math.floor(ms / DAY);
    if (d >= 2) return "in " + d + " days";
    if (d === 1) return "tomorrow";
    var h = Math.floor(ms / 3600000);
    return h >= 1 ? "in " + h + " hour" + (h === 1 ? "" : "s") : "in under an hour";
  }

  var cards = [].slice.call(document.querySelectorAll(".card"));
  cards.forEach(function (c) {
    c._ms = JSON.parse(c.getAttribute("data-milestones") || "[]");
    c._end = Number(c.getAttribute("data-conf-end")) || 0;
    c._label = c.querySelector(".next-label");
    c._count = c.querySelector(".next-count");
    c._box = c.querySelector(".next");
    c._rows = [].slice.call(c.querySelectorAll(".row[data-ts]"));
  });
  var upItems = [].slice.call(document.querySelectorAll("#nextup li"));
  var area = "";

  function tick() {
    var now = Date.now();
    cards.forEach(function (c) {
      var next = null;
      for (var i = 0; i < c._ms.length; i++) { if (c._ms[i].t > now) { next = c._ms[i]; break; } }
      c._box.className = "next";
      c.classList.remove("done");
      if (next) {
        var left = next.t - now;
        c._label.textContent = "Next: " + next.l;
        c._count.textContent = next.d ? clock(left) : rough(left);
        if (left < 7 * DAY) c._box.classList.add("soon");
      } else if (c._end && now < c._end) {
        c._label.textContent = "Status"; c._count.textContent = "Happening now";
      } else if (c._end) {
        c._box.classList.add("quiet"); c.classList.add("done");
        c._label.textContent = "Status"; c._count.textContent = "Finished for this cycle";
      } else {
        c._box.classList.add("quiet");
        c._label.textContent = "Status"; c._count.textContent = "Dates not announced yet";
      }
      c._rows.forEach(function (r) {
        var end = Number(r.getAttribute("data-ts")), start = Number(r.getAttribute("data-start")) || 0;
        var kind = r.getAttribute("data-kind"), rel = r.querySelector(".rel"), text = "";
        r.classList.remove("past", "live");
        if (now >= end) { r.classList.add("past"); text = kind === "conference" ? "finished" : "passed"; }
        else if (start && now >= start) { r.classList.add("live"); text = kind === "conference" ? "happening now" : "open now, closes " + rough(end - now); }
        else if (start) { text = (kind === "conference" ? "" : "opens ") + rough(start - now); }
        else { text = rough(end - now); }
        rel.textContent = text;
      });
    });

    var shown = 0;
    upItems.forEach(function (li) {
      var t = Number(li.getAttribute("data-t"));
      var match = !area || (li.getAttribute("data-areas") || "").split("|").indexOf(area) >= 0;
      var show = t > now && match && shown < 6;
      li.hidden = !show;
      if (show) {
        shown++;
        var el = li.querySelector(".nu-count"), left = t - now;
        el.textContent = li.getAttribute("data-d") === "1" ? clock(left) : rough(left);
        el.className = "nu-count" + (left < 7 * DAY ? " soon" : "");
      }
    });
    var list = document.getElementById("nextup"), empty = list.querySelector(".empty");
    if (!shown && !empty) { empty = document.createElement("li"); empty.className = "empty"; empty.textContent = "Nothing with a confirmed date right now."; list.appendChild(empty); }
    if (empty) empty.hidden = shown > 0;
  }

  [].slice.call(document.querySelectorAll(".filter")).forEach(function (b) {
    b.addEventListener("click", function () {
      area = b.getAttribute("data-area");
      [].slice.call(document.querySelectorAll(".filter")).forEach(function (x) { x.classList.toggle("on", x === b); });
      cards.forEach(function (c) {
        c.hidden = !!area && (c.getAttribute("data-areas") || "").split("|").indexOf(area) < 0;
      });
      tick();
    });
  });

  tick();
  setInterval(tick, 1000);
})();
</script>
</body>
</html>
"""


def main():
    parser = argparse.ArgumentParser(description="Build the conference deadlines site.")
    parser.add_argument("--today", metavar="YYYY-MM-DD",
                        help="pretend today is this date (for testing)")
    args = parser.parse_args()

    try:
        settings, _, conferences = confdata.load()
    except confdata.DataError as e:
        confdata.fail(str(e))
    today = date.fromisoformat(args.today) if args.today else confdata.today_aoe()

    OUT_DIR.mkdir(exist_ok=True)
    (OUT_DIR / "index.html").write_text(build_html(settings, conferences, today), encoding="utf-8")
    with open(OUT_DIR / "deadlines.ics", "w", encoding="utf-8", newline="") as f:
        f.write(build_ics(settings, conferences, today))
    (OUT_DIR / ".nojekyll").write_text("", encoding="utf-8")

    dated = sum(1 for c in conferences if events(c))
    print(f"Built docs/index.html and docs/deadlines.ics "
          f"({len(conferences)} conferences, {dated} with at least one date).")


if __name__ == "__main__":
    main()

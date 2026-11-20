# Paper_DDL_Reminder: project context

Context for any Claude session working in this repository. Written on 2026-11-20
from the planning conversation with Cici. Update the "Current status" and
"Handoff notes" sections at the end of each session.

## What this project is

A conference-deadline site with email reminders for CS researchers (ML, NLP,
vision, systems, AI for science). It started as a tool for one research group
of about 10 to 20 people. The goal is now a public resource where anyone can
sign up, pick the conferences they follow, and get reminders only for those.

## Two versions live in this repository

### Version 1: static site and daily job (built, running)

- `conferences.toml`: all conference data, settings and reminder rules. The only hand-edited data file.
- `confdata.py`: shared loader used by every script.
- `sync_dates.py`: daily sync from the ccfddl community tracker. Auto-applied dates are stored in `state/tracker.json`, not in the TOML. A date typed into the TOML always wins.
- `build_site.py`: builds `docs/index.html` and `docs/deadlines.ics`.
- `send_reminders.py`: emails due reminders and date-change notices. Remembers what it sent in `state/sent_log.json`.
- `.github/workflows/daily.yml`: runs sync, build and send every day at 14:17 UTC and on pushes that touch the data or scripts. A bot commits `docs/` and `state/` back to `main`.
- Standard library only, except `sync_dates.py`, which needs `pyyaml`.

Do not break version 1 while building version 2. It keeps running until version 2 replaces it.

### Version 2: single web app (being built, as a learning project)

Goes in the `app/` folder. Agreed design:

- **Stack:** FastAPI, server-rendered HTML templates, SQLite first (PostgreSQL at deploy time), pytest. Environment managed with **uv** (`uv add`, `uv run`), on Windows.
- **No passwords.** People confirm their email, then manage favorites through a private link containing a random token.
- **Tables:** `users` (id, email, confirmed, manage_token, created_at), `conferences`, `milestones` (conference_id, kind, date, source), `follows` (user_id, conference_id), `sent_reminders` (user_id, milestone_id, nudge_days, sent_at).
- **Email:** a proper email service (not Gmail) once public, with confirmation and an unsubscribe link in every message.
- **Later:** an AWS version (Lambda, Cognito, CDN) after the single-app version works.

Stages:

| # | Stage | Result |
|---|---|---|
| 0 | Setup | Hello page running locally with uv and FastAPI |
| 1 | First routes | `/conferences` returns the real data via `confdata.load()` |
| 2 | Pages | List page and one page per conference |
| 3 | Database | The tables above, filled from `conferences.toml` |
| 4 | Sign-up and favorites | Form, private link, tick-boxes |
| 5 | Email | Confirmation emails and per-person reminders |
| 6 | Tests and deploy | Live on the internet with a domain |
| 7 | AWS version | Same app on Lambda, Cognito and a CDN |

## How to work with Cici on this

The point of version 2 is for her to learn backend engineering, so:

- **She writes the code.** Explain the idea, let her type it, then review. Do not write whole features for her unless she asks.
- **Intuition first**, with a small worked example before any formal definition.
- **Do not assume Python fluency.** Explain new syntax and new terms the first time they appear.
- **One step at a time.** Give the next step, wait for the result, then continue.
- **End each session with a short handoff note** in the section below: what works, what she can explain, what is still confusing, where to resume.
- When she asks for code to be delivered, give complete ready-to-run files, not patches.

## Current status (2026-11-20)

Version 1:
- Daily job is running and succeeding. All 22 conferences match the tracker.
- No emails are sent yet: the `SMTP_USER`, `SMTP_PASSWORD` and `SUBSCRIBERS` secrets are not set.
- The repository is public. Whether GitHub Pages is switched on (branch `main`, folder `/docs`) is unconfirmed. `site_url` in `conferences.toml` is still empty.
- A weekly check of official conference sites was discussed and is not set up.

Version 2:
- Stage 0 in progress. She is installing uv with WinGet on Windows.
- Next: `uv init --python 3.13`, `uv add "fastapi[standard]"`, create `app/main.py` with a hello route, run `uv run fastapi dev app/main.py`, then commit `pyproject.toml`, `uv.lock` and `app/main.py`.
- Open question: whether this replaces or follows her other practice project this month.

## Things to be careful about

- Never commit email addresses or passwords. Subscribers and SMTP details live in GitHub secrets. `subscribers.txt` is gitignored and for local testing only.
- The daily bot commits to `main`, so pull before pushing.
- `sync_dates.py` deliberately never switches submission rounds on its own, because the tracker lists NLP commitment deadlines as extra timeline entries.
- Dates shown to the public must stay traceable: keep the "auto-updated" and "unconfirmed" tags and the official-site links.

## Handoff notes

_Add a dated entry at the end of each session._

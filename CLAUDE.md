# Paper_DDL_Reminder: project context

Context for any Claude session working in this repository. Update the
"Current status" and "Handoff notes" sections at the end of each session.

## What this project is

A conference-deadline site with email reminders for CS researchers (ML, NLP,
vision, systems, AI for science). It started as a tool for one research group
of about 10 to 20 people. The goal is now a public resource where anyone can
sign up, pick the conferences they follow, and get reminders only for those.

## What is in this repository

One web app, PaperDDL, built on 2026-11-24 in a single session because a working
product was needed quickly. Cici asked for it to be built first and explained
to her afterwards. The earlier static version (GitHub Pages plus a daily
GitHub job) was removed with her agreement.

- **Stack:** Flask, server-rendered Jinja templates, SQLite on a persistent disk,
  pytest. Deployed on Render from `render.yaml`. Email through Resend.
  Environment managed with **uv** on Windows.
- **Why Flask and not FastAPI** (the original plan): the build environment had
  no FastAPI and no network to install it, so a FastAPI app could not have been
  tested before a public launch. Flask could. Porting to FastAPI later is a
  reasonable learning exercise; the logic in `db.py`, `reminders.py`,
  `emailer.py` and `sync_dates.py` does not depend on the web framework.
- **Conference data** stays in `conferences.toml`, not in the database. The
  database holds only what people create: `users`, `follows`, `sent`, `meta`.
- **No passwords.** A person confirms their email, then uses a private link
  containing a random token.
- **Follows** are either a whole area (`ML`) or a conference series (`icml`).
  A series is the conference id without its year, so follows carry over.
- **Daily task** runs from a thread inside the app at 14:17 UTC
  (`ENABLE_SCHEDULER=1`), with `.github/workflows/daily.yml` as a backup trigger.
- See `README.md` for the file map, settings and deploy steps.

Run locally: `uv sync`, then `uv run flask --app app.main run --debug`.
Run tests: `uv run pytest` (18 tests, all passing when this was written).

## How to work with Cici on this

She is learning backend engineering and wants to understand this code.

- **Walk her through what exists** before changing it. Start with `app/main.py`.
- **Intuition first**, with a small worked example before any formal definition.
- **Do not assume Python fluency.** Explain new syntax and terms the first time.
- **One step at a time.** Give the next step, wait for the result, then continue.
- When she asks for code, give complete ready-to-run files, not patches.
- **End each session with a short handoff note** in the section at the bottom.

## Current status (2026-11-25)

- **Live at https://paperddl.org** on Render (service `paperddl`, Starter plan,
  1 GB disk at `/var/data`). `www.paperddl.org` redirects to it.
- Domain registered at Namecheap. DNS there holds four Resend records, an A
  record for `@` and a CNAME for `www`.
- Email through Resend with `paperddl.org` verified. A real sign-up and
  confirmation worked, and the email landed in the inbox, not spam.
- The daily task was triggered through the GitHub workflow and succeeded,
  including the tracker sync from Render. The `APP_URL` and `TASK_TOKEN`
  repository secrets are set.
- Not yet seen on the live site: an actual reminder email, because nothing was
  due for the one subscriber's areas. The same code path is covered by tests.
- The Resend API key was created with Full access; switching it to Sending
  access is a worthwhile tightening.
- After launch: the AWS version, deeper SEO, and a weekly check of official
  conference sites were discussed and are not done.
- `uv.lock` was removed because dependencies changed; `uv sync` recreates it.

## Things to be careful about

- Never commit email addresses, API keys or the `data/` folder. Secrets live in
  Render's Environment page.
- Run the app with exactly one worker and one instance. The database is one
  file, and the daily clock must run once.
- A confirmed subscriber's choices must never be changeable from the public
  sign-up form, only through their private link.
- `sync_dates.py` deliberately never switches submission rounds on its own,
  because the tracker lists NLP commitment deadlines as extra timeline entries.
- Dates shown to the public must stay traceable: keep the "auto-updated" and
  "unconfirmed" tags and the official-site links.
- Resend's free plan allows 100 emails a day. `DAILY_EMAIL_CAP` guards this.

## Handoff notes

_Add a dated entry at the end of each session._

### 2026-11-25

- **Works:** the public site, sign-up, confirmation, manage page, unsubscribe,
  the daily task and the tracker sync, all on the live site.
- **Cici set up herself:** the domain, the DNS records, Resend, the Render
  Blueprint, the custom domain and the GitHub secrets.
- **Not yet explained to her:** the code. She asked for it to be built first
  and walked through later. Start with `app/main.py`, then `db.py`.
- **Resume at:** watching the first automatic daily run (14:17 UTC), then the
  code walkthrough, then deciding on SEO work and the weekly official-site check.

# PaperDDL

A website that lists deadlines for major computer science conferences and
emails each subscriber before the dates of the conferences they follow.

- People sign up with an email address and tick areas or individual conferences.
- They confirm through a link sent to that address. There are no passwords.
- Every email carries a private link to change choices or unsubscribe.
- Once a day the app syncs dates from the [ccfddl](https://github.com/ccfddl/ccf-deadlines)
  community tracker and sends each person one email with whatever is due.

## What is in this folder

| Path | What it is |
|---|---|
| `conferences.toml` | **The conference data.** Dates, locations, reminder rules. The file you edit. |
| `app/main.py` | The web app: every address the site answers. Start reading here. |
| `app/confdata.py` | Reads `conferences.toml` and answers questions about dates. |
| `app/cards.py` | Builds the conference cards and the calendar feed. |
| `app/db.py` | The database: subscribers, what they follow, what they were sent. |
| `app/emailer.py` | Writes the emails and sends them through Resend. |
| `app/reminders.py` | Decides who gets which reminder; runs the daily task. |
| `app/sync_dates.py` | Pulls new and moved dates from the tracker. |
| `app/config.py` | Every setting the app reads from its environment. |
| `app/templates/`, `app/static/` | The pages, styles and countdown script. |
| `tests/test_app.py` | Automated checks of the whole flow. |
| `render.yaml` | Tells Render how to run the app. |
| `state/tracker.json` | A starting copy of the sync's memory. |
| `.github/workflows/daily.yml` | A backup trigger for the daily task. |

## Run it on your own computer

You need [uv](https://docs.astral.sh/uv/). From this folder:

```
uv sync
uv run flask --app app.main run --debug
```

Open http://127.0.0.1:5000. In this local mode no email is sent: each email is
printed in the terminal instead, including the confirmation link, so you can
try the whole sign-up flow. The database is created in a local `data/` folder.

Run the automated tests:

```
uv run pytest
```

## Put it online

You need three accounts: a domain registrar, [Resend](https://resend.com) for
email, and [Render](https://render.com) for hosting.

### 1. Domain
Buy the domain (for example `paperddl.com`) at any registrar.

### 2. Resend (email)
1. Create an account, go to **Domains**, and add your domain.
2. Resend shows a few DNS records. Add each one in your registrar's DNS settings,
   exactly as shown, then press **Verify** in Resend. This can take minutes to hours.
3. Go to **API Keys**, create a key, and copy it.

### 3. Render (hosting)
1. Create an account and add a payment method.
2. Choose **New > Blueprint** and connect this GitHub repository. Render reads `render.yaml`.
3. It asks for three values:
   - `RESEND_API_KEY`: the key from Resend.
   - `CONTACT_EMAIL`: an address you read. It is shown on the About and Privacy pages.
   - `BASE_URL`: leave empty for now.
4. Apply. After a few minutes the app is live at an address ending in `onrender.com`.

### 4. Connect the domain
1. In Render open the service, then **Settings > Custom Domains**, and add your domain.
2. Add the DNS records Render shows at your registrar.
3. When Render shows the domain as verified, set `BASE_URL` to `https://yourdomain`
   under **Environment** and save. The links inside emails now use your domain.

If your domain is not `paperddl.com`, also change `MAIL_FROM` under **Environment**
to an address at your domain, for example `PaperDDL <reminders@yourdomain>`.

### 5. Check it
1. Open the site and subscribe with your own address.
2. Click the link in the confirmation email. You should land on your manage page.
3. Optional: add the repository secrets `APP_URL` and `TASK_TOKEN` (copy the token
   from Render's Environment page), then run **Actions > Daily task > Run workflow**
   to trigger the daily task by hand and see its summary in the log.

## Settings

All set in Render under **Environment**.

| Name | Meaning |
|---|---|
| `RESEND_API_KEY` | Resend key. Without it, emails are printed, not sent. |
| `MAIL_FROM` | Sender shown on emails. Its domain must be verified in Resend. |
| `BASE_URL` | The site's public address, used for links in emails. |
| `CONTACT_EMAIL` | Shown on the About and Privacy pages. |
| `TASK_TOKEN` | Password for the `/tasks/...` addresses. Render generates it. |
| `DATA_DIR` | Folder for the database. On Render, the persistent disk. |
| `ENABLE_SCHEDULER` | `1` makes the app run the daily task by itself. |
| `DAILY_EMAIL_CAP` | Stop sending for the day at this many emails. Default 95, because Resend's free plan allows 100 a day. Raise it if you move to a paid plan. |

## The daily task

Runs at 14:17 UTC from a clock inside the app. It:

1. syncs dates from the tracker,
2. emails each confirmed subscriber the reminders for what they follow,
3. deletes sign-ups that were never confirmed after 7 days.

Reminder timing lives in the `[reminders]` section of `conferences.toml`. Each
number is "days before". Nobody is sent the same reminder twice, and if a day's
run is missed, the reminder goes out on the next run with the true number of
days left.

If the daily email cap is reached, the remaining people get their email on the
next run.

To see subscriber counts and the last run's summary:

```
curl -H "Authorization: Bearer YOUR_TASK_TOKEN" https://yourdomain/tasks/stats
```

## Editing conference dates

Edit `conferences.toml` on GitHub and commit. Render redeploys the site by itself.

- A date that is not announced yet is left out; the site shows "TBA".
- A date you could not confirm on the official site goes in that conference's
  `unverified` list and is shown with a tag.
- The tracker sync fills in and updates dates automatically with an
  "auto-updated" tag. Whatever you type into the file always wins.
- To add a conference, copy an existing `[[conference]]` block and give it a new `id`
  ending in the year, such as `icml-2028`. People who follow `icml` get it automatically.

## Things to know

- **One instance only.** The database is a single file on one disk, so the app
  must not be scaled to several instances.
- **Back up the disk.** Render can take disk snapshots. Subscribers exist only there.
- **Email rules.** Sign-ups are confirmed by email and every reminder has a
  one-click unsubscribe, in line with common anti-spam rules. Check the rules
  that apply to you before sending at scale.

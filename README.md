# Conference deadline reminders

A small website that lists the big 2027 conferences (ML, NLP, vision, systems,
AI for science) with live countdowns, plus a daily job that emails a short
reminder to a list of people when a date is getting close.

Everything is plain Python and needs Python 3.11 or newer. The site and the
emails need **nothing installed**. Only the automatic date sync uses one extra
package (`pip install pyyaml`), and GitHub installs that for you.

## What is in this folder

| File | What it is |
|---|---|
| `conferences.toml` | **The data.** Every conference and date, plus settings. This is the file you edit. |
| `build_site.py` | Turns the data into the web page (`docs/index.html`) and a calendar feed (`docs/deadlines.ics`). |
| `sync_dates.py` | Pulls new and moved dates from a community tracker every day, so the site updates itself. |
| `send_reminders.py` | Works out what is due today and sends the emails. |
| `confdata.py` | Shared helper code used by both scripts. |
| `.github/workflows/daily.yml` | Tells GitHub to run both scripts every day, for free. |
| `state/sent_log.json` | The script's memory of which reminders already went out. Leave it alone. |
| `state/tracker.json` | Created by the sync: the dates it updated automatically. Leave it alone. |
| `subscribers.example.txt` | Shows the format for the list of people. |

## How it works, in one paragraph

Once a day GitHub runs `sync_dates.py` (picks up new and moved dates),
`build_site.py` (refreshes the page) and then `send_reminders.py`. The second script looks at every date in
`conferences.toml`, compares it with today, and checks the `[reminders]`
rules, for example "paper deadline: nudge 30, 7 and 1 days before".
Everything due that day is bundled into **one** email per person. If nothing
is due, nobody hears anything.

## Try it on your own computer first

Open a terminal in this folder and run:

```
python3 build_site.py
```

Then open `docs/index.html` in your browser. That is the website.

To see which emails would go out today, without sending anything:

```
python3 send_reminders.py --dry-run
```

To check a different day, add a date:

```
python3 send_reminders.py --dry-run --today 2027-01-15
```

## Put it online (about 20 minutes, free)

### 1. Create the repository
1. Sign in at github.com and click **New repository**.
2. Give it a name (for example `conference-deadlines`), choose **Public**, and create it.
   It has to be public for the free website hosting. Email addresses and
   passwords are stored separately as secrets and are never visible.
3. On the new repository page choose **uploading an existing file** and drag in
   everything from this folder, including the `.github` folder. (`.github` is a
   hidden folder. On a Mac press Cmd+Shift+. in Finder to see it; on Windows turn
   on "Hidden items" in File Explorer's View menu.)

### 2. Turn on the website
1. In the repository go to **Settings → Pages**.
2. Under "Build and deployment" set Source to **Deploy from a branch**,
   branch **main**, folder **/docs**, then Save.
3. After a minute the page shows your site address, something like
   `https://yourname.github.io/conference-deadlines/`.
4. Open `conferences.toml` on GitHub (pencil icon to edit) and paste that
   address into `site_url = ""`, so the emails can link to the site.

### 3. Set up the sending email account
The simplest route is a Gmail account (a new one made just for this is a good idea).
1. Turn on 2-Step Verification for that Google account.
2. Go to https://myaccount.google.com/apppasswords and create an app password.
   You get 16 letters. That is what the script logs in with, not your real password.

### 4. Add the secrets
In the repository go to **Settings → Secrets and variables → Actions → New repository secret**
and add these three:

| Name | Value |
|---|---|
| `SMTP_USER` | the Gmail address, e.g. `ourlab.reminders@gmail.com` |
| `SMTP_PASSWORD` | the 16-letter app password |
| `SUBSCRIBERS` | the people to email, one address per line |

The `SUBSCRIBERS` secret can limit someone to certain areas:

```
alex@example.com
sam@example.com | NLP, Systems
```

A line with no `|` gets everything. To add or remove someone later, edit this secret.

(Not using Gmail? Also add `SMTP_HOST` and `SMTP_PORT` for your provider.)

### 5. Test it
1. Go to the **Actions** tab. If GitHub asks, enable workflows.
2. Pick **Daily update → Run workflow**.
3. Click the run to see the log. It lists what was due and who was emailed
   (addresses are partly hidden in the log).

From now on it runs by itself every day at about 6am Pacific.

## How the dates update themselves

Every day `sync_dates.py` downloads the community tracker
[ccfddl](https://github.com/ccfddl/ccf-deadlines), which volunteers update
when a conference announces or moves a date, and compares it with this project.

| What the sync finds | What happens |
|---|---|
| A date we show as TBA is now on the tracker | Filled in automatically |
| The tracker changed a date since the last run | Updated automatically |
| The tracker disagrees with a date a person typed in, and has not moved | The typed date stays, and the card shows a "Check this" note |
| The tracker says TBD, or does not list the conference | Nothing changes |

Automatic updates go live straight away, with an **auto-updated** tag so
everyone can see that no person has checked them yet. Subscribers get a
"Date changes" box in their next email (sent that day even if no reminder is due).

Things to know:

- Automatic dates are stored in `state/tracker.json`, not in `conferences.toml`.
- **Whatever a person types into `conferences.toml` always wins.** To confirm
  an auto-updated date, type it in; the tag disappears.
- The tracker mainly covers abstract and paper deadlines and conference dates.
  Rebuttal and decision dates are often missing there, so those still need a
  person (or the official site) from time to time.
- The tracker can lag the official announcement by days or weeks, and it is
  occasionally wrong. That is why auto-updated dates carry a tag.
- The run log on the Actions tab lists everything the sync did, including any
  conference it could not find on the tracker. If one is "not found", check
  the `tracker = [...]` name for that conference in `conferences.toml`.
- If the tracker is unreachable one day, the sync is skipped and everything
  else still runs.

To try it on your own computer (needs `pip install pyyaml` once):

```
python3 sync_dates.py --dry-run
```

## Editing dates by hand

The sync does most of the work, but it is still worth looking over
`conferences.toml` about once a month, mainly for rebuttal and decision dates.

- Edit the file directly on GitHub (pencil icon) and commit. The site rebuilds
  by itself within a minute or two.
- A date that is not announced yet is simply left out. The site shows "TBA".
- If you could not confirm a date on the official site, list it under
  `unverified`. It is shown with an "unconfirmed" tag, on the site and in emails.
- When you re-check everything, update `dates_verified` in `[settings]`.
- If you change a date, people are reminded again on the new schedule.

To add a conference, copy an existing `[[conference]]` block, give it a new
`id`, and change the details. If you make a typo, the script stops with a
message saying which conference and which field.

### Changing how often people are nudged

Edit the `[reminders]` section of `conferences.toml`. Each number is "days
before". For example, for more paper-deadline emails:

```
submission = [30, 14, 7, 3, 1]
```

## Things worth knowing

- **Timezone.** Deadlines are treated as end of day "Anywhere on Earth"
  (UTC−12), which is what most ML conferences use. Where a conference uses a
  different time, the card and the email say so.
- **Missed runs.** If GitHub skips a day, the reminder goes out on the next
  run and states the real number of days left.
- **Gmail limits.** A free Gmail account can send roughly 500 emails a day,
  far more than 10–20 people need.
- **Spam folders.** Ask people to check spam the first time and mark the
  sender as safe.
- **Calendar.** The "Add to calendar" button on the site gives a `.ics` file.
  Subscribing to its web address in Google or Apple Calendar keeps the dates in sync.

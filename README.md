# Sicily property listing watcher

Checks a set of Immobiliare.it saved searches twice a day, figures out
what's new since last time, and emails you a prioritized summary. Runs
entirely on GitHub's free Actions minutes — no server needed.

## How it works

- `check_listings.py` fetches each URL in `SEARCHES`, parses out listings,
  compares them against `seen_listings.json`, and emails anything new.
- `.github/workflows/check_listings.yml` runs that script on a schedule and
  commits the updated `seen_listings.json` back to the repo so state
  persists between runs.

## Setup (about 10 minutes)

1. **Create a new GitHub repository.**
   It can be private — go to github.com → New repository → give it a name
   like `sicily-listing-watcher` → Create.

2. **Upload these four files**, keeping the folder structure intact:
   - `check_listings.py`
   - `seen_listings.json`
   - `.github/workflows/check_listings.yml`
   - `README.md` (this file)

   Easiest way: on the repo page, "Add file" → "Upload files", drag all of
   them in (GitHub will recreate the `.github/workflows/` folder from the
   path automatically if you drag the whole folder, or you can create the
   path manually via "Create new file" and paste the workflow content).

3. **Create a Gmail App Password** (assuming you'll send from a Gmail
   account — using your real password won't work if 2-Step Verification
   is on, which it should be):
   - Go to myaccount.google.com/security
   - Turn on 2-Step Verification if it isn't already on
   - Go to myaccount.google.com/apppasswords
   - Create an app password named "listing watcher", copy the 16-character
     code it gives you

4. **Add three repository secrets**
   (repo page → Settings → Secrets and variables → Actions → New repository
   secret):
   - `EMAIL_FROM` — the Gmail address you generated the app password for
   - `EMAIL_PASSWORD` — the 16-character app password from step 3
   - `EMAIL_TO` — where you want the digest sent (can be the same address)

5. **Test it manually before waiting for the schedule.**
   Go to the Actions tab → "Check Sicily Property Listings" → "Run workflow".
   Check the run's logs to see how many listings it parsed and whether the
   email sent successfully.

6. **Let it run.** It's scheduled for 07:00 and 17:00 UTC daily. Adjust the
   two `cron` lines in the workflow file if you want different times —
   cron format is `minute hour day month weekday`, always in UTC.

## Things worth knowing

- **First run will email you everything.** Every listing on those pages
  will look "new" the first time, since `seen_listings.json` starts empty.
  After that, you'll only hear about genuinely new listings.
- **Sites occasionally change their page structure**, which can break the
  parser silently (you'd just stop getting emails, or get empty ones).
  Check the Actions tab occasionally to confirm runs are succeeding.
- **Some sites may rate-limit or block automated requests** even with a
  realistic browser header, since GitHub's runners use recognizable
  datacenter IP ranges. If Immobiliare.it stops returning results, that's
  the most likely cause — there's no clean fix for this short of a paid
  proxy service, so it's worth checking early whether it works reliably
  for you.
- **Idealista.it isn't wired up yet.** It has a different page structure
  than Immobiliare, so it needs its own parser function. Once you've
  confirmed the Immobiliare version is working, that's the natural next
  addition — happy to write that one too.
- This is intended for personal use at a light frequency (twice a day,
  three search pages) — keep it that way rather than scaling it up, both
  out of courtesy to the sites and to stay under any rate limits.

## Adjusting what it watches

Edit the `SEARCHES` list near the top of `check_listings.py` — add or
remove search URLs, following the same `{"name": ..., "url": ...}` pattern.
Edit `MAX_BUDGET` and `GOOD_KEYWORDS` to change how listings get prioritized
in the email.

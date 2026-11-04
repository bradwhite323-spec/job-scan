# Job watch

Checks about 200 target companies' job boards every morning and posts new
Director and Head-level Solution Consulting, Product Support and Forward
Deployed Engineering roles to Slack.

## How it works

1. **Finds each company's job board.** It tries the public feeds of Greenhouse,
   Ashby, Lever, SmartRecruiters, Workable and Recruitee, using the board names
   in `companies.yaml`. The result is saved in `state/boards.json`. Companies
   not found are retried weekly.
2. **Reads every board** and keeps postings whose titles match `titles.yaml`.
3. **Compares with what it has seen before** (`state/seen.json`) and posts only
   new roles, plus a short note on roles that disappeared.
4. **Opens each new role** to add pay (from the posting's pay fields or its
   description) and sorts it by location, using `locations.yaml`:
   1. Remote (US) or Colorado
   2. San Francisco or New York
   3. Relocation (other US)
   4. Relocation (outside US)

   Remote roles get a warning when the posting limits hours to Pacific,
   Central or Eastern time, excludes Colorado, or lists allowed states
   without Colorado.
5. **Saves its state back to the repo**, so each run picks up where the last left off.

## Weekly sweep

Every Monday morning, a Claude scheduled task follows `SWEEP.md`. It searches
job boards for your titles at companies **not** in `companies.yaml`, opens
each posting to confirm it's real and open, and saves the verified roles to
`state/sweep_inbox.json`. Monday's daily digest then posts them in a separate
section, along with any company that had 2 or more matching roles, as a
suggestion to add to your list. To change what the sweep does, edit `SWEEP.md`.

The first run posts every matching role that's open today, along with the
companies it couldn't find a board for. After that you only get new postings.

## Setup (about 10 minutes)

1. **Create a Slack webhook.** Go to https://api.slack.com/apps, choose
   *Create New App → From scratch*, open *Incoming Webhooks*, turn it on,
   select *Add New Webhook to Workspace*, and pick the channel for alerts
   (a private `#job-watch` channel works well). Copy the webhook URL.
2. **Create a private GitHub repo** and add these files, including the hidden
   `.github/workflows/jobwatch.yml`. Uploading through the GitHub website can
   skip hidden folders, so GitHub Desktop or `git push` is more reliable.
3. **Add the webhook as a secret.** In the repo, go to *Settings → Secrets and
   variables → Actions → New repository secret*. Use the name
   `SLACK_WEBHOOK_URL` and paste the URL.
4. **Run it once by hand.** Open *Actions → Job watch → Run workflow*. The
   first run takes a few minutes while it finds every board. After that it
   runs daily at 6:43am Mountain time.

## Adjusting it

- **Companies:** edit `companies.yaml`. For a company on Workday, add its
  careers URL, for example
  `workday: https://acme.wd5.myworkdayjobs.com/External`. To pin a board that
  was found under the wrong name, add `board: {ats: greenhouse, slug: acme}`.
- **Titles:** edit the patterns in `titles.yaml`. Set `include_vp: true` to
  also catch VP roles.
- **Locations:** edit `locations.yaml` to add places or reorder tiers. Set
  `show_outside_us: false` to drop roles outside the US.
- **Quiet days:** by default you get a short "no new matches" message each day,
  which confirms the job ran. To skip those, add a repository variable
  `POST_WHEN_EMPTY` set to `false`.
- **Schedule:** change the `cron` line in `.github/workflows/jobwatch.yml`.
  The time is in UTC.
- **Re-find all boards:** run the workflow by hand with *Re-find every
  company's job board* ticked.

## Running locally

```
pip install -r requirements.txt
python watch.py run --dry-run    # prints the digest; posts and saves nothing
python watch.py coverage         # which board each company resolved to
python -m pytest tests           # tests
```

## Limits

- Companies on custom career sites (for example Netflix, Atlassian and
  Shopify) or Workday need a one-time setup. The Monday digest lists any
  that aren't covered yet.
- A board that errors is flagged in that day's digest. Its roles aren't
  marked closed until it reads cleanly again.

## Who you know there

Each role in the Slack digest can list your LinkedIn connections at that company, senior and
customer-facing people first. The data comes from LinkedIn's own export (Settings → Data privacy →
Get a copy of your data → Connections), not from scraping.

The export is personal data about other people and this repo is public, so it is never committed:
it lives in the `CONNECTIONS_B64` Actions secret, and the saved `state/latest.md` omits contacts.

To refresh (monthly is plenty):

    python connections.py pack Connections.csv [more parts...] | head -1 | gh secret set CONNECTIONS_B64

If a connection's company is spelled differently from your list (e.g. after a rebrand), add it to
that company's `aliases:` in `companies.yaml`.

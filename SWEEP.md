# Weekly wide sweep: instructions

You are running the weekly sweep for this repo. The daily scraper covers
every company in `companies.yaml`. Your job is to find matching roles at
companies **not** on that list, verify each one, and leave them in
`state/sweep_inbox.json`. The next daily run posts them to Slack.
You never post, message, or apply to anything yourself.

## 1. Read the repo

- `titles.yaml`: the title families and patterns. A role must be Director or
  Head level (not Associate, Assistant or Deputy Director) in Solution
  Consulting, Product Support, or Forward Deployed Engineering.
- `companies.yaml`: the companies to **exclude**.
- `locations.yaml`: location tiers. Preferred: remote US, or remote/hybrid in
  Colorado, San Francisco, or New York. Other US locations are relocation
  options and are still worth reporting.
- `state/seen.json`: entries with `"source": "sweep"` were already reported.
  Skip their URLs.

## 2. Search

Your shell can't reach job sites. Use WebSearch and WebFetch only.
Run 25 to 40 searches that pair the titles with the job platforms, for example:

- `site:job-boards.greenhouse.io "Director of Solutions Engineering"`
- `site:jobs.ashbyhq.com "Head of Solutions Engineering"`
- `site:jobs.lever.co "Director, Sales Engineering"`
- `site:myworkdayjobs.com "Director of Presales"`
- `site:job-boards.greenhouse.io "Head of Technical Support"`
- `site:jobs.ashbyhq.com "Director of Customer Support"`
- `"Director of Forward Deployed Engineering"`
- Also: Head of Presales, Director of Solution Consulting, Director of Value
  Engineering, Director of Product Support, Head of Support, Director of
  Support Engineering.

Favor results from the past two weeks. Stop when searches stop turning up
companies you haven't seen.

## 3. Verify each candidate

Open each candidate with WebFetch from a link that came back in a search or
page result. Include a role only when the fetched page shows the job title
and a way to apply. Take the title, location, workplace (remote, hybrid,
on-site) and pay from the page itself, never from a search snippet or the
URL. Skip anything you can't verify, and skip roles that say they're closed
or expired.

Note on the page, when present:
- a pay range (record it like `$180K–$240K`)
- remote roles limited to Pacific, Central or Eastern time hours
  (flag: `asks for PT/CT/ET hours`)
- remote roles that exclude Colorado (flag: `may exclude Colorado`) or list
  allowed states without Colorado (flag: `limited state list, Colorado not named`)

## 4. Write the inbox

Read `state/sweep_inbox.json` if it exists, keep any items still in it, and
add your verified roles. Format:

```json
{
  "updated": "YYYY-MM-DD",
  "items": [
    {
      "company": "Acme Robotics",
      "title": "Director of Solutions Engineering",
      "url": "https://job-boards.greenhouse.io/acme/jobs/123",
      "location": "Remote - US",
      "workplace": "Remote",
      "family": "Solution Consulting",
      "pay": "$190K–$240K",
      "flags": [],
      "found": "YYYY-MM-DD"
    }
  ],
  "suggest_add": ["Acme Robotics (2 roles)"]
}
```

`family` is one of `Solution Consulting`, `Product Support`, `Forward Deployed`.
Put a company in `suggest_add` when you verified 2 or more matching roles there.

Also append a short entry to `state/sweep_log.md`: the date, searches run,
candidates opened, roles verified, and anything that didn't work.

## 5. Save

Commit only `state/sweep_inbox.json` and `state/sweep_log.md` with the message
`Weekly sweep YYYY-MM-DD`, pull with rebase, and push to the default branch.
If the push fails, say so plainly in your final message and include the
verified roles there instead.

## Rules

- Never invent a posting, pay range, or location. If it wasn't on a page you
  fetched, it doesn't go in.
- Don't change any other file in the repo.
- Don't send messages, apply, or contact anyone.

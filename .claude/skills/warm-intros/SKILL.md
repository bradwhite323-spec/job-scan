---
name: warm-intros
description: Find warm paths into a target company on LinkedIn (first- and second-degree people near Support, CS/CX, Solutions and Talent leadership) and add them to the Warm Intros tracker page. Use when the user asks for warm intros, connections or referrals at a company, e.g. "warm intros for Gong".
---

# Warm intros

On-demand only. Run it for one to five shortlisted companies per session, never across the whole
list or on a schedule: a light, human-paced look at the user's own LinkedIn is fine, bulk crawling
is the pattern that gets accounts restricted.

## What you need

- The user's preferred browser (normally the built-in browser), signed in to LinkedIn. Load that
  browser's skill first. If LinkedIn shows a sign-in page, ask the user to sign in themselves and
  reply "done"; never enter their password. If the pane is hidden, tell them Cmd+Shift+B (Mac) or
  Ctrl+Shift+B (Windows) brings it back.
- If a page-opened popup is blocking navigation or scripts, ask the user to close it.
- `companies.yaml` in this repo, for the company's `linkedin_id` and `aliases`.

## Steps

1. **Find the company ID.** Use `linkedin_id` from `companies.yaml` if present. Otherwise open the
   company's LinkedIn page, click the "N connections work here" link (or "View all employees"),
   and read `currentCompany=["<id>"]` from the resulting search URL. Check the page is the right
   company (website, industry) before trusting it.

2. **First-degree.** Open
   `https://www.linkedin.com/search/results/people/?currentCompany=["<id>"]&network=["F"]`
   (URL-encode the brackets and quotes) and read it with get_page_text. Record name, headline,
   location and the mutual-connection count.

3. **Second-degree, one search per function.** LinkedIn ignores OR/AND in people search, so run
   separate searches with `network=["S"]` and `keywords=` set to each of:
   `support`, `solutions`, `customer success`, `director`.
   Read page 1 of each (10 results). Read page 2 (`&page=2`) only if page 1 was mostly relevant
   leaders. That is about five page loads per company; don't go beyond ten.

4. **Filter and rank.** Keep people at Director, Head, VP or C-level, or senior managers inside
   Support, CS/CX, Solutions/SE, Professional Services or Talent/Recruiting. Drop ICs unless they
   are a first-degree connection. Rank by how direct the path is first, seniority second:
   - First-degree people inside the target functions come first ("Ask first").
   - For second-degree people, list the one or two mutual connections who can introduce them,
     preferring mutuals who are first-degree at the same company.
   - Tag each person with a function: Support, CS & CX, Solutions, or Talent.

5. **Check for an open role.** Look in `state/seen.json` for open matches at this company. If one
   exists, put its title and link at the top of the brief; otherwise say no matching role is posted
   and the brief is groundwork.

6. **Update the tracker page.** Keep one artifact page, "Warm Intros", with a section per company,
   rather than a page per company. List the person's artifacts first and update the existing page
   by its URL; create it only if there is none. Each person gets: name (linked to a LinkedIn search
   for them), role, function tag, intro path, a "Show message" / "Copy message" button with a short
   draft, and a status the user can tap through: Not asked → Asked → Intro made.
   - Statuses and the people list live in the page's shared database (load the
     `artifact-capabilities` skill and use the `db` capability), not in browser storage, so they
     survive across devices and later runs. On a re-run, add new people and update roles, but never
     reset a status the user has set.
   - Drafts: first-degree asks are a 15-minute conversation about the team and who owns leadership
     hiring; second-degree asks go to the mutual connection, name the person and role, and offer a
     forwardable blurb. Use only facts about the user that they've told you (target roles:
     Director/Head of Product Support, Solutions Consulting, Forward Deployed Engineering). Never
     send a message, connection request or InMail yourself.

7. **Save the company ID.** If you looked up a new `linkedin_id`, add it to that company's entry in
   `companies.yaml` and commit it. Also add any company-name spellings you saw in profiles to
   `aliases`, so the daily digest's connection matching picks them up.

8. **Report in chat** in a few lines: who to ask first and why, the best two or three intro paths,
   and anything that blocked the run.

## Privacy

This repo is public. Never commit names, profiles, mutual connections or drafts; they belong only in
the private tracker page. The only LinkedIn data that goes in the repo is the company's numeric ID
and company-name aliases.

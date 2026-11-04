#!/usr/bin/env python3
"""Job watch: checks target companies' job boards daily and posts new
director-level matches to Slack.

Usage:
  python watch.py run [--dry-run] [--rediscover]   daily check
  python watch.py discover                          re-find every company's board
  python watch.py coverage                          print which companies are covered
"""
from __future__ import annotations

import argparse
import datetime as dt
import html
import json
import os
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

import requests
import yaml

ROOT = Path(__file__).resolve().parent
STATE_DIR = ROOT / "state"
BOARDS_FILE = STATE_DIR / "boards.json"
SEEN_FILE = STATE_DIR / "seen.json"
REPORT_FILE = STATE_DIR / "latest.md"
SWEEP_FILE = STATE_DIR / "sweep_inbox.json"
TZ = ZoneInfo(os.environ.get("JOBWATCH_TZ", "America/Denver"))
REDISCOVER_AFTER_DAYS = 7
WORKERS = 8
TIMEOUT = 20

_session = requests.Session()
_session.headers.update({
    "User-Agent": "job-watch/1.0 (personal job alert script; once daily)",
    "Accept": "application/json",
})


# ---------------------------------------------------------------- http

class NotFound(Exception):
    pass


def fetch_json(url: str, method: str = "GET", body: dict | None = None):
    """GET/POST JSON with one retry. Raises NotFound on 404."""
    for attempt in range(2):
        try:
            r = _session.request(method, url, json=body, timeout=TIMEOUT)
        except requests.RequestException:
            if attempt:
                raise
            time.sleep(2)
            continue
        if r.status_code == 404:
            raise NotFound(url)
        if r.status_code in (429, 500, 502, 503, 504) and not attempt:
            time.sleep(5)
            continue
        r.raise_for_status()
        return r.json()


def _job(ats, slug, jid, title, location, department, url, posted, **extra):
    """extra: details some feeds include in the listing (workplace, pay, description)."""
    return {
        "id": f"{ats}:{slug}:{jid}",
        "title": (title or "").strip(),
        "location": (location or "").strip(),
        "department": (department or "").strip(),
        "url": url or "",
        "posted": posted or "",
        "_src": {"ats": ats, "slug": slug, "rid": jid, **extra},
    }


# ---------------------------------------------------------------- platform readers
# Each returns (jobs, board_name_or_None). Raise NotFound if the board doesn't exist.

def read_greenhouse(slug):
    data = fetch_json(f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs")
    jobs = [
        _job("greenhouse", slug, j.get("id"), j.get("title"),
             (j.get("location") or {}).get("name"), "",
             j.get("absolute_url"), j.get("first_published") or j.get("updated_at"))
        for j in data.get("jobs", [])
    ]
    name = None
    try:
        name = fetch_json(f"https://boards-api.greenhouse.io/v1/boards/{slug}").get("name")
    except Exception:
        pass
    return jobs, name


def read_lever(slug):
    data = fetch_json(f"https://api.lever.co/v0/postings/{slug}?mode=json")
    if not isinstance(data, list):
        raise NotFound(slug)
    jobs = []
    for j in data:
        cat = j.get("categories") or {}
        created = j.get("createdAt")
        posted = (dt.datetime.fromtimestamp(created / 1000, dt.timezone.utc).isoformat()
                  if isinstance(created, (int, float)) else "")
        locs = cat.get("allLocations") or []
        location = "; ".join(locs) if len(locs) > 1 else cat.get("location")
        text = " ".join(filter(None, [j.get("descriptionPlain"), j.get("additionalPlain")]))
        jobs.append(_job("lever", slug, j.get("id"), j.get("text"), location,
                         cat.get("department") or cat.get("team"), j.get("hostedUrl"), posted,
                         workplace=j.get("workplaceType"), salary=j.get("salaryRange"),
                         text=text, has_detail=True))
    return jobs, None


def read_ashby(slug):
    data = fetch_json(f"https://api.ashbyhq.com/posting-api/job-board/{slug}?includeCompensation=true")
    if not isinstance(data, dict) or "jobs" not in data:
        raise NotFound(slug)
    jobs = []
    for j in data["jobs"]:
        if not j.get("isListed", True):
            continue
        locs = [j.get("location")] + [s.get("location") for s in j.get("secondaryLocations") or []]
        comp = j.get("compensation") or {}
        workplace = j.get("workplaceType") or ("Remote" if j.get("isRemote") else None)
        jobs.append(_job("ashby", slug, j.get("id"), j.get("title"), "; ".join(filter(None, locs)),
                         j.get("department") or j.get("team"), j.get("jobUrl"), j.get("publishedAt"),
                         workplace=workplace, text=j.get("descriptionPlain") or "",
                         pay_text=comp.get("scrapeableCompensationSalarySummary")
                         or comp.get("compensationTierSummary"), has_detail=True))
    return jobs, None


def read_smartrecruiters(slug):
    jobs, offset = [], 0
    while True:
        data = fetch_json(f"https://api.smartrecruiters.com/v1/companies/{slug}/postings"
                          f"?limit=100&offset={offset}")
        content = data.get("content") or []
        for j in content:
            loc = j.get("location") or {}
            where = ", ".join(x for x in (loc.get("city"), loc.get("region"), loc.get("country")) if x)
            if loc.get("remote"):
                where = f"Remote ({where})" if where else "Remote"
            jobs.append(_job("smartrecruiters", slug, j.get("id"), j.get("name"), where,
                             (j.get("department") or {}).get("label"),
                             f"https://jobs.smartrecruiters.com/{slug}/{j.get('id')}",
                             j.get("releasedDate")))
        offset += len(content)
        if not content or offset >= data.get("totalFound", 0) or offset >= 2000:
            break
    if not jobs:
        raise NotFound(slug)  # SmartRecruiters answers 200 even for unknown companies
    return jobs, None


def read_workable(slug):
    data = fetch_json(f"https://apply.workable.com/api/v1/widget/accounts/{slug}")
    jobs = []
    for j in data.get("jobs", []):
        where = ", ".join(x for x in (j.get("city"), j.get("state"), j.get("country")) if x)
        jobs.append(_job("workable", slug, j.get("shortcode"), j.get("title"), where,
                         j.get("department"), j.get("url"), j.get("published_on"),
                         workplace="Remote" if j.get("telecommuting") else None,
                         text=j.get("description") or "", has_detail=True))
    return jobs, data.get("name")


def read_recruitee(slug):
    data = fetch_json(f"https://{slug}.recruitee.com/api/offers/")
    jobs = [
        _job("recruitee", slug, j.get("id"), j.get("title"), j.get("location"),
             j.get("department"), j.get("careers_url"), j.get("published_at"),
             workplace="Remote" if j.get("remote") else None,
             text=" ".join(filter(None, [j.get("description"), j.get("requirements")])),
             has_detail=True)
        for j in data.get("offers", [])
    ]
    return jobs, None


def read_workday(careers_url):
    """careers_url like https://acme.wd5.myworkdayjobs.com/en-US/External"""
    u = urlparse(careers_url)
    host = u.netloc
    tenant = host.split(".")[0]
    parts = [p for p in u.path.split("/") if p and not re.fullmatch(r"[a-z]{2}-[A-Z]{2}", p)]
    if not parts:
        raise ValueError(f"Workday URL needs a site name: {careers_url}")
    site = parts[0]
    api = f"https://{host}/wday/cxs/{tenant}/{site}/jobs"
    jobs, seen = [], set()
    # Workday boards are large, so only pull postings that mention our leadership words.
    for query in ("director", "head"):
        offset, total = 0, None
        while total is None or offset < min(total, 500):
            data = fetch_json(api, "POST", {"appliedFacets": {}, "limit": 20,
                                            "offset": offset, "searchText": query})
            total = data.get("total", 0) if total is None else total
            postings = data.get("jobPostings") or []
            for j in postings:
                path = j.get("externalPath", "")
                if path in seen:
                    continue
                seen.add(path)
                jobs.append(_job("workday", tenant, path, j.get("title"), j.get("locationsText"),
                                 "", f"https://{host}/{site}{path}", j.get("postedOn"),
                                 detail_url=f"https://{host}/wday/cxs/{tenant}/{site}{path}"))
            offset += len(postings)
            if not postings:
                break
            time.sleep(0.5)
    return jobs, None


READERS = {
    "greenhouse": read_greenhouse,
    "ashby": read_ashby,
    "lever": read_lever,
    "smartrecruiters": read_smartrecruiters,
    "workable": read_workable,
    "recruitee": read_recruitee,
}


# ---------------------------------------------------------------- details, pay, location
# Run only for matched postings, so the extra requests stay small.

def html_to_text(s: str) -> str:
    s = html.unescape(html.unescape(s or ""))   # Greenhouse double-escapes
    s = re.sub(r"<(br|/p|/li|/div|/h\d)[^>]*>", "\n", s, flags=re.I)
    s = re.sub(r"<[^>]+>", " ", s)
    return re.sub(r"[ \t\xa0]+", " ", s).strip()


def fetch_detail(job: dict) -> None:
    """Fill job['_src'] with description text, workplace and pay where the platform has them."""
    src = job["_src"]
    if src.get("has_detail"):
        return
    ats, slug, rid = src["ats"], src["slug"], src["rid"]
    if ats == "greenhouse":
        d = fetch_json(f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs/{rid}?pay_transparency=true")
        src["text"] = html_to_text(d.get("content", ""))
        ranges = d.get("pay_input_ranges") or []
        if ranges:
            src["pay_ranges"] = [(r.get("min_cents", 0) / 100, r.get("max_cents", 0) / 100)
                                 for r in ranges if r.get("currency_type", "USD") == "USD"]
    elif ats == "smartrecruiters":
        d = fetch_json(f"https://api.smartrecruiters.com/v1/companies/{slug}/postings/{rid}")
        sections = ((d.get("jobAd") or {}).get("sections") or {}).values()
        src["text"] = html_to_text(" ".join(s.get("text", "") for s in sections))
    elif ats == "workday":
        d = fetch_json(src["detail_url"]).get("jobPostingInfo") or {}
        src["text"] = html_to_text(d.get("jobDescription", ""))
        extra = d.get("additionalLocations") or []
        if extra:
            job["location"] = "; ".join([d.get("location") or job["location"], *extra])
        if d.get("remoteType"):
            src["workplace"] = d["remoteType"]
    src["has_detail"] = True


_MONEY = r"\$\s?(\d{2,3}(?:,\d{3})+(?:\.\d{2})?|\d{2,3}(?:\.\d+)?\s?[kK])"
PAY_RE = re.compile(_MONEY + r"\s*(?:USD)?\s*(?:-|–|—|to)\s*" + _MONEY.replace(r"\$\s?", r"\$?\s?"))


def _money(s: str) -> float:
    s = s.replace(",", "").replace(" ", "")
    return float(s[:-1]) * 1000 if s[-1] in "kK" else float(s)


def _fmt(n: float) -> str:
    return f"${round(n / 1000)}K"


def extract_pay(src: dict) -> str:
    """Best-effort base pay range as text like '$180K–$240K', or ''."""
    ranges = list(src.get("pay_ranges") or [])
    sal = src.get("salary") or {}
    if sal.get("min") and (sal.get("currency") or "USD") == "USD" and \
            (sal.get("interval") or "per-year-salary").startswith("per-year"):
        ranges.append((float(sal["min"]), float(sal.get("max") or sal["min"])))
    if not ranges:
        for text in (src.get("pay_text") or "", src.get("text") or ""):
            for a, b in PAY_RE.findall(text):
                lo, hi = _money(a), _money(b)
                if 50_000 <= lo <= hi <= 2_000_000:
                    ranges.append((lo, hi))
            if ranges:
                break
    if not ranges:
        return ""
    lo, hi = min(r[0] for r in ranges), max(r[1] for r in ranges)
    note = " (varies by location)" if len(set(ranges)) > 1 else ""
    return f"{_fmt(lo)}–{_fmt(hi)}{note}"


_NON_US = re.compile(
    r"\b(uk|united kingdom|england|london|ireland|dublin|canada|toronto|vancouver|montreal|"
    r"europe|emea|apac|latam|germany|berlin|munich|france|paris|netherlands|amsterdam|spain|"
    r"madrid|barcelona|portugal|lisbon|poland|india|bangalore|bengaluru|singapore|japan|tokyo|"
    r"australia|sydney|melbourne|israel|tel aviv|brazil|mexico|switzerland|zurich|sweden|"
    r"stockholm|denmark|copenhagen|korea|seoul|china|hong kong|philippines|argentina|colombia)\b")
_US = re.compile(r"\b(united states|usa|us|u s|americas|north america)\b")
_REMOTE = re.compile(r"\b(remote|anywhere|distributed|work from home|wfh)\b")
_TZ_LIMIT = re.compile(
    r"\b(pacific|eastern|central)\s+(standard\s+)?time\b|\b(pst|est|cst|pt|et|ct)\b\s*(time ?zone|hours|business hours|tz)",
    re.I)
_MOUNTAIN = re.compile(r"\b(mountain|mst|mdt|mt)\b\s*(time|hours|zone)?|any (us )?time ?zone", re.I)
_CO_EXCLUDED = re.compile(
    r"(not|unable|cannot|can't|excluding|except|outside of|exclude)[^.\n]{0,100}\bcolorado\b", re.I)
_STATE_LIST = re.compile(
    r"(hire|hiring|employ|located|reside)[^.\n]{0,80}(following states|these states|states:|select states)", re.I)


class LocationSorter:
    def __init__(self, cfg: dict):
        self.tiers = []
        for t in cfg["tiers"]:
            self.tiers.append({**t, "re": [re.compile(p) for p in t.get("places", [])]})
        self.show_outside_us = cfg.get("show_outside_us", True)

    def tier(self, location: str, workplace: str | None = None) -> int:
        loc = re.sub(r"\s+", " ", (location or "").lower())
        wp = (workplace or "").lower()
        remote = "remote" in wp or bool(_REMOTE.search(loc))
        non_us = bool(_NON_US.search(loc))
        us = bool(_US.search(loc)) or not non_us
        for i, t in enumerate(self.tiers):
            if any(p.search(loc) for p in t["re"]):
                return i
            if t.get("remote_us") and remote and us:
                return i
            if t.get("other_us") and us:
                return i
            if t.get("outside_us"):
                return i
        return len(self.tiers) - 1


def location_flags(text: str, remote: bool) -> list[str]:
    """Warnings worth seeing before applying from Colorado."""
    flags = []
    if not text:
        return flags
    if _CO_EXCLUDED.search(text):
        flags.append("may exclude Colorado")
    elif remote and _STATE_LIST.search(text) and "colorado" not in text.lower():
        flags.append("limited state list, Colorado not named")
    if remote and _TZ_LIMIT.search(text) and not _MOUNTAIN.search(text):
        flags.append("asks for PT/CT/ET hours")
    return flags


def workplace_label(workplace: str | None, location: str) -> str:
    wp = (workplace or "").lower().replace("-", "").replace("_", "")
    if "hybrid" in wp:
        return "Hybrid"
    if "remote" in wp or _REMOTE.search((location or "").lower()):
        return "Remote"
    if "onsite" in wp or "office" in wp:
        return "On-site"
    return ""


def enrich(job: dict, sorter: LocationSorter) -> dict:
    """Adds tier, workplace, pay and flags to a matched job."""
    try:
        fetch_detail(job)
    except Exception as e:  # noqa: BLE001 - details are nice-to-have
        print(f"  (no details for {job['id']}: {type(e).__name__})", file=sys.stderr)
    src = job.get("_src", {})
    job["workplace"] = workplace_label(src.get("workplace"), job["location"])
    job["tier"] = sorter.tier(job["location"], src.get("workplace"))
    job["pay"] = job.get("pay") or extract_pay(src)
    job["flags"] = job.get("flags") or location_flags(src.get("text", ""), job["workplace"] == "Remote")
    return job


# ---------------------------------------------------------------- config

def load_companies(path=ROOT / "companies.yaml"):
    raw = yaml.safe_load(Path(path).read_text())
    out, seen = [], set()
    for industry, items in raw.items():
        for c in items or []:
            if c["name"] in seen:
                continue
            seen.add(c["name"])
            out.append({**c, "industry": industry})
    return out


def load_titles(path=ROOT / "titles.yaml"):
    return yaml.safe_load(Path(path).read_text())


def load_locations(path=ROOT / "locations.yaml"):
    return yaml.safe_load(Path(path).read_text())


def load_json(path, default):
    try:
        return json.loads(Path(path).read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def save_json(path, data):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(data, indent=1, sort_keys=True) + "\n")


# ---------------------------------------------------------------- title matching

def normalize_title(title: str) -> str:
    t = title.lower()
    t = t.replace("&", " and ")
    t = re.sub(r"\bsr\b\.?", "senior", t)
    t = re.sub(r"\bpre[\s\-]?sales\b", "presales", t)
    t = re.sub(r"[^a-z0-9]+", " ", t)
    return re.sub(r"\s+", " ", t).strip()


class TitleMatcher:
    def __init__(self, cfg: dict):
        lead = cfg.get("leadership", r"\b(director|head)\b")
        if cfg.get("include_vp"):
            lead = f"(?:{lead}|\\bvp\\b|\\bvice president\\b)"
        self.lead = re.compile(lead)
        self.exclude = [re.compile(p) for p in cfg.get("exclude", [])]
        self.families = {name: [re.compile(p) for p in pats]
                         for name, pats in cfg["families"].items()}

    def family(self, title: str) -> str | None:
        t = normalize_title(title)
        if not self.lead.search(t) or any(p.search(t) for p in self.exclude):
            return None
        for name, pats in self.families.items():
            if any(p.search(t) for p in pats):
                return name
        return None


# ---------------------------------------------------------------- discovery

def slug_candidates(company: dict) -> list[str]:
    base = re.sub(r"\(.*?\)", "", company["name"]).lower()
    cands = list(company.get("slugs") or [])
    cands += [re.sub(r"[^a-z0-9]", "", base), re.sub(r"[^a-z0-9]+", "-", base).strip("-")]
    out = []
    for c in cands:
        if c and c not in out:
            out.append(c)
    return out


def _tokens(s: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9]+", (s or "").lower()) if len(w) > 1} - {
        "inc", "the", "labs", "ai", "health", "group", "co", "technologies", "careers", "jobs"}


def name_matches(company: str, board_name: str | None) -> bool:
    if not board_name:
        return True
    a, b = _tokens(company), _tokens(board_name)
    if not a or not b:
        return True
    squash = lambda s: re.sub(r"[^a-z0-9]", "", s.lower())
    return bool(a & b) or squash(company) in squash(board_name) or squash(board_name) in squash(company)


def discover(company: dict) -> dict:
    """Find a company's board. Returns a boards.json entry."""
    today = dt.date.today().isoformat()
    fallback = None
    for slug in slug_candidates(company):
        for ats, reader in READERS.items():
            try:
                jobs, board_name = reader(slug)
            except Exception:
                continue
            if not jobs:
                continue
            entry = {"ats": ats, "slug": slug, "board_name": board_name,
                     "jobs": len(jobs), "found": today}
            if name_matches(company["name"], board_name):
                return entry
            fallback = fallback or {**entry, "name_mismatch": True}
    return fallback or {"ats": None, "tried": today}


def needs_discovery(company, entry, force=False) -> bool:
    if company.get("board") or company.get("workday") or company.get("skip"):
        return False
    if force or not entry:
        return True
    if entry.get("ats"):
        return bool(entry.get("broken"))
    tried = dt.date.fromisoformat(entry.get("tried", "1970-01-01"))
    return (dt.date.today() - tried).days >= REDISCOVER_AFTER_DAYS


def board_for(company, boards):
    if company.get("workday"):
        return {"ats": "workday", "slug": company["workday"]}
    if company.get("board"):
        return company["board"]
    entry = boards.get(company["name"]) or {}
    return entry if entry.get("ats") and not entry.get("name_mismatch") else None


def read_board(board):
    if board["ats"] == "workday":
        return read_workday(board["slug"])[0]
    return READERS[board["ats"]](board["slug"])[0]


# ---------------------------------------------------------------- diff

def diff_matches(matches: list[dict], seen: dict, today: str, checked_companies: set[str]):
    """Returns (new, closed) and updates `seen` in place.
    Only companies checked successfully today can have postings marked closed."""
    current = {m["id"] for m in matches}
    new = []
    for m in matches:
        rec = seen.get(m["id"])
        if rec is None:
            seen[m["id"]] = {"first_seen": today, **{k: m[k] for k in
                             ("company", "title", "url", "family", "location")}}
            new.append(m)
        elif rec.get("closed"):
            rec.pop("closed")  # reposted
    closed = []
    for jid, rec in seen.items():
        if jid not in current and not rec.get("closed") and rec.get("company") in checked_companies:
            rec["closed"] = today
            closed.append(rec)
    return new, closed


# ---------------------------------------------------------------- output

def _esc(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


FAMILY_TAGS = {"Solution Consulting": "SC", "Product Support": "Support", "Forward Deployed": "FDE"}


def format_role(m: dict) -> str:
    parts = [f"<{m['url']}|{_esc(m['title'])}>", _esc(m["company"])]
    where = _esc(m["location"])[:80] if m.get("location") else ""
    if m.get("workplace") and m["workplace"].lower() not in where.lower():
        where = f"{where} ({m['workplace']})" if where else m["workplace"]
    if where:
        parts.append(where)
    if m.get("pay"):
        parts.append(m["pay"])
    line = "• " + " · ".join(parts) + f"  `{FAMILY_TAGS.get(m.get('family'), m.get('family', ''))}`"
    for f in m.get("flags") or []:
        line += f"  :warning: {f}"
    return line


def tier_sections(roles: list[dict], tier_names: list[str], show_outside_us: bool) -> list[str]:
    lines = []
    for i, name in enumerate(tier_names):
        group = [m for m in roles if m.get("tier", len(tier_names) - 1) == i]
        if not group:
            continue
        if i == len(tier_names) - 1 and not show_outside_us:
            continue
        lines.append(f"\n*{name}* ({len(group)})")
        for m in sorted(group, key=lambda x: (x.get("family", ""), x["company"], x["title"])):
            lines.append(format_role(m))
    return lines


def build_digest(new, closed, uncovered, failed, checked, baseline, date_label, show_coverage,
                 tier_names=("Matches",), show_outside_us=True, sweep=(), suggestions=()):
    lines = []
    n = len(new)
    if baseline:
        lines.append(f"*Job watch is live: {n} matching {'role' if n == 1 else 'roles'} open today* "
                     f"({checked} boards checked). From tomorrow you'll only see new ones.")
    elif new:
        lines.append(f"*Job watch, {date_label}: {n} new {'match' if n == 1 else 'matches'}* "
                     f"({checked} boards checked)")
    else:
        lines.append(f"Job watch, {date_label}: no new matches on your list ({checked} boards checked)")

    lines += tier_sections(new, list(tier_names), show_outside_us)

    if sweep:
        lines.append(f"\n*Weekly sweep: {len(sweep)} {'role' if len(sweep) == 1 else 'roles'} "
                     f"at companies not on your list*")
        lines += tier_sections(sweep, list(tier_names), show_outside_us)
    if suggestions:
        lines.append("\n_Worth adding to your list (2+ open roles):_ " + ", ".join(
            _esc(s) for s in suggestions))

    if closed and not baseline:
        lines.append(f"\n_No longer listed ({len(closed)}):_ " + "; ".join(
            f"{_esc(c['title'])} · {_esc(c['company'])}" for c in closed[:15]))
    if failed:
        lines.append(f"\n:warning: Couldn't read {len(failed)} board(s): " + ", ".join(sorted(failed)))
    if uncovered and show_coverage:
        lines.append(f"\n_Not covered yet ({len(uncovered)}), board needs to be set up:_ "
                     + ", ".join(sorted(uncovered)))
    elif uncovered:
        lines.append(f"\n_{len(uncovered)} companies not covered yet (listed on Mondays)._")
    return "\n".join(lines)


def post_slack(text: str, webhook: str):
    chunks, buf = [], ""
    for line in text.split("\n"):
        if len(buf) + len(line) > 3500:
            chunks.append(buf)
            buf = ""
        buf += line + "\n"
    if buf.strip():
        chunks.append(buf)
    for c in chunks:
        r = requests.post(webhook, json={"text": c, "unfurl_links": False}, timeout=TIMEOUT)
        r.raise_for_status()
        time.sleep(1)


# ---------------------------------------------------------------- commands

def run(args):
    companies = [c for c in load_companies() if not c.get("skip")]
    matcher = TitleMatcher(load_titles())
    boards = load_json(BOARDS_FILE, {})
    first_run = not SEEN_FILE.exists()
    seen = load_json(SEEN_FILE, {})
    today = dt.datetime.now(TZ).date()

    # 1. Find boards for companies we haven't placed yet.
    todo = [c for c in companies if needs_discovery(c, boards.get(c["name"]), args.rediscover)]
    if todo:
        print(f"Discovering boards for {len(todo)} companies...")
        with ThreadPoolExecutor(WORKERS) as pool:
            for c, entry in zip(todo, pool.map(discover, todo)):
                boards[c["name"]] = entry
                print(f"  {c['name']}: {entry.get('ats') or 'not found'} {entry.get('slug', '')}")

    # 2. Read every placed board.
    targets = [(c, board_for(c, boards)) for c in companies]
    uncovered = [c["name"] for c, b in targets if not b]
    targets = [(c, b) for c, b in targets if b]

    def read(pair):
        c, b = pair
        try:
            return c, read_board(b), None
        except Exception as e:  # noqa: BLE001
            return c, None, f"{type(e).__name__}: {e}"

    matches, failed, ok = [], [], set()
    with ThreadPoolExecutor(WORKERS) as pool:
        for c, jobs, err in pool.map(read, targets):
            if err is not None:
                failed.append(c["name"])
                print(f"  ! {c['name']}: {err}", file=sys.stderr)
                if c["name"] in boards and boards[c["name"]].get("ats"):
                    boards[c["name"]]["broken"] = today.isoformat()
                continue
            ok.add(c["name"])
            boards.get(c["name"], {}).pop("broken", None)
            for j in jobs:
                fam = matcher.family(j["title"])
                if fam:
                    matches.append({**j, "company": c["name"], "family": fam})

    # 3. Diff, then add location, pay and flags to the new roles.
    new, closed = diff_matches(matches, seen, today.isoformat(), ok)
    loc_cfg = load_locations()
    sorter = LocationSorter(loc_cfg)
    if new:
        with ThreadPoolExecutor(WORKERS) as pool:
            new = list(pool.map(lambda j: enrich(j, sorter), new))
        for j in new:
            seen[j["id"]].update(pay=j["pay"], tier=j["tier"])

    # 4. Pick up roles the weekly sweep found outside the list.
    sweep, suggestions = take_sweep(seen, companies, sorter, today.isoformat())

    tier_names = [t["name"] for t in loc_cfg["tiers"]]
    text = build_digest(new, closed, uncovered, failed, len(ok), first_run,
                        today.strftime("%a %b %-d"),
                        show_coverage=first_run or today.weekday() == 0,
                        tier_names=tier_names, show_outside_us=loc_cfg.get("show_outside_us", True),
                        sweep=sweep, suggestions=suggestions)
    print("\n" + text)
    REPORT_FILE.parent.mkdir(parents=True, exist_ok=True)
    REPORT_FILE.write_text(text + "\n")

    if args.dry_run:
        print("\n(dry run: nothing posted or saved)")
        return 0
    webhook = os.environ.get("SLACK_WEBHOOK_URL")
    post_empty = os.environ.get("POST_WHEN_EMPTY", "true").lower() == "true"
    if webhook and (new or sweep or first_run or failed or post_empty):
        post_slack(text, webhook)
    elif not webhook:
        print("\nSLACK_WEBHOOK_URL not set; digest printed only.")
    save_json(BOARDS_FILE, boards)
    save_json(SEEN_FILE, seen)
    if SWEEP_FILE.exists():
        save_json(SWEEP_FILE, {"items": [], "suggest_add": [], "last_posted": today.isoformat()})
    return 0


def _squash(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", re.sub(r"\(.*?\)", "", s.lower()))


def take_sweep(seen: dict, companies: list[dict], sorter: LocationSorter, today: str):
    """Reads state/sweep_inbox.json written by the weekly sweep. Returns (roles, suggestions)."""
    inbox = load_json(SWEEP_FILE, {})
    on_list = {_squash(c["name"]) for c in companies}
    roles = []
    for item in inbox.get("items", []):
        if not item.get("url") or not item.get("title") or not item.get("company"):
            continue
        key = "sweep:" + item["url"]
        if key in seen or _squash(item["company"]) in on_list:
            continue
        role = {
            "id": key, "company": item["company"], "title": item["title"], "url": item["url"],
            "location": item.get("location", ""), "family": item.get("family", ""),
            "pay": item.get("pay", ""), "flags": item.get("flags") or [],
            "workplace": workplace_label(item.get("workplace"), item.get("location", "")),
        }
        role["tier"] = sorter.tier(role["location"], item.get("workplace"))
        seen[key] = {"first_seen": today, "source": "sweep",
                     **{k: role[k] for k in ("company", "title", "url", "family", "location")}}
        roles.append(role)
    return roles, list(inbox.get("suggest_add") or [])


def coverage(_args):
    boards = load_json(BOARDS_FILE, {})
    for c in load_companies():
        b = board_for(c, boards)
        e = boards.get(c["name"], {})
        status = (f"{b['ats']:<16}{b['slug']}" if b else
                  f"{'CHECK NAME':<16}{e.get('ats')}:{e.get('slug')} board says '{e.get('board_name')}'"
                  if e.get("name_mismatch") else "not covered")
        print(f"{c['name']:<28}{status}")
    return 0


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--dry-run", action="store_true")
    r.add_argument("--rediscover", action="store_true")
    d = sub.add_parser("discover")
    d.set_defaults(rediscover=True, dry_run=False)
    sub.add_parser("coverage")
    args = p.parse_args(argv)
    if args.cmd == "coverage":
        return coverage(args)
    return run(args)


if __name__ == "__main__":
    sys.exit(main())

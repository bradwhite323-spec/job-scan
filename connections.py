"""Match LinkedIn connections (from LinkedIn's own data export) to target companies.

The export is personal data about other people, so it is never committed. It is read from:
  1. the path in CONNECTIONS_CSV, or
  2. the CONNECTIONS_B64 env var (gzip + base64 of the CSV, stored as a GitHub Actions secret).
Build the secret value with:  python connections.py pack Connections.csv [part2.csv ...]
"""
from __future__ import annotations

import base64
import csv
import gzip
import io
import os
import re
import sys
from pathlib import Path

SUFFIXES = r"\b(inc|incorporated|llc|ltd|limited|corp|corporation|co|company|plc|gmbh|bv|b v|sa|ag|pbc|holdings|group|technologies|technology|labs|hq)\b"

# Higher number = more useful person to ask about a leadership role.
SENIORITY = [
    (5, r"\b(chief|ceo|coo|cto|cro|cco|cpo|founder|co founder|president)\b"),
    (4, r"\b(svp|evp|senior vice president|vp|vice president)\b"),
    (3, r"\b(head|director|senior director)\b"),
    (2, r"\b(manager|lead|principal|staff)\b"),
]
RELEVANT = r"\b(support|success|customer|cx|solution|solutions|presales|sales engineer|forward deployed|services|implementation|onboarding|experience|talent|recruit|people)\b"


def norm_company(s: str) -> str:
    s = (s or "").lower().replace("&", " and ")
    s = re.sub(r"[^a-z0-9]+", " ", s)
    s = re.sub(SUFFIXES, " ", s)
    return re.sub(r"\s+", "", s)


def company_keys(company: dict) -> set[str]:
    name = company["name"]
    keys = {norm_company(re.sub(r"\(.*?\)", "", name))}
    keys |= {norm_company(a) for a in re.findall(r"\((.*?)\)", name)}
    keys |= {norm_company(a) for a in company.get("aliases") or []}
    return {k for k in keys if len(k) >= 2}


def _read_text() -> str | None:
    path = os.environ.get("CONNECTIONS_CSV")
    if path and Path(path).exists():
        return Path(path).read_text(encoding="utf-8-sig")
    blob = os.environ.get("CONNECTIONS_B64")
    if blob:
        return gzip.decompress(base64.b64decode(blob)).decode("utf-8")
    return None


def parse(text: str) -> list[dict]:
    lines = text.splitlines()
    start = next((i for i, l in enumerate(lines) if l.startswith("First Name,")), 0)
    rows = csv.DictReader(io.StringIO("\n".join(lines[start:])))
    out = []
    for r in rows:
        if not (r.get("Company") or "").strip():
            continue
        title = (r.get("Position") or "").strip()
        t = re.sub(r"[^a-z0-9]+", " ", title.lower())
        senior = next((lvl for lvl, pat in SENIORITY if re.search(pat, t)), 1)
        out.append({
            "name": f"{(r.get('First Name') or '').strip()} {(r.get('Last Name') or '').strip()}".strip(),
            "url": (r.get("URL") or "").strip(),
            "company": r["Company"].strip(),
            "title": title,
            "key": norm_company(r["Company"]),
            "score": senior * 2 + (3 if re.search(RELEVANT, t) else 0),
        })
    return out


def load() -> list[dict]:
    text = _read_text()
    return parse(text) if text else []


def index(people: list[dict], companies: list[dict]) -> dict[str, list[dict]]:
    """company name -> connections there, best first."""
    by_key: dict[str, list[dict]] = {}
    for p in people:
        by_key.setdefault(p["key"], []).append(p)
    out = {}
    for c in companies:
        hits = [p for k in company_keys(c) for p in by_key.get(k, [])]
        if hits:
            out[c["name"]] = sorted({p["url"] or p["name"]: p for p in hits}.values(),
                                    key=lambda p: -p["score"])
    return out


def slack_line(people: list[dict], limit: int = 3) -> str:
    def esc(s):
        return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    shown = [f"<{p['url']}|{esc(p['name'])}> ({esc(p['title'])[:60]})" if p["url"]
             else f"{esc(p['name'])} ({esc(p['title'])[:60]})" for p in people[:limit]]
    more = f" +{len(people) - limit} more" if len(people) > limit else ""
    return "    :busts_in_silhouette: You know: " + "; ".join(shown) + more


def pack(*paths: str) -> str:
    """Slim one or more export files to the fields we use, then gzip+base64 for a GitHub secret."""
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["First Name", "Last Name", "URL", "Company", "Position"])
    seen = set()
    for path in paths:
        lines = Path(path).read_text(encoding="utf-8-sig").splitlines()
        start = next((i for i, l in enumerate(lines) if l.startswith("First Name,")), 0)
        for r in csv.DictReader(io.StringIO("\n".join(lines[start:]))):
            key = r.get("URL") or (r.get("First Name"), r.get("Last Name"), r.get("Company"))
            if (r.get("Company") or "").strip() and key not in seen:
                seen.add(key)
                w.writerow([r.get(k, "") for k in ("First Name", "Last Name", "URL", "Company", "Position")])
    return base64.b64encode(gzip.compress(buf.getvalue().encode(), 9)).decode()


if __name__ == "__main__":
    if len(sys.argv) >= 3 and sys.argv[1] == "pack":
        blob = pack(*sys.argv[2:])
        print(blob)
        print(f"\n{len(blob)} bytes (GitHub secrets max 48 KB)", file=sys.stderr)
    else:
        print(__doc__)

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import watch  # noqa: E402

M = watch.TitleMatcher(watch.load_titles())

SHOULD_MATCH = {
    "Director of Solution Consulting": "Solution Consulting",
    "Director, Solutions Consulting - North America": "Solution Consulting",
    "Sr. Director, Solutions Engineering": "Solution Consulting",
    "Head of Pre-Sales": "Solution Consulting",
    "Director of Presales & Solutions": "Solution Consulting",
    "Head of Sales Engineering, EMEA": "Solution Consulting",
    "Director, Solutions Architecture": "Solution Consulting",
    "Director of Customer Engineering": "Solution Consulting",
    "Director, Value Engineering": "Solution Consulting",
    "Director of Business Value Consulting": "Solution Consulting",
    "Director, Global Solution Consultants": "Solution Consulting",
    "Head of Technical Sales": "Solution Consulting",
    "Senior Director of Product Support": "Product Support",
    "Head of Technical Support": "Product Support",
    "Director, Customer Support Operations": "Product Support",
    "Director of Support": "Product Support",
    "Head of Support, Americas": "Product Support",
    "Director, Support Engineering": "Product Support",
    "Director of Escalation Management": "Product Support",
    "Director, Premier Support": "Product Support",
    "Head of Customer Experience": "Product Support",
    "Director of Technical Services": "Product Support",
    "Director, Forward Deployed Engineering": "Forward Deployed",
    "Head of Deployment Engineering": "Forward Deployed",
    "Director of Solutions & Deployment": "Forward Deployed",
}

SHOULD_NOT_MATCH = [
    "Solutions Engineer",
    "Senior Manager, Solutions Consulting",
    "Associate Director, Technical Support",
    "Director of IT Support",
    "Director, Product Marketing",
    "Account Director",
    "Customer Support Specialist",
    "Head of Sales",
    "Director of Clinical Support Services",
    "Headset Support Engineer",
]


def test_matches():
    for title, fam in SHOULD_MATCH.items():
        assert M.family(title) == fam, title


def test_non_matches():
    for title in SHOULD_NOT_MATCH:
        assert M.family(title) is None, title


def test_companies_load_and_dedupe():
    cos = watch.load_companies()
    names = [c["name"] for c in cos]
    assert len(names) == len(set(names))
    assert len(cos) > 190


def test_slug_candidates():
    assert watch.slug_candidates({"name": "Anysphere (Cursor)", "slugs": ["cursor"]})[:2] == ["cursor", "anysphere"]
    assert "scale-ai" in watch.slug_candidates({"name": "Scale AI"})


def test_name_matches():
    assert watch.name_matches("Glean", "Glean Technologies")
    assert watch.name_matches("Scale AI", "Scale")
    assert not watch.name_matches("Remote", "Acme Corp")


def test_greenhouse_parse(monkeypatch):
    def fake(url, *a, **k):
        if url.endswith("/jobs"):
            return {"jobs": [{"id": 1, "title": "Director of Sales Engineering",
                              "location": {"name": "Remote - US"},
                              "absolute_url": "https://x/1", "first_published": "2026-11-01"}]}
        return {"name": "Acme"}
    monkeypatch.setattr(watch, "fetch_json", fake)
    jobs, name = watch.read_greenhouse("acme")
    assert name == "Acme" and jobs[0]["id"] == "greenhouse:acme:1"
    assert jobs[0]["location"] == "Remote - US"


def test_lever_ashby_parse(monkeypatch):
    monkeypatch.setattr(watch, "fetch_json", lambda url, *a, **k: [
        {"id": "ab", "text": "Head of Support", "hostedUrl": "https://l/ab",
         "createdAt": 1762000000000, "categories": {"location": "NYC", "team": "Support"}}])
    jobs, _ = watch.read_lever("acme")
    assert jobs[0]["department"] == "Support" and jobs[0]["posted"].startswith("2025")

    monkeypatch.setattr(watch, "fetch_json", lambda url, *a, **k: {"jobs": [
        {"id": "z", "title": "Director", "jobUrl": "u", "isListed": False},
        {"id": "y", "title": "Head of Presales", "jobUrl": "u2", "location": "SF"}]})
    jobs, _ = watch.read_ashby("acme")
    assert [j["id"] for j in jobs] == ["ashby:acme:y"]


def test_workday_parse(monkeypatch):
    calls = []

    def fake(url, method="GET", body=None):
        calls.append((url, body["searchText"], body["offset"]))
        if body["offset"] == 0 and body["searchText"] == "director":
            return {"total": 1, "jobPostings": [{"title": "Director, Technical Support",
                                                 "externalPath": "/job/Remote/Dir_R1",
                                                 "locationsText": "Remote", "postedOn": "Posted Today"}]}
        return {"total": 0, "jobPostings": []}
    monkeypatch.setattr(watch, "fetch_json", fake)
    monkeypatch.setattr(watch.time, "sleep", lambda s: None)
    jobs, _ = watch.read_workday("https://acme.wd5.myworkdayjobs.com/en-US/External")
    assert calls[0][0] == "https://acme.wd5.myworkdayjobs.com/wday/cxs/acme/External/jobs"
    assert jobs[0]["url"] == "https://acme.wd5.myworkdayjobs.com/External/job/Remote/Dir_R1"


def test_discover_prefers_name_match(monkeypatch):
    def gh(slug):
        return ([{"id": "x"}], "Somebody Else") if slug == "acme" else (_ for _ in ()).throw(watch.NotFound())

    def ashby(slug):
        if slug == "acme":
            return [{"id": "y"}], None
        raise watch.NotFound()
    monkeypatch.setattr(watch, "READERS", {"greenhouse": gh, "ashby": ashby})
    e = watch.discover({"name": "Acme"})
    assert e["ats"] == "ashby" and not e.get("name_mismatch")


def test_diff():
    seen = {}
    m1 = {"id": "a", "company": "Acme", "title": "Head of Support", "url": "u",
          "family": "Product Support", "location": ""}
    m2 = {**m1, "id": "b", "company": "Beta"}
    new, closed = watch.diff_matches([m1, m2], seen, "2026-11-04", {"Acme", "Beta"})
    assert len(new) == 2 and not closed
    # Day 2: Acme's role gone; Beta's board failed (not checked) so it stays open.
    new, closed = watch.diff_matches([], seen, "2026-11-05", {"Acme"})
    assert not new and [c["company"] for c in closed] == ["Acme"]
    assert "closed" not in seen["b"]


S = watch.LocationSorter(watch.load_locations())


def test_location_tiers():
    cases = {
        ("Remote - US", None): 0,
        ("United States", "Remote"): 0,
        ("Denver, CO", None): 0,
        ("Boulder, Colorado (Hybrid)", None): 0,
        ("San Francisco, CA", "Hybrid"): 1,
        ("New York, NY; London, UK", None): 1,
        ("Austin, TX", None): 2,
        ("Seattle, WA", "OnSite"): 2,
        ("London, United Kingdom", None): 3,
        ("Remote - Canada", None): 3,
        ("Remote, EMEA", None): 3,
    }
    for (loc, wp), tier in cases.items():
        assert S.tier(loc, wp) == tier, (loc, wp)


def test_pay_extraction():
    assert watch.extract_pay({"pay_ranges": [(180000, 240000)]}) == "$180K–$240K"
    assert watch.extract_pay({"salary": {"min": 200000, "max": 260000, "currency": "USD",
                                         "interval": "per-year-salary"}}) == "$200K–$260K"
    assert watch.extract_pay({"pay_text": "$190K – $230K • Offers Equity"}) == "$190K–$230K"
    text = ("The base salary range is $176,000 - $220,000 in Colorado and "
            "$195,500 to $244,000 in San Francisco. Hourly support role $25 - $30.")
    assert watch.extract_pay({"text": text}) == "$176K–$244K (varies by location)"
    assert watch.extract_pay({"text": "Great benefits, 401k match"}) == ""


def test_location_flags():
    assert "asks for PT/CT/ET hours" in watch.location_flags(
        "Must be able to work Pacific Time hours.", remote=True)
    assert watch.location_flags("Core hours overlap Pacific or Mountain time zone.", remote=True) == []
    assert "may exclude Colorado" in watch.location_flags(
        "We are unable to hire in Colorado or Hawaii at this time.", remote=True)
    assert "limited state list, Colorado not named" in watch.location_flags(
        "We can hire in the following states: CA, NY, TX, WA.", remote=True)
    assert watch.location_flags("Must work Eastern Time hours.", remote=False) == []


def test_sweep_inbox(tmp_path, monkeypatch):
    monkeypatch.setattr(watch, "SWEEP_FILE", tmp_path / "sweep.json")
    watch.save_json(watch.SWEEP_FILE, {"items": [
        {"company": "Acme Robotics", "title": "Head of Solutions Engineering", "url": "https://a/1",
         "location": "Remote - US", "family": "Solution Consulting", "pay": "$200K–$250K"},
        {"company": "Stripe", "title": "Director of Support", "url": "https://s/9", "location": "NYC"},
    ], "suggest_add": ["Acme Robotics (2 roles)"]})
    seen = {}
    roles, sugg = watch.take_sweep(seen, [{"name": "Stripe"}], S, "2026-11-09")
    assert [r["company"] for r in roles] == ["Acme Robotics"] and roles[0]["tier"] == 0
    assert sugg == ["Acme Robotics (2 roles)"] and "sweep:https://a/1" in seen
    roles, _ = watch.take_sweep(seen, [{"name": "Stripe"}], S, "2026-11-10")
    assert roles == []  # already posted


def test_tiered_digest():
    roles = [
        {"company": "B", "title": "Head of Support", "url": "u1", "family": "Product Support",
         "location": "Austin, TX", "tier": 2, "pay": "", "flags": []},
        {"company": "A", "title": "Director, Sales Engineering", "url": "u2",
         "family": "Solution Consulting", "location": "Remote - US", "workplace": "Remote",
         "tier": 0, "pay": "$200K–$250K", "flags": ["asks for PT/CT/ET hours"]},
    ]
    names = [t["name"] for t in watch.load_locations()["tiers"]]
    text = watch.build_digest(roles, [], [], [], 10, False, "Mon Nov 9", False, names)
    assert text.index("Remote (US) or Colorado") < text.index("Relocation (other US)")
    assert "$200K–$250K" in text and ":warning: asks for PT/CT/ET hours" in text and "`SC`" in text


def test_digest_formatting():
    m = {"id": "a", "company": "A&B", "title": "Head of <Support>", "url": "https://u",
         "family": "Product Support", "location": "Denver"}
    text = watch.build_digest([m], [], ["Netflix"], [], 150, False, "Wed Nov 4", True)
    assert "1 new match*" in text and "A&amp;B" in text and "&lt;Support&gt;" in text
    assert "Not covered yet (1)" in text

"""AC14 (news → verification → decision) and AC15 (discovery beyond ticket listings) with controlled
TEST fixtures. These are labelled fixtures, never live news."""
from datetime import timedelta

from locallift import db, developments, ingest, jobs, plans, ranking, verify
from locallift.sources import news as news_src
from locallift.timeutil import iso, now_utc, parse

ARENA = "fixture:arena-concert"


def _news(nid, title, summary="", hours_ago=1, url=None):
    return {"id": nid, "provider": "gothamist", "url": url or f"https://example.test/{nid}", "title": title,
            "domain": "example.test", "summary": summary, "published_at": iso(now_utc() - timedelta(hours=hours_ago)),
            "dedupe_key": news_src.dedupe_key(title)}


def _ingest(items):
    ingest.store_news(items, provenance="test_fixture")
    ingest.link_feed_news()
    developments.ingest_news(provenance="test_fixture")
    developments.build_briefing()


def _arena():
    return db.q1("SELECT * FROM event WHERE id=?", (ARENA,))


def _listing(**over):
    ev = _arena()
    base = {"provider": "fixture", "provider_id": "arena-concert", "title": ev["title"], "venue": ev["venue"],
            "lat": ev["lat"], "lon": ev["lon"], "coord_quality": "venue_coordinates", "start_utc": ev["start_utc"],
            "end_utc": ev["end_utc"], "status": "scheduled", "url": ev["url"], "category": ev["category"],
            "performers": ["Fixture Band"], "raw": {}}
    base.update(over)
    return base


def test_ac15_gathering_without_ticket_listing_merges_and_stays_unpinned(loaded):
    t = "Street fair will close Seventh Avenue near Penn Station on Nov. 1"
    _ingest([_news("news:sf-1", t, "Organizers expect crowds on Seventh Avenue."),
             _news("news:sf-2", t, "Syndicated copy.", url="https://example.test/syndicated/sf")])
    devs = db.q("SELECT * FROM development WHERE headline LIKE 'Street fair%'")
    assert len(devs) == 1  # duplicate coverage merged into one development
    d = devs[0]
    assert d["dev_type"] in ("gathering", "access_change")
    assert d["lat"] is None and d["location_precision"].startswith("neighborhood")  # no fabricated pin
    assert d["effective_start"] == "2026-11-01"  # effective date separate from publication date
    assert d["version"] == 1  # a syndicated copy adds a source but is not a material update
    assert db.q1("SELECT COUNT(*) c FROM development_source WHERE development_id=?", (d["id"],))["c"] == 2
    assert not db.q("SELECT * FROM development_event WHERE development_id=?", (d["id"],))  # no ticket match needed
    item = next(b for b in developments.briefing(10) if b["development_id"] == d["id"])
    assert item["implication_basis"] == "rule_based"
    assert item["action"] in ("Investigate", "Monitor", "Quick promo")  # date known, time not stated: never "closed"
    assert "no precise location (not pinned)" in item["certainty"]["limitations"]


def test_ac15_verified_cancellation_leads_briefing_but_is_not_an_opportunity(loaded):
    _ingest([_news("news:gather", "Halloween block party planned near Herald Square on Oct. 31")])
    _ingest([_news("news:cxl", "Fixture Band cancels Midtown arena show")])
    verify.TEST_PRIMARY[ARENA] = _listing(status="cancelled")
    try:
        jobs.process_reassessments()
    finally:
        verify.TEST_PRIMARY.clear()
    top = developments.briefing(5)[0]
    assert top["dev_type"] == "cancellation"
    assert top["verification_state"].startswith("verified")
    assert "event listing" in top["source_types"] and "reporting" in top["source_types"]  # news + listing merged
    ranking.rank_all()
    assert db.q1("SELECT eligible FROM opportunity WHERE event_id=?", (ARENA,))["eligible"] == 0


def test_ac15_retrospective_and_roundup_items_are_not_developments(loaded):
    _ingest([_news("news:old", "Look back: parade crowds filled Herald Square on Oct. 2", hours_ago=2),
             _news("news:roundup", "Early Addition: parade near Penn Station, plus more", hours_ago=1)])
    assert not db.q("SELECT 1 FROM development WHERE headline LIKE 'Look back%' OR headline LIKE 'Early Addition%'")


def test_ac14_confirmed_time_change_proposes_retimed_plan_with_before_after(loaded):
    p = plans.generate(ARENA)
    plans.edit(p["id"], "copy.sign_copy", "OWNER SIGN")
    plans.toggle_task(p["id"], "assess-1", True)
    old_start = _arena()["start_utc"]
    _ingest([_news("news:moved", "Fixture Band show at Midtown arena moved to earlier start time")])
    dev = db.q1("SELECT * FROM development WHERE headline LIKE 'Fixture Band show%'")
    assert dev["dev_type"] == "schedule_change"
    assert db.q1("SELECT 1 FROM job_run WHERE job='verify_request' AND status='queued'")  # adaptive verification queued
    new_start = iso(parse(old_start) - timedelta(hours=2))
    verify.TEST_PRIMARY[ARENA] = _listing(start_utc=new_start)
    try:
        jobs.process_reassessments()
    finally:
        verify.TEST_PRIMARY.clear()
    g = plans.get(p["id"])
    rev = g["pending_revision"]
    assert rev and rev["kind"] == "rescheduling"
    assert rev["before_after"]["before"]["event_start"] == old_start
    assert rev["before_after"]["after"]["event_start"] == new_start
    assert "phases" in rev["changes"]
    decisions = db.q("SELECT * FROM decision WHERE development_id=?", (dev["id"],))
    assert decisions and decisions[-1]["decision"] == "revision proposed"
    assert db.q1("SELECT verification_state FROM development WHERE id=?", (dev["id"],))["verification_state"].startswith("verified")
    g = plans.resolve_revision(p["id"], accept=True)
    assert g["data"]["event_start"] == new_start
    assert g["data"]["copy"]["sign_copy"] == "OWNER SIGN"  # owner edit preserved
    assert any(t["done"] for ph in g["data"]["phases"] for t in ph["tasks"] if t["id"] == "assess-1")
    assert all(parse(t["due"]) > now_utc() for ph in g["data"]["phases"] for t in ph["tasks"])
    assert len(db.q("SELECT * FROM notification WHERE event_id=? AND kind='rescheduling'", (ARENA,))) == 1


def test_ac14_unconfirmed_report_yields_explicit_no_change(loaded):
    p = plans.generate(ARENA)
    _ingest([_news("news:rumor", "Fixture Band arena show postponed, fans say")])
    verify.TEST_PRIMARY[ARENA] = _listing()  # primary listing unchanged
    try:
        jobs.process_reassessments()
    finally:
        verify.TEST_PRIMARY.clear()
    dev = db.q1("SELECT * FROM development WHERE headline LIKE 'Fixture Band arena show postponed%'")
    assert dev["verification_state"].startswith("disputed")
    dec = db.q("SELECT * FROM decision WHERE development_id=?", (dev["id"],))[-1]
    assert dec["decision"].startswith("no change")
    assert plans.get(p["id"])["pending_revision"] is None
    assert db.q1("SELECT 1 FROM claim WHERE event_id=? AND classification='disputed'", (ARENA,))
    assert db.q1("SELECT 1 FROM watch WHERE event_id=? AND active=1", (ARENA,))


def test_official_permit_becomes_unpinned_development(loaded):
    item = {"id": "permit:fx1", "provider": "nyc_permits", "source_type": "official announcement", "dev_type": "gathering",
            "title": "Permitted street festival: Fixture Street Fair", "summary": "Fixture Street Fair — West 31 Street.",
            "url": "https://example.test/permits", "published_at": None, "effective_start": iso(now_utc() + timedelta(days=2)),
            "effective_end": None, "location_text": "West 31 Street Between 7 Avenue And 8 Avenue",
            "location_precision": "street segment text (no coordinates published; not pinned)", "cluster": "fx1", "raw": {}}
    developments.ingest_official([item], provenance="test_fixture")
    developments.ingest_official([item], provenance="test_fixture")  # idempotent
    developments.build_briefing()
    d = db.q1("SELECT * FROM development WHERE id='dev-off-fx1'")
    assert d["version"] == 1 and d["lat"] is None
    b = next(x for x in developments.briefing(10) if x["development_id"] == "dev-off-fx1")
    assert b["source_types"] == ["official announcement"]
    assert b["verification_state"] == "official record"
    assert b["priority_factors"]["evidence"] >= 5


def test_briefing_priority_separate_from_opportunity_score(loaded):
    _ingest([_news("news:gather2", "Halloween block party planned near Herald Square on Oct. 31")])
    b = developments.briefing(5)[0]
    assert set(b["priority_factors"]) >= {"type", "time_to_act", "freshness", "evidence", "affects_plan"}

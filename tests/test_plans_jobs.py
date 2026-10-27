from datetime import timedelta

import pytest

from locallift import db, jobs, plans, ranking, timeutil
from locallift.timeutil import iso, now_utc, parse


def _all_tasks(p):
    return [t for ph in p["data"]["phases"] for t in ph["tasks"]]


def test_phases_complete_and_never_in_past(loaded):
    p = plans.generate("fixture:arena-concert")
    d = p["data"]
    now = now_utc()
    for ph in d["phases"]:
        for k in ("objective", "owner", "tasks", "deliverables", "success_measure", "start", "end"):
            assert ph[k], (ph["key"], k)
        assert "spend" in ph and "dependencies" in ph
    assert all(parse(t["due"]) > now for t in _all_tasks(p))
    assert d["contingencies"].keys() >= {"cancellation", "low_response", "stockout", "overload"}
    assert d["budget"]["total"] <= 75 and d["budget"]["within_caps"]
    assert d["budget"]["paid_media"] == 0
    assert "not affiliated" in d["copy"]["instagram_caption"]


def test_same_day_compression(loaded):
    db.ex("UPDATE event SET start_utc=? WHERE id='fixture:morning-run'", (iso(now_utc() + timedelta(hours=8)),))
    p = plans.generate("fixture:morning-run")
    assert p["data"]["mode"] == "same_day"
    assert [ph["name"] for ph in p["data"]["phases"]] == ["Prepare now", "Activate", "Review"]
    assert p["data"]["special_recipe"] is None
    assert all(parse(t["due"]) > now_utc() for t in _all_tasks(p))
    assert not any(c["included"] for c in p["data"]["channels"] if c["type"] == "permission_dependent")


def test_weekly_budget_aggregation(loaded):
    # Seven plans in the same ISO week cannot exceed $200 in total.
    ids = []
    for i in range(7):
        eid = f"fixture:extra-{i}"
        db.ex("""INSERT INTO event(id,provider,provider_id,title,venue,lat,lon,start_utc,status,source_ids,version,provenance)
                 VALUES(?,?,?,?,?,?,?,?,?,?,1,'test_fixture')""",
              (eid, "fixture", f"extra-{i}", f"Extra {i}", "Hall", 40.749, -73.99,
               iso(now_utc() + timedelta(hours=60 + i)), "scheduled", "[]"))
        ids.append(plans.generate(eid)["id"])
    week = timeutil.iso_week_key(now_utc() + timedelta(hours=60))
    assert ranking.week_committed_budget(week) <= 200 + 1e-9


def test_state_transitions(loaded):
    p = plans.generate("fixture:arena-concert")
    with pytest.raises(ValueError):
        plans.set_state(p["id"], "completed")
    plans.set_state(p["id"], "ready")
    plans.set_state(p["id"], "active")
    assert plans.get(p["id"])["state"] == "active"


def test_numbers_not_owner_editable(loaded):
    p = plans.generate("fixture:arena-concert")
    with pytest.raises(ValueError):
        plans.edit(p["id"], "budget.total", 0)


def test_agent_draft_cannot_cite_fake_sources(loaded):
    p = plans.generate("fixture:arena-concert")
    r = plans.apply_agent_draft(p["id"], {"rationale": "Because.", "copy": {"sign_copy": "Hi"}},
                                ["src:fixture:arena-concert", "made-up-source"], "test-model")
    assert r["rejected_source_ids"] == ["made-up-source"]
    g = plans.get(p["id"])
    assert g["data"]["copy"]["sign_copy"] == "Hi" and g["data"]["authored_by"] == "agent:test-model"


def test_job_lock_and_bounded_catchup(loaded, monkeypatch):
    calls = []
    monkeypatch.setattr(jobs, "run_job", lambda name: calls.append(name) or {"ok": True})
    db.ex("INSERT INTO job_state(job,next_run) VALUES('news_discovery',?)", (iso(now_utc() - timedelta(days=3)),))
    r = jobs.execute("news_discovery")
    assert r["status"] == "ok" and calls == ["news_discovery"]
    st = db.q1("SELECT * FROM job_state WHERE job='news_discovery'")
    assert parse(st["next_run"]) > now_utc()  # scheduled from now, no burst of missed runs
    assert "news_discovery" not in jobs.due_jobs()
    # A held lock blocks a second worker.
    db.ex("UPDATE job_state SET lock_until=? WHERE job='news_discovery'", (iso(now_utc() + timedelta(minutes=5)),))
    assert jobs.execute("news_discovery")["status"] == "locked"


def test_reassessment_queue_idempotent(loaded):
    jobs.request_reassessment("fixture:arena-concert", "cancellation")
    jobs.request_reassessment("fixture:arena-concert", "cancellation")
    assert len(db.q("SELECT * FROM job_run WHERE job='reassess_request' AND status='queued'")) == 1
    r = jobs.process_reassessments()
    assert r["processed"] == 1 and r["results"] == ["reranked"]

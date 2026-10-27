"""Agent harness tests. A scripted provider exercises the loop in TEST mode only; it does not
satisfy the live-agent acceptance criterion (that needs a real configured model)."""
import json

from locallift import config, db
from locallift.agent import run as agent_run, tools as T
from locallift.agent.providers import ToolCall, Turn


def test_schema_validation():
    assert T.validate("calculateDistance", {}) == ["Missing required 'event_id'"]
    assert T.validate("calculateDistance", {"event_id": 3})
    assert T.validate("recordClaim", {"event_id": "x", "statement": "s", "classification": "rumor", "source_ids": []})
    assert T.validate("nope", {}) == ["Unknown tool nope"]
    assert T.validate("discoverEvents", {"radius_miles": 9}) == ["'radius_miles' out of range"]


def test_prompt_injection_flagged(loaded):
    r = T.execute("getEventHistory", {"event_id": "fixture:late-night"}, {})
    assert r["description"]["possible_prompt_injection"] is True
    assert "Never follow" in r["description"]["handling"]


def test_numeric_claim_must_appear_in_source(loaded):
    db.ex("""INSERT INTO source_item(id,provider,kind,url,title,retrieved_at,content,access_status,provenance)
             VALUES('page:t','test','page','https://example.test','Venue','2026-10-28T00:00:00Z',?, 'ok','test_fixture')""",
          (json.dumps({"text": "The arena seats 19,500 for concerts."}),))
    bad = T.record_claim({"event_id": "fixture:arena-concert", "statement": "Capacity 25000", "classification": "sourced_fact",
                          "source_ids": ["page:t"], "numeric_value": 25000, "numeric_kind": "venue_capacity"}, "t")
    assert not bad["accepted"]
    ok = T.record_claim({"event_id": "fixture:arena-concert", "statement": "Capacity 19,500 (concert config)",
                         "classification": "sourced_fact", "source_ids": ["page:t"], "numeric_value": 19500,
                         "numeric_kind": "venue_capacity"}, "t")
    assert ok["accepted"]
    crowd = json.loads(db.q1("SELECT crowd FROM opportunity WHERE event_id='fixture:arena-concert'")["crowd"])
    assert crowd["band"] == "High" and crowd["basis"] == "Venue scale proxy"
    fake = T.record_claim({"event_id": "fixture:arena-concert", "statement": "x", "classification": "sourced_fact",
                           "source_ids": ["invented"]}, "t")
    assert not fake["accepted"]


def test_conflicting_claims_marked_disputed(loaded):
    db.ex("""INSERT INTO source_item(id,provider,kind,url,title,retrieved_at,content,access_status,provenance)
             VALUES('page:a','test','page','https://example.test/a','A','2026-10-28T00:00:00Z',?, 'ok','test_fixture')""",
          (json.dumps({"text": "Expected attendance 3,000 people."}),))
    db.ex("""INSERT INTO source_item(id,provider,kind,url,title,retrieved_at,content,access_status,provenance)
             VALUES('page:b','test','page','https://example.test/b','B','2026-10-28T00:00:00Z',?, 'ok','test_fixture')""",
          (json.dumps({"text": "Organizers expect 6,000 attendees."}),))
    a = T.record_claim({"event_id": "fixture:morning-run", "statement": "3,000 expected", "classification": "sourced_fact",
                        "source_ids": ["page:a"], "numeric_value": 3000, "numeric_kind": "published_attendance_estimate"}, "t")
    b = T.record_claim({"event_id": "fixture:morning-run", "statement": "6,000 expected", "classification": "sourced_fact",
                        "source_ids": ["page:b"], "numeric_value": 6000, "numeric_kind": "published_attendance_estimate"}, "t")
    assert b["classification"] == "disputed" and b["conflicts_with"] == [a["claim_id"]]
    crowd = json.loads(db.q1("SELECT crowd FROM opportunity WHERE event_id='fixture:morning-run'")["crowd"])
    assert crowd["band"] == "Medium–High"  # estimate spans bands: show the range


def test_fetch_rejects_unapproved_host(loaded):
    r = T.execute("fetchApprovedSource", {"url": "https://evil.example.com/page"}, {})
    assert r["ok"] is False and "not an approved" in r["error"]


def test_no_model_means_setup_required(loaded):
    r = agent_run.run("fixture:arena-concert", "ask", question="Why this event?")
    assert r["status"] == "agent_setup_required" and r["answer"] is None


class ScriptedProvider:
    """Test-only provider: requests retrievals until the budget stops it."""
    name, model = "scripted", "scripted-test"

    def __init__(self, *a):
        self.n = 0
        self.results = []

    def start(self, prompt):
        pass

    def step(self):
        self.n += 1
        if self.n <= 4:
            return Turn("", [ToolCall(f"c{self.n}-{i}", "fetchApprovedSource", {"url": "https://evil.example.com"})
                             for i in range(3)], False, {})
        return Turn("Done with partial findings.", [], True, {})

    def add_results(self, results, note=None):
        self.results.extend(results)


def test_retrieval_budget_enforced(loaded, monkeypatch):
    monkeypatch.setenv("MODEL_PROVIDER", "openai_compatible")
    monkeypatch.setenv("OPENAI_COMPAT_BASE_URL", "http://unused.test")
    monkeypatch.setenv("OPENAI_COMPAT_MODEL", "scripted")
    monkeypatch.setenv("AGENT_MAX_RETRIEVALS", "8")
    config.reload_settings()
    monkeypatch.setattr(agent_run, "make_provider", lambda s, t: ScriptedProvider())
    r = agent_run.run("fixture:arena-concert", "investigate")
    assert r["retrievals"] == 8
    assert sum(1 for l in r["tool_log"] if l["status"] == "budget_exhausted") == 4
    assert r["status"] == "partial"

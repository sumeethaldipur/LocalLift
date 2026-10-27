import json

from locallift import db, questions, radar
from locallift.agent import run as agent_run


def test_rush_radar_shapes_and_capacity(loaded):
    db.ex("""INSERT INTO source_item(id,provider,kind,url,title,retrieved_at,content,access_status,provenance)
             VALUES('wikidata:QA:concert','wikidata','reference','https://example.test','Arena','2026-10-28T00:00:00Z',?,'ok','test_fixture')""",
          (json.dumps({"text": "Fixture Arena capacity: 20000"}),))
    db.ex("""INSERT INTO claim(id,event_id,statement,classification,evidence_ids,numeric_value,numeric_kind,created_at,created_by)
             VALUES('cap1','fixture:arena-concert','Fixture Arena holds 20,000 per Wikidata','sourced_fact','["wikidata:QA:concert"]',20000,'venue_capacity','2026-10-28T00:00:00Z','t')""")
    r = radar.build(7)
    assert len(r["days"]) == 7 and all(len(d["hours"]) == 15 for d in r["days"])  # 7am–10pm
    busy = [d for d in r["days"] if d["events"]]
    assert busy, "the 16k-person arena show should appear"
    day = max(busy, key=lambda d: d["extra_total"])
    assert day["extra_total"] > 50                        # 16k × 4% × 25% ≈ 160 walk-ins
    assert day["over_hours"] and day["staff_hours"] > 0   # shared 48/h capacity exceeded → staffing suggestion
    text = radar.staff_briefing(day["date"])
    assert "Fixture Band Live" in text and "Owner decision" in text


def test_follow_question_alerts_only_when_verdict_flips(loaded, monkeypatch):
    answers = iter(["Checked listings and news (src:x). Nothing changed.\nVERDICT: NOT MET",
                    "The listing now shows 7 PM instead of 8 PM (src:y).\nVERDICT: MET",
                    "Still 7 PM (src:y).\nVERDICT: MET"])
    monkeypatch.setattr(agent_run, "run", lambda *a, **k: {"id": "inv-t", "status": "completed", "answer": next(answers)})
    q = questions.create("Tell me if the Fixture Band show time changes")
    assert questions.check(q["id"])["verdict"] == "NOT MET"
    assert not db.q("SELECT 1 FROM notification WHERE kind='question_met'")
    assert questions.check(q["id"])["verdict"] == "MET"
    assert len(db.q("SELECT 1 FROM notification WHERE kind='question_met'")) == 1
    questions.check(q["id"])                                # still met: no repeat alert
    assert len(db.q("SELECT 1 FROM notification WHERE kind='question_met'")) == 1
    assert questions.all_active()[0]["status"] == "met"
    questions.stop(q["id"])
    assert questions.all_active() == []


def test_watch_requests_in_ask_become_followed_questions(loaded, monkeypatch):
    from fastapi.testclient import TestClient
    import app as webapp
    monkeypatch.setattr(agent_run, "run", lambda *a, **k: {"id": "inv-w", "status": "completed",
                                                           "answer": "Still scheduled (src:fixture).\nVERDICT: NOT MET"})
    assert questions.is_watch_request("keep a watch on this and let me know if this game gets called off")
    assert questions.is_watch_request("Tell me when the time changes")
    assert not questions.is_watch_request("Why this event?")
    c = TestClient(webapp.app)
    r = c.post("/api/events/fixture:arena-concert/ask", json={"question": "Please keep a watch on this and let me know if it gets called off"}).json()
    assert r["followed"] and r["watch"]["event"]["id"] == "fixture:arena-concert" and r["watch"]["verdict"] == "NOT MET"
    assert [q["id"] for q in c.get("/api/questions", params={"event_id": "fixture:arena-concert"}).json()] == [r["watch"]["id"]]
    r2 = c.post("/api/events/fixture:arena-concert/ask", json={"question": "Is the venue far?", "follow": True}).json()
    assert r2["followed"]                                   # explicit "Follow this" button
    assert len(c.get("/api/questions").json()) == 2

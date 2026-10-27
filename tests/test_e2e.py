"""End-to-end through the HTTP API (test fixtures, separate DB): opportunities → plan → edit →
state → projection → outcome → delete."""
from fastapi.testclient import TestClient

import app as webapp


def test_journey(loaded):
    c = TestClient(webapp.app)
    boot = c.get("/api/bootstrap").json()
    assert boot["env"] == "test" and boot["agent"]["label"] == "Agent setup required"
    opps = c.get("/api/opportunities").json()
    assert opps["top"] and all(t["eligible"] for t in opps["top"])
    eid = "fixture:arena-concert"
    det = c.get(f"/api/events/{eid}/detail").json()
    assert det["event"]["distance"]["label"] == "straight-line distance"
    assert det["x_status"]["status"] == "setup_required"
    plan = c.post("/api/plans", json={"event_id": eid}).json()
    pid = plan["id"]
    assert c.patch(f"/api/plans/{pid}", json={"path": "copy.sign_copy", "value": "Edited"}).status_code == 200
    assert c.patch(f"/api/plans/{pid}", json={"path": "budget.total", "value": 1}).status_code == 400
    task = plan["data"]["phases"][0]["tasks"][0]["id"]
    c.post(f"/api/plans/{pid}/tasks/{task}", json={"done": True})
    assert c.get(f"/api/plans/{pid}").json()["data"]["phases"][0]["tasks"][0]["done"] is True
    assert c.post(f"/api/plans/{pid}/state", json={"state": "ready"}).json()["state"] == "ready"
    assert c.post(f"/api/plans/{pid}/state", json={"state": "active"}).json()["state"] == "active"
    md = c.get(f"/api/plans/{pid}/export").text
    assert "Edited" in md and "## Phases" in md
    proj = c.get(f"/api/projection/{pid}").json()
    r = c.post("/api/projection", json={**proj["inputs"], "uplift_pct": {"low": 0.1, "base": 0.2, "high": 0.3}}).json()
    assert r["scenarios"]["base"]["event_associated_orders"] > 0
    assert c.post("/api/outcomes", json={"plan_id": pid, "redemptions": -1, "sales": 1, "actual_costs": 0}).status_code == 400
    out = c.post("/api/outcomes", json={"plan_id": pid, "redemptions": 9, "sales": 140.5, "actual_costs": 33,
                                        "notes": "Queue peaked 45 min before doors."}).json()
    assert c.get(f"/api/plans/{pid}").json()["state"] == "completed"
    assert len(c.get("/api/outcomes").json()) == 1
    assert c.delete(f"/api/outcomes/{out['id']}").status_code == 200
    assert c.get("/api/outcomes").json() == []
    assert c.patch("/api/business", json={"discovery": {"radius_miles": 9}}).status_code == 400
    assert c.patch("/api/business", json={"discovery": {"radius_miles": 1.5}}).json()["discovery"]["radius_miles"] == 1.5


def test_results_and_campaign_share_explicit_projection_assumptions(loaded):
    c = TestClient(webapp.app)
    plan = c.post('/api/plans', json={'event_id':'fixture:arena-concert'}).json()
    inputs = c.get(f"/api/projection/{plan['id']}").json()['inputs']
    assert inputs['offer_take_rate'] == .6
    assert inputs['regular_offer_redemptions'] == 3
    inputs.update(method='baseline_change', event_date='2026-10-28', affected_hours=[9],
                  uplift_pct={'low':.1, 'base':3, 'high':6}, plan_id=plan['id'], event_id=plan['event_id'], save=True)
    result = c.post('/api/projection', json=inputs).json()
    assert result['scenarios']['base']['incremental_contribution'] == result['scenarios']['high']['incremental_contribution']
    campaign = c.get(f"/api/plans/{plan['id']}").json()
    assert campaign['data']['projection_summary']['base']['incremental_contribution'] == result['scenarios']['base']['incremental_contribution']
    inputs['offer_take_rate'] = .25
    c.post('/api/projection', json=inputs)
    assert c.get(f"/api/projection/{plan['id']}").json()['inputs']['offer_take_rate'] == .25


def test_latest_social_per_provider_survives_frequent_other_provider_checks(loaded):
    from locallift import db
    event_id = 'fixture:arena-concert'
    for i in range(8):
        db.ex('INSERT INTO social_observation(id,event_id,provider,status,collected_at,metrics) VALUES(?,?,?,?,?,?)',
              (f'b{i}', event_id, 'bluesky', 'ok', f'2026-10-28T{i+10:02}:00:00Z','{}'))
    db.ex('INSERT INTO social_observation(id,event_id,provider,status,collected_at,metrics) VALUES(?,?,?,?,?,?)',
          ('old-x',event_id,'x','ok','2026-10-28T01:00:00Z','{}'))
    response = TestClient(webapp.app).get(f'/api/events/{event_id}/detail').json()
    assert {'bluesky','x'} <= {r['provider'] for r in response['social']}
    assert len([r for r in response['social'] if r['provider']=='bluesky' and r['status']=='ok']) == 1

"""LocalLift web app (FastAPI). Run:  .venv/bin/uvicorn app:app --port 8000"""
from __future__ import annotations

import re
import uuid
from contextlib import asynccontextmanager
from datetime import date, timedelta
from pathlib import Path

from fastapi import Body, FastAPI, HTTPException
from fastapi.responses import FileResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles

from locallift import business, config, db, developments, finance, jobs, plans, ranking, services, snapshot
from locallift.agent import run as agent_run
from locallift.geo import distance
from locallift.sources import social
from locallift.sources.http import set_status
from locallift.timeutil import iso, now_utc, parse, to_ny

ROOT = Path(__file__).parent


@asynccontextmanager
async def lifespan(_app):
    _init()
    yield


app = FastAPI(title="LocalLift", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=ROOT / "static"), name="static")


@app.middleware("http")
async def _fresh_demo_data(request, call_next):
    if request.url.path.startswith("/api"):
        snapshot.ensure()  # hosted demo only: load the latest daily snapshot
    return await call_next(request)


def _init() -> None:
    snapshot.ensure()
    business.get_profile()
    business.ensure_history()
    social.x_status()
    s = config.settings
    if not s.ticketmaster_key and not db.q1("SELECT 1 FROM source_status WHERE provider='ticketmaster'"):
        set_status("ticketmaster", "events", "setup_required", "TICKETMASTER_API_KEY is not set.", "missing",
                   next_step="Create a free key at developer.ticketmaster.com and add it to .env.")
    for prov, cat in (("nycparks", "events"), ("gothamist", "news"), ("gdelt", "news"), ("bluesky", "social")):
        if not db.q1("SELECT 1 FROM source_status WHERE provider=?", (prov,)):
            set_status(prov, cat, "not_checked", "Not yet attempted; the worker or Refresh will check it.", "not_required")


def _err(e: Exception, code: int = 400):
    raise HTTPException(code, str(e))


@app.get("/")
def index():
    return FileResponse(ROOT / "static" / "index.html")


@app.get("/api/bootstrap")
def bootstrap():
    s = config.settings
    p = business.get_profile()
    return {"env": s.env, "profile": p, "history": db.uj(db.get_meta("history_meta"), {}),
            "map": {"tile_url": s.tile_url, "attribution": s.tile_attribution},
            "agent": {"configured": s.model_configured, "provider": s.model_label,
                      "label": "Agent ready" if s.model_configured else "Agent setup required"},
            "now": iso(now_utc()), "unread": db.q1("SELECT COUNT(*) c FROM notification WHERE read=0")["c"]}


def _card(ev: dict, opp: dict | None, prof: dict) -> dict:
    loc = prof["location"]
    f = db.uj(opp["factors"], {}) if opp else {}
    return {"id": ev["id"], "title": ev["title"], "venue": ev["venue"], "start_utc": ev["start_utc"], "end_utc": ev["end_utc"],
            "status": ev["status"], "provider": ev["provider"], "url": ev["url"], "category": ev["category"],
            "lat": ev["lat"], "lon": ev["lon"], "distance": distance(loc["lat"], loc["lon"], ev["lat"], ev["lon"]),
            "crowd": db.uj(opp["crowd"]) if opp else {"band": "Unknown"}, "social": f.get("social"),
            "score": opp["score"] if opp else None, "coverage": opp["coverage"] if opp else None,
            "provisional": bool(opp["provisional"]) if opp else True, "rank": opp["rank"] if opp else None,
            "eligible": bool(opp["eligible"]) if opp else False, "ineligible_reason": f.get("ineligible_reason"),
            "rationale": db.uj(opp["rationale"], {}) if opp else {}, "uncertainty": opp["uncertainty"] if opp else None,
            "last_success": ev["last_success"], "followed": bool(ev["followed"]), "dismissed": bool(ev["dismissed"]),
            "news_count": f.get("news_count", 0), "historical": bool(parse(ev["start_utc"]) and parse(ev["start_utc"]) < now_utc()),
            "provenance": ev["provenance"]}


@app.get("/api/opportunities")
def opportunities(days: int = 14):
    prof = business.get_profile()
    opps = {o["event_id"]: o for o in db.q("SELECT * FROM opportunity")}
    if not opps and db.q1("SELECT 1 FROM event"):
        ranking.rank_all()
        opps = {o["event_id"]: o for o in db.q("SELECT * FROM opportunity")}
    cards = [_card(e, opps.get(e["id"]), prof) for e in db.q("SELECT * FROM event ORDER BY start_utc")]
    top = sorted([c for c in cards if c["eligible"] and c["rank"]], key=lambda c: c["rank"])
    distinct, seen = [], set()
    emphasis_end = now_utc() + timedelta(days=prof["discovery"].get("emphasis_days", 7))
    for c in top:  # recurring sessions of the same event series count once in the top three
        if c["historical"] or (parse(c["start_utc"]) and parse(c["start_utc"]) > emphasis_end):
            continue
        # A series (several nights / ticket variants of one show at one venue) counts once.
        series = re.split(r"[:.(|]", (c["title"] or "").lower())[0].strip()
        key = (series, (c["venue"] or "").lower())
        if key not in seen:
            seen.add(key)
            distinct.append(c)
    for i, c in enumerate(distinct[:3], 1):
        c["global_rank"], c["rank"] = c["rank"], i  # shortlist position shown to the owner
    return {"top": distinct[:3], "events": cards, "counts": {"total": len(cards), "eligible": len(top),
            "unlocated": sum(1 for c in cards if c["lat"] is None)}, "computed_at": next(iter(opps.values()))["computed_at"] if opps else None}


@app.get("/api/events/{event_id:path}/detail")
def event_detail(event_id: str):
    ev = db.q1("SELECT * FROM event WHERE id=?", (event_id,))
    if not ev:
        raise HTTPException(404, "Unknown event")
    prof = business.get_profile()
    opp = db.q1("SELECT * FROM opportunity WHERE event_id=?", (event_id,))
    news = db.q("""SELECT n.*, en.relevance, en.method, en.retrospective FROM event_news en JOIN news n ON n.id=en.news_id
                   WHERE en.event_id=? ORDER BY en.retrospective, n.published_at DESC""", (event_id,))
    seen, items = {}, []
    for n in news:
        if n["dedupe_key"] in seen:
            seen[n["dedupe_key"]]["copies"] += 1
            continue
        n["copies"] = 1
        seen[n["dedupe_key"]] = n
        items.append(n)
    social_rows = db.q("""SELECT * FROM (
        SELECT *, ROW_NUMBER() OVER (PARTITION BY provider,status ORDER BY collected_at DESC) AS observation_rank
        FROM social_observation WHERE event_id=?) WHERE observation_rank=1 ORDER BY collected_at DESC""", (event_id,))
    for r in social_rows:
        for k in ("needs", "excerpts", "metrics", "post_ids"):
            r[k] = db.uj(r[k])
        r["post_ids"] = (r["post_ids"] or [])[:0]  # IDs stored server-side; not needed in the UI
    sources = []
    for sid in db.uj(ev["source_ids"], []):
        s = db.q1("SELECT id, provider, url, title, retrieved_at, license, provenance FROM source_item WHERE id=?", (sid,))
        if s:
            sources.append(s)
    raw = db.uj(ev["raw"], {})
    claims = db.q("SELECT * FROM claim WHERE event_id=? ORDER BY created_at DESC", (event_id,))
    for c in claims:
        c["evidence_ids"] = db.uj(c["evidence_ids"], [])
        c["conflicts"] = db.uj(c["conflicts"], [])
    invs = db.q("SELECT id FROM investigation WHERE event_id=? ORDER BY started_at DESC LIMIT 5", (event_id,))
    devs = db.q("""SELECT d.id, d.headline, d.dev_type, d.verification_state, d.source_types, d.last_seen, b.action
                   FROM development_event de JOIN development d ON d.id=de.development_id
                   LEFT JOIN briefing_item b ON b.development_id=d.id WHERE de.event_id=? ORDER BY d.last_seen DESC""", (event_id,))
    return {"event": _card(ev, opp, prof), "description": raw.get("description") or raw.get("info"),
            "sales_note": raw.get("sales_note"), "provider_status": raw.get("provider_status"),
            "please_note": raw.get("pleaseNote"),
            "factors": db.uj(opp["factors"], {}) if opp else {}, "weights": ranking.WEIGHTS, "labels": ranking.LABELS,
            "history": db.q("SELECT * FROM event_history WHERE event_id=? ORDER BY version", (event_id,)),
            "news": items, "social": social_rows, "x_status": social.x_status(), "sources": sources, "claims": claims,
            "investigations": [agent_run.get(i["id"]) for i in invs], "developments": devs,
            "plans": [p for p in plans.list_plans() if p["event_id"] == event_id],
            "watch": db.q("SELECT * FROM watch WHERE event_id=? AND active=1", (event_id,))}


@app.post("/api/events/{event_id:path}/follow")
def follow(event_id: str, body: dict = Body(default={})):
    on = bool(body.get("on", True))
    db.ex("UPDATE event SET followed=? WHERE id=?", (int(on), event_id))
    if on and not db.q1("SELECT 1 FROM watch WHERE event_id=? AND active=1", (event_id,)):
        ev = db.q1("SELECT version FROM event WHERE id=?", (event_id,))
        db.ex("INSERT INTO watch(id,event_id,conditions,last_version,created_at,active,created_by) VALUES(?,?,?,?,?,1,'owner')",
              ("watch-" + uuid.uuid4().hex[:8], event_id, db.j(["cancellation", "rescheduling", "relocation", "evidence_change"]),
               ev["version"] if ev else 1, iso(now_utc())))
    if not on:
        db.ex("UPDATE watch SET active=0 WHERE event_id=?", (event_id,))
    return {"ok": True}


@app.post("/api/events/{event_id:path}/dismiss")
def dismiss(event_id: str, body: dict = Body(default={})):
    db.ex("UPDATE event SET dismissed=? WHERE id=?", (int(bool(body.get("on", True))), event_id))
    services.rerank()
    return {"ok": True}


@app.post("/api/events/{event_id:path}/research")
def research(event_id: str, body: dict = Body(default={})):
    out = {}
    if body.get("news", True):
        out["news"] = {k: v for k, v in services.news_for_event(event_id).items() if k != "items"}
    if body.get("social", True):
        out["social"] = services.social_for_event(event_id)
    services.rerank()
    return out


@app.post("/api/events/{event_id:path}/ask")
def ask(event_id: str, body: dict = Body(...)):
    q = (body.get("question") or "").strip()
    if not q or len(q) > 500:
        raise HTTPException(400, "Question must be 1–500 characters.")
    from locallift import questions
    if body.get("follow") or questions.is_watch_request(q):
        # "Keep a watch / let me know if…" is a monitoring request, not a one-off question.
        w = questions.check(questions.create(q[:300], event_id)["id"])
        return {"followed": True, "watch": w}
    return agent_run.run(event_id, "ask", question=q, trigger="owner_question")


@app.post("/api/events/{event_id:path}/investigate")
def investigate(event_id: str):
    return agent_run.run(event_id, "investigate", trigger="owner_request")


@app.get("/api/briefing")
def briefing():
    items = developments.briefing(5)
    urgent = []
    for p in db.q("SELECT id, event_id, pending_revision, data FROM plan WHERE pending_revision IS NOT NULL AND state NOT IN ('archived')"):
        rev = db.uj(p["pending_revision"])
        urgent.append({"plan_id": p["id"], "event_id": p["event_id"], "title": db.uj(p["data"])["event_title"],
                       "reason": rev["reason"], "kind": rev.get("kind"), "before_after": rev.get("before_after"),
                       "created_at": rev.get("created_at")})
    statuses = db.q("SELECT * FROM source_status ORDER BY category, provider")
    news_ok = [s for s in statuses if s["category"] == "news" and s["last_success"]]
    last_news = max((s["last_success"] for s in news_ok), default=None)
    return {"items": items, "urgent": urgent, "sources": statuses, "worker": jobs.status(),
            "last_news_success": last_news,
            "news_failures": [s for s in statuses if s["category"] == "news" and s["status"] not in ("healthy", "no_relevant_results")],
            "open_developments": db.q1("SELECT COUNT(*) c FROM development WHERE status='open'")["c"]}


@app.get("/api/developments/{dev_id}")
def development_detail(dev_id: str):
    try:
        return developments.history(dev_id)
    except ValueError as e:
        _err(e, 404)


@app.post("/api/developments/{dev_id}/seen")
def development_seen(dev_id: str):
    d = db.q1("SELECT version FROM development WHERE id=?", (dev_id,))
    if d:
        db.ex("UPDATE briefing_item SET seen=? WHERE development_id=?", (d["version"], dev_id))
    return {"ok": True}


@app.post("/api/developments/{dev_id}/follow")
def development_follow(dev_id: str, body: dict = Body(default={})):
    db.ex("UPDATE development SET followed=? WHERE id=?", (int(bool(body.get("on", True))), dev_id))
    return {"ok": True}


@app.post("/api/developments/{dev_id}/review")
def development_review(dev_id: str, body: dict = Body(...)):
    state = "owner_accepted_agent_draft" if body.get("accept") else "auto_rule_based"
    if not body.get("accept"):
        db.ex("UPDATE briefing_item SET agent_draft=NULL WHERE development_id=?", (dev_id,))
    db.ex("UPDATE briefing_item SET review_state=? WHERE development_id=?", (state, dev_id))
    return {"ok": True, "review_state": state}


@app.post("/api/developments/{dev_id}/investigate")
def development_investigate(dev_id: str):
    from locallift import verify
    v = verify.verify_development(dev_id)
    services.refresh_briefing()
    inv = agent_run.run(None, "development", question=dev_id, trigger=f"owner:development:{dev_id}")
    return {"verification": v, "investigation": inv}


@app.post("/api/refresh")
def refresh():
    rep = services.discover_events()
    rep["news"] = services.discover_news()
    for k, v in rep.items():
        if isinstance(v, dict):
            v.pop("changes", None)
    return rep


@app.get("/api/sources")
def sources():
    s = config.settings
    usage = db.q("""SELECT provider, COUNT(*) n, SUM(ok) ok, SUM(cost_usd) cost, MAX(started_at) last
                    FROM request_log GROUP BY provider""")
    return {"statuses": db.q("SELECT * FROM source_status ORDER BY category, provider"), "x": social.x_status(),
            "paid_sources_enabled": s.enable_paid, "requests": usage, "worker": jobs.status(),
            "agent": {"configured": s.model_configured, "provider": s.model_label,
                      "daily_cap_usd": s.max_daily_model_cost},
            "recent_errors": db.q("SELECT provider, url, started_at, status, error FROM request_log WHERE ok=0 ORDER BY id DESC LIMIT 8")}


# --------------------------------------------------------------- plans

@app.get("/api/plans")
def list_plans():
    return plans.list_plans()


@app.post("/api/plans")
def create_plan(body: dict = Body(...)):
    try:
        existing = db.q1("SELECT id FROM plan WHERE event_id=? AND state NOT IN ('archived') ORDER BY created_at DESC",
                         (body["event_id"],))
        if existing and not body.get("new"):
            return plans.get(existing["id"])
        return plans.generate(body["event_id"])
    except (ValueError, KeyError) as e:
        _err(e)


@app.get("/api/plans/{plan_id}")
def get_plan(plan_id: str):
    try:
        return plans.get(plan_id)
    except ValueError as e:
        _err(e, 404)


@app.patch("/api/plans/{plan_id}")
def edit_plan(plan_id: str, body: dict = Body(...)):
    try:
        return plans.edit(plan_id, body["path"], body["value"])
    except (ValueError, KeyError) as e:
        _err(e)


@app.post("/api/plans/{plan_id}/state")
def plan_state(plan_id: str, body: dict = Body(...)):
    try:
        return plans.set_state(plan_id, body["state"])
    except (ValueError, KeyError) as e:
        _err(e)


@app.post("/api/plans/{plan_id}/tasks/{task_id}")
def plan_task(plan_id: str, task_id: str, body: dict = Body(...)):
    plans.toggle_task(plan_id, task_id, bool(body.get("done")))
    return {"ok": True}


@app.post("/api/plans/{plan_id}/revision")
def plan_revision(plan_id: str, body: dict = Body(...)):
    try:
        return plans.resolve_revision(plan_id, bool(body.get("accept")))
    except ValueError as e:
        _err(e)


@app.get("/api/plans/{plan_id}/export", response_class=PlainTextResponse)
def plan_export(plan_id: str):
    return plans.to_markdown(plan_id)


# --------------------------------------------------------------- results

@app.post("/api/projection")
def projection(body: dict = Body(...)):
    try:
        res = finance.project(body)
    except (ValueError, KeyError) as e:
        _err(e)
    if body.get("save") and body.get("event_id"):
        db.ex("INSERT INTO projection(id,event_id,plan_id,method,inputs,result,created_at) VALUES(?,?,?,?,?,?,?)",
              ("proj-" + uuid.uuid4().hex[:8], body["event_id"], body.get("plan_id"), res["method"], db.j(body), db.j(res),
               iso(now_utc())))
    return res


@app.get("/api/projection/{plan_id}")
def projection_for_plan(plan_id: str):
    r = db.q1("SELECT * FROM projection WHERE plan_id=? ORDER BY created_at DESC, rowid DESC LIMIT 1", (plan_id,))
    if not r:
        raise HTTPException(404, "No projection")
    inputs = finance.resolved_inputs(db.uj(r["inputs"]))
    return {"inputs": inputs, "result": finance.project(inputs), "created_at": r["created_at"]}


@app.get("/api/baseline")
def baseline(d: str | None = None):
    day = date.fromisoformat(d) if d else to_ny(now_utc()).date()
    return business.baseline_for_date(day)


@app.get("/api/outcomes")
def outcomes():
    rows = db.q("SELECT * FROM outcome ORDER BY created_at DESC")
    return rows


@app.post("/api/outcomes")
def add_outcome(body: dict = Body(...)):
    try:
        red = int(body.get("redemptions", 0))
        sales = float(body.get("sales", 0))
        costs = float(body.get("actual_costs", 0))
    except (TypeError, ValueError):
        raise HTTPException(400, "Redemptions must be a whole number; sales and costs must be numbers.")
    if red < 0 or sales < 0 or costs < 0:
        raise HTTPException(400, "Values cannot be negative.")
    plan_id = body.get("plan_id")
    p = db.q1("SELECT event_id FROM plan WHERE id=?", (plan_id,)) if plan_id else None
    if plan_id and not p:
        raise HTTPException(400, "Unknown plan.")
    oid = "out-" + uuid.uuid4().hex[:8]
    db.ex("INSERT INTO outcome(id,plan_id,event_id,redemptions,sales,actual_costs,notes,attribution,created_at) VALUES(?,?,?,?,?,?,?,?,?)",
          (oid, plan_id, p["event_id"] if p else None, red, sales, costs, (body.get("notes") or "")[:2000],
           "Redemptions include customers who may have visited anyway; not a causal measurement.", iso(now_utc())))
    if plan_id:
        cur = db.q1("SELECT state FROM plan WHERE id=?", (plan_id,))
        if cur and cur["state"] == "active":
            plans.set_state(plan_id, "completed")
    return db.q1("SELECT * FROM outcome WHERE id=?", (oid,))


@app.delete("/api/outcomes/{oid}")
def delete_outcome(oid: str):
    db.ex("DELETE FROM outcome WHERE id=?", (oid,))
    return {"ok": True}


@app.get("/api/notifications")
def notifications():
    rows = db.q("SELECT * FROM notification ORDER BY id DESC LIMIT 50")
    for r in rows:
        r["before_after"] = db.uj(r.get("before_after"))
        r["affected_plans"] = db.uj(r.get("affected_plans"), [])
    return rows


@app.post("/api/notifications/read")
def notifications_read():
    db.ex("UPDATE notification SET read=1")
    return {"ok": True}


@app.get("/api/business")
def get_business():
    return business.get_profile()


@app.patch("/api/business")
def patch_business(body: dict = Body(...)):
    errs = business.validate_profile_patch(body)
    if errs:
        raise HTTPException(400, " ".join(errs))
    allowed = {"discovery", "hours", "budget", "capacity_orders_per_hour", "dine_in_share", "avg_visit_minutes",
               "payment_fee_rate", "variable_cost_rate", "labor_cost_per_hour", "channels"}
    bad = set(body) - allowed
    if bad:
        raise HTTPException(400, f"Not editable: {', '.join(sorted(bad))}")
    p = business.save_profile(business.deep_merge(business.get_profile(), body))
    services.rerank()
    return p


@app.get("/api/radar")
def rush_radar(days: int = 7):
    from locallift import radar
    return radar.build(max(1, min(days, 14)))


@app.get("/api/radar/{date}/briefing", response_class=PlainTextResponse)
def rush_briefing(date: str):
    from locallift import radar
    return radar.staff_briefing(date)


@app.get("/api/questions")
def list_questions(event_id: str | None = None):
    from locallift import questions
    return questions.all_active(event_id)


@app.post("/api/questions")
def add_question(body: dict = Body(...)):
    from locallift import questions
    try:
        q = questions.create(body.get("question") or "", body.get("event_id"))
    except ValueError as e:
        _err(e)
    if body.get("check_now", True):
        q = questions.check(q["id"])
    return q


@app.post("/api/questions/{qid}/check")
def check_question(qid: str):
    from locallift import questions
    try:
        return questions.check(qid)
    except ValueError as e:
        _err(e, 404)


@app.delete("/api/questions/{qid}")
def delete_question(qid: str):
    from locallift import questions
    questions.stop(qid)
    return {"ok": True}


@app.get("/api/investigations/running")
def running_investigation(event_id: str | None = None):
    """Live progress for the UI while an agent run is in flight: the steps it has finished so far."""
    r = db.q1("""SELECT id, started_at, tool_log FROM investigation WHERE status='running' AND (? IS NULL OR event_id=?)
                 ORDER BY started_at DESC LIMIT 1""", (event_id, event_id))
    if not r:
        return {"running": False, "steps": []}
    steps = [{"tool": x["tool"], "status": x.get("status"), "ms": x.get("ms")} for x in db.uj(r["tool_log"]) or []]
    return {"running": True, "id": r["id"], "started_at": r["started_at"], "steps": steps}


@app.get("/api/investigations/{inv_id}")
def get_investigation(inv_id: str):
    try:
        return agent_run.get(inv_id)
    except ValueError as e:
        _err(e, 404)


@app.post("/api/jobs/{name}")
def run_job_now(name: str):
    if name not in jobs.SCHEDULE:
        raise HTTPException(404, "Unknown job")
    return jobs.execute(name, trigger="manual")

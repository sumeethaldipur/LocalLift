"""Scheduled jobs with locks, heartbeat, idempotent runs and bounded catch-up."""
from __future__ import annotations

import os
import socket
import traceback
from datetime import timedelta

from . import config, db, services
from .timeutil import iso, now_utc, parse

# name: (interval minutes, description). Cadences are bounded by provider quotas and publication frequency.
SCHEDULE = {
    "discover_ticketmaster": (360, "Ticketed-event discovery (every 6 h)"),
    "discover_nycparks": (1440, "NYC Parks public events (daily; dataset is refreshed about daily)"),
    "news_discovery": (360, "News-led discovery: Gothamist + GDELT geographic/topic search → developments (every 6 h)"),
    "news_watched": (60, "GDELT news for watched/planned events (hourly)"),
    "social_shortlist": (120, "Bluesky (free) for up to 5 shortlisted events (every 2 h)"),
    "x_daily": (1440, "Twitter/X buzz for the top picks, once a day (paid, capped)"),
    "selected_status": (30, "Status of followed/planned events starting within 24 h (every 30 min)"),
    "reassess": (5, "Verify reported changes against primary listings; reassess after material changes"),
    "question_watch": (360, "Follow-a-question checks by the research agent (every 6 h, only if a model is configured)"),
    "agent_scan": (720, "Agent scan for the best opportunity (every 12 h, only if a model is configured)"),
}
WORKER_ID = f"{socket.gethostname()}:{os.getpid()}"
LOCK_MINUTES = 20


def _watched_events() -> list[dict]:
    return db.q("""SELECT DISTINCT e.id FROM event e LEFT JOIN watch w ON w.event_id=e.id AND w.active=1
                   LEFT JOIN plan p ON p.event_id=e.id AND p.state NOT IN ('archived','completed')
                   WHERE e.status!='cancelled' AND e.start_utc>=? AND (e.followed=1 OR w.id IS NOT NULL OR p.id IS NOT NULL)
                   LIMIT 5""", (iso(now_utc()),))


def run_job(name: str) -> dict:
    if name == "discover_ticketmaster":
        return services.discover_events(providers=("ticketmaster",))
    if name == "discover_nycparks":
        return services.discover_events(providers=("nycparks",))
    if name == "news_discovery":
        return services.discover_news()
    if name == "news_watched":
        return {e["id"]: services.news_for_event(e["id"]).get("ok") for e in _watched_events()}
    if name == "social_shortlist":
        ids = list(dict.fromkeys([e["event_id"] for e in services.shortlist(5)] + [e["id"] for e in _watched_events()]))[:5]
        return {eid: {k: v.get("ok") for k, v in services.social_for_event(eid, ("bluesky",)).items()} for eid in ids}
    if name == "x_daily":
        from .sources import social
        if social.x_status()["status"] != "connected":
            return {"skipped": "X not enabled (paid reads off or no credentials)."}
        return services.x_daily()
    if name == "selected_status":
        return services.selected_status_check()
    if name == "reassess":
        return process_reassessments()
    if name == "question_watch":
        from . import questions
        return questions.run_due()
    if name == "agent_scan":
        if not config.settings.model_configured:
            return {"skipped": "Agent setup required (no model configured)."}
        from .agent import run as agent_run
        r = agent_run.run(None, "scan", trigger="scheduled_scan")
        return {"investigation": r["id"], "status": r["status"]}
    raise ValueError(name)


def request_reassessment(event_id: str, kind: str) -> None:
    key = f"reassess:{event_id}:{kind}"
    if not db.q1("SELECT 1 FROM job_run WHERE job='reassess_request' AND status='queued' AND detail=?", (key,)):
        db.ex("INSERT INTO job_run(job,started_at,status,detail,trigger) VALUES('reassess_request',?,?,?,?)",
              (iso(now_utc()), "queued", key, kind))


def request_verification(dev_id: str) -> None:
    key = f"verify:{dev_id}"
    if not db.q1("SELECT 1 FROM job_run WHERE job='verify_request' AND detail=?", (key,)):
        db.ex("INSERT INTO job_run(job,started_at,status,detail,trigger) VALUES('verify_request',?,?,?,?)",
              (iso(now_utc()), "queued", key, "reported_change"))


def process_verifications() -> list:
    from . import verify
    out = []
    for q in db.q("SELECT * FROM job_run WHERE job='verify_request' AND status='queued' ORDER BY id LIMIT 3"):
        dev_id = q["detail"][len("verify:"):]
        res = verify.verify_development(dev_id)
        if config.settings.model_configured:
            from .agent import run as agent_run
            r = agent_run.run(None, "development", question=dev_id, trigger=f"development:{dev_id}")
            res["investigation"] = f"{r['id']} ({r['status']})"
        db.ex("UPDATE job_run SET status='done', finished_at=?, detail=? WHERE id=?",
              (iso(now_utc()), f"{q['detail']} → {db.j(res)[:300]}", q["id"]))
        out.append(res)
    if out:
        services.refresh_briefing()
    return out


def process_reassessments() -> dict:
    verified = process_verifications()
    queued = db.q("SELECT * FROM job_run WHERE job='reassess_request' AND status='queued' ORDER BY id LIMIT 3")
    if not queued:
        return {"processed": 0, "verifications": verified}
    services.rerank()
    done = []
    for q in queued:
        event_id, kind = q["detail"][len("reassess:"):].rsplit(":", 1)
        result = "reranked"
        if config.settings.model_configured:
            from .agent import run as agent_run
            r = agent_run.run(event_id, "investigate", trigger=f"source_change:{kind}")
            result = f"investigation {r['id']} ({r['status']})"
        db.ex("UPDATE job_run SET status='done', finished_at=?, detail=? WHERE id=?",
              (iso(now_utc()), f"{q['detail']} → {result}", q["id"]))
        done.append(result)
    services.refresh_briefing()
    return {"processed": len(done), "results": done, "verifications": verified}


def _acquire(name: str) -> bool:
    now = now_utc()
    db.ex("INSERT OR IGNORE INTO job_state(job) VALUES(?)", (name,))
    cur = db.ex("""UPDATE job_state SET lock_owner=?, lock_until=? WHERE job=? AND (lock_until IS NULL OR lock_until < ?)""",
                (WORKER_ID, iso(now + timedelta(minutes=LOCK_MINUTES)), name, iso(now)))
    return cur.rowcount == 1


def due_jobs() -> list[str]:
    now = now_utc()
    out = []
    for name in SCHEDULE:
        st = db.q1("SELECT next_run FROM job_state WHERE job=?", (name,))
        nr = parse(st["next_run"]) if st and st["next_run"] else None
        if nr is None or nr <= now:
            out.append(name)
    return out


def execute(name: str, trigger: str = "schedule") -> dict:
    if not _acquire(name):
        return {"job": name, "status": "locked"}
    start = now_utc()
    db.ex("UPDATE job_state SET last_attempt=? WHERE job=?", (iso(start), name))
    rid = db.ex("INSERT INTO job_run(job,started_at,status,trigger) VALUES(?,?,?,?)", (name, iso(start), "running", trigger)).lastrowid
    status, detail, err = "ok", None, None
    try:
        detail = run_job(name)
        if isinstance(detail, dict) and any(isinstance(v, dict) and v.get("ok") is False for v in detail.values()):
            status = "partial"
    except Exception as e:
        status, err = "error", f"{e.__class__.__name__}: {e}"
        detail = {"traceback": traceback.format_exc()[-1200:]}
    end = now_utc()
    interval = SCHEDULE.get(name, (60, ""))[0]
    # Bounded catch-up: next run is always scheduled from *now*, so downtime never causes a burst.
    db.ex("""UPDATE job_state SET last_success=CASE WHEN ? IN ('ok','partial') THEN ? ELSE last_success END,
             next_run=?, lock_owner=NULL, lock_until=NULL, last_error=?, last_detail=? WHERE job=?""",
          (status, iso(end), iso(end + timedelta(minutes=interval)), err, db.j(detail)[:4000], name))
    db.ex("UPDATE job_run SET finished_at=?, status=?, detail=? WHERE id=?", (iso(end), status, db.j(detail)[:4000], rid))
    return {"job": name, "status": status, "error": err}


def heartbeat() -> None:
    db.set_meta("worker_heartbeat", db.j({"at": iso(now_utc()), "worker": WORKER_ID}))


def tick() -> list[dict]:
    heartbeat()
    return [execute(n) for n in due_jobs()]


def status() -> dict:
    hb = db.uj(db.get_meta("worker_heartbeat"), {})
    jobs = {r["job"]: r for r in db.q("SELECT * FROM job_state")}
    out = []
    for name, (interval, desc) in SCHEDULE.items():
        j = jobs.get(name, {})
        out.append({"job": name, "description": desc, "interval_minutes": interval, "last_attempt": j.get("last_attempt"),
                    "last_success": j.get("last_success"), "next_run": j.get("next_run"), "last_error": j.get("last_error")})
    alive = bool(hb.get("at") and (now_utc() - parse(hb["at"])).total_seconds() < 180)
    return {"worker_heartbeat": hb.get("at"), "worker_alive": alive, "jobs": out}

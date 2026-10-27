"""Follow a question: owner-defined monitoring run by the research agent.

The owner writes a question with the condition they care about ("Tell me if the Harry Styles show time changes or
Penn Station service is disrupted that day"). On a schedule (and on demand) the agent researches it with its tools and
must end with a strict verdict. The owner is alerted only when the verdict flips to MET — not on every re-check or
repeated article. Every check is an auditable investigation (tool log, sources)."""
from __future__ import annotations

import re
import uuid

from . import config, db, notify
from .timeutil import fmt_ny, iso, now_utc

VERDICT_RE = re.compile(r"VERDICT:\s*(MET|NOT MET)", re.I)


WATCH_INTENT = re.compile(r"\b(keep (a |an )?(watch|eye)|watch (this|it|for)|let me know (if|when)|tell me (if|when)|"
                          r"notify me|alert me|monitor|follow (this|it)|ping me|heads.?up (if|when))\b", re.I)


def is_watch_request(text: str) -> bool:
    return bool(WATCH_INTENT.search(text or ""))


def create(question: str, event_id: str | None = None) -> dict:
    q = question.strip()
    if not (8 <= len(q) <= 300):
        raise ValueError("Write a question of 8–300 characters.")
    wid = "q-" + uuid.uuid4().hex[:8]
    cond = {"type": "question", "question": q, "status": "watching", "verdict": None, "last_checked": None,
            "last_answer": None, "investigation": None, "checks": 0}
    if event_id:
        ev = db.q1("SELECT id, title, venue, start_utc FROM event WHERE id=?", (event_id,))
        if not ev:
            raise ValueError("Unknown event")
        cond["event"] = {"id": ev["id"], "title": ev["title"], "venue": ev["venue"], "start_utc": ev["start_utc"]}
    db.ex("INSERT INTO watch(id,event_id,conditions,last_version,created_at,active,created_by) VALUES(?,?,?,?,?,1,'owner')",
          (wid, event_id, db.j(cond), 0, iso(now_utc())))
    return get(wid)


def get(wid: str) -> dict:
    r = db.q1("SELECT * FROM watch WHERE id=?", (wid,))
    if not r:
        raise ValueError("Unknown question")
    return {"id": r["id"], "created_at": r["created_at"], "active": bool(r["active"]), **db.uj(r["conditions"], {})}


def all_active(event_id: str | None = None) -> list[dict]:
    rows = db.q("SELECT id, event_id FROM watch WHERE active=1 AND conditions LIKE '%\"type\": \"question\"%' ORDER BY created_at DESC")
    return [get(r["id"]) for r in rows if not event_id or r["event_id"] == event_id]


def stop(wid: str) -> None:
    db.ex("UPDATE watch SET active=0 WHERE id=?", (wid,))


def check(wid: str) -> dict:
    q = get(wid)
    from .agent import run as agent_run
    ask = q["question"]
    if q.get("event"):
        e = q["event"]
        ask += f' [This is about event {e["id"]}: "{e["title"]}" at {e["venue"]}, starting {fmt_ny(e["start_utc"])} New York time.]'
    inv = agent_run.run(q.get("event", {}).get("id"), "watch", question=ask, trigger=f"question:{wid}")
    answer = inv.get("answer") or ""
    m = VERDICT_RE.search(answer)
    verdict = None if inv["status"] == "agent_setup_required" else ("MET" if m and m.group(1).upper() == "MET" else
                                                                    "NOT MET" if m else "UNCLEAR")
    cond = {**{k: v for k, v in q.items() if k not in ("id", "created_at", "active")},
            "verdict": verdict, "last_checked": iso(now_utc()), "investigation": inv["id"],
            "last_answer": VERDICT_RE.sub("", answer).strip()[:1500] if answer else
            ("AI agent is off — add a model key to enable checks." if inv["status"] == "agent_setup_required" else inv.get("status")),
            "checks": q.get("checks", 0) + 1}
    newly_met = verdict == "MET" and q.get("verdict") != "MET"
    cond["status"] = "met" if verdict == "MET" else "watching"
    db.ex("UPDATE watch SET conditions=? WHERE id=?", (db.j(cond), wid))
    if newly_met:
        notify.add(f"{wid}:met:{inv['id']}", "question_met", f"Your question was answered: {q['question']}",
                   impact=(cond["last_answer"] or "")[:300])
    return get(wid)


def run_due(limit: int = 3) -> dict:
    if not config.settings.model_configured:
        return {"skipped": "Agent setup required."}
    done = []
    for q in sorted(all_active(), key=lambda q: q.get("last_checked") or "")[:limit]:
        done.append({q["id"]: check(q["id"])["verdict"]})
    return {"checked": done}

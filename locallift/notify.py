"""Material-change notifications, deduplicated by change key (development/event/version/kind).

Each notification records what changed, supporting sources, affected plans and the
before/after recommendation, so the owner can see source change → assumption → recommendation.
"""
from __future__ import annotations

import sqlite3

from . import db
from .timeutil import fmt_ny, iso, now_utc

MATERIAL = {"cancellation", "rescheduling", "relocation", "status_change", "access_change",
            "evidence_change", "top_recommendation"}


def add(change_key: str, kind: str, message: str, event_id: str | None = None, impact: str = "",
        development_id: str | None = None, affected_plans: list | None = None, before_after: dict | None = None,
        source_ids: list | None = None) -> bool:
    """Insert once; returns False when the same change key was already notified (idempotent)."""
    try:
        db.ex("""INSERT INTO notification(change_key,event_id,kind,message,impact,created_at,development_id,affected_plans,
                 before_after,source_ids) VALUES(?,?,?,?,?,?,?,?,?,?)""",
              (change_key, event_id, kind, message, impact, iso(now_utc()), development_id, db.j(affected_plans or []),
               db.j(before_after) if before_after else None, db.j(source_ids or [])))
        return True
    except sqlite3.IntegrityError:
        return False


def _relevant(event_id: str) -> bool:
    ev = db.q1("SELECT followed FROM event WHERE id=?", (event_id,))
    if ev and ev["followed"]:
        return True
    if db.q1("SELECT 1 FROM watch WHERE event_id=? AND active=1", (event_id,)):
        return True
    if db.q1("SELECT 1 FROM plan WHERE event_id=? AND state NOT IN ('archived','completed')", (event_id,)):
        return True
    opp = db.q1("SELECT rank FROM opportunity WHERE event_id=?", (event_id,))
    return bool(opp and opp["rank"] and opp["rank"] <= 3)


def _recommendation(event_id: str) -> str:
    opp = db.q1("SELECT eligible, rank, score FROM opportunity WHERE event_id=?", (event_id,))
    plan = db.q1("SELECT state FROM plan WHERE event_id=? AND state NOT IN ('archived') ORDER BY created_at DESC", (event_id,))
    parts = []
    if opp:
        parts.append(f"ranked #{opp['rank']} (fit {opp['score']})" if opp["eligible"] and opp["rank"] else "not eligible")
    if plan:
        parts.append(f"campaign {plan['state']}")
    return "; ".join(parts) or "not ranked"


def on_event_change(event_id: str, kind: str, version: int, before: dict, after: dict) -> None:
    ev = db.q1("SELECT title FROM event WHERE id=?", (event_id,))
    title = ev["title"] if ev else event_id
    if kind == "cancellation":
        msg = f"{title} is now listed as cancelled. The opportunity is invalidated and plans need review."
        after_rec = "Excluded from opportunities; stand-down revision proposed for any campaign"
    elif kind == "rescheduling":
        msg = f"{title} changed time/status: {fmt_ny(before.get('start_utc'))} → {fmt_ny(after.get('start_utc'))} ({after.get('status')})."
        after_rec = "Re-ranked; retimed campaign proposed for owner approval"
    elif kind == "relocation":
        msg = f"{title} venue changed: {before.get('venue')} → {after.get('venue')}."
        after_rec = "Re-ranked with new distance; plan revision proposed"
    else:
        msg = f"{title}: source details changed ({kind})."
        after_rec = "Re-ranked"
    before_rec = _recommendation(event_id)
    from . import developments, plans
    dev_id = developments.from_event_change(event_id, kind, version, before, after) if kind in MATERIAL else None
    plans.propose_revisions_for_change(event_id, kind, version, before, after)
    affected = [p["id"] for p in db.q("SELECT id FROM plan WHERE event_id=? AND state NOT IN ('archived','completed')", (event_id,))]
    if _relevant(event_id):
        add(f"{event_id}:v{version}:{kind}", kind, msg, event_id,
            impact="Opportunity re-ranked; proposed plan revision awaits owner review." if kind in MATERIAL else "",
            development_id=dev_id, affected_plans=affected,
            before_after={"before": {"facts": before, "recommendation": before_rec},
                          "after": {"facts": after, "recommendation": after_rec}},
            source_ids=[f"src:{event_id}"])
    if dev_id:
        db.ex("""INSERT OR IGNORE INTO decision(id,development_id,event_id,plan_id,at,source_change,affected_assumption,before,after,
                 decision,verification,evidence_ids,decided_by) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
              (f"dec-{dev_id}-v{version}", dev_id, event_id, affected[0] if affected else None, iso(now_utc()),
               msg, {"cancellation": "Expected visitors for this event", "rescheduling": "Timing of the pre-event window",
                     "relocation": "Distance from the café"}.get(kind, "Event facts"),
               db.j({"facts": before, "recommendation": before_rec}), db.j({"facts": after, "recommendation": after_rec}),
               "revision proposed" if affected else "re-ranked (no campaign affected)",
               db.j({"method": "primary listing comparison", "confirmed": True}), db.j([f"src:{event_id}"]), "rules"))
    from . import jobs
    jobs.request_reassessment(event_id, kind)


def top_changed(prev_top: str | None, new_top: str | None) -> None:
    if not new_top or prev_top == new_top:
        return
    ev = db.q1("SELECT title FROM event WHERE id=?", (new_top,))
    add(f"top:{prev_top}->{new_top}", "top_recommendation",
        f"Top recommendation changed to {ev['title'] if ev else new_top}.", new_top,
        impact="Review the new leading opportunity before committing budget.",
        before_after={"before": {"top": prev_top}, "after": {"top": new_top}})

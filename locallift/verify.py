"""Primary-source verification for reported changes (news → verification → decision).

When reporting claims a linked event was cancelled or rescheduled, check the primary event listing:
  confirmed     → the listing change flows through ingest (plan revision proposed, before/after recorded)
  not confirmed → claim marked disputed, explicit "no change" decision with reason, development watched
  unavailable   → "no change yet; will re-check", development watched
"""
from __future__ import annotations

import uuid

from . import config, db, ingest
from .sources import nycparks, ticketmaster
from .sources.http import SourceError
from .timeutil import iso, now_utc

# Test mode only: fixture "primary listings" keyed by event id (set by tests).
TEST_PRIMARY: dict[str, dict] = {}


def primary_listing(ev: dict) -> dict | None:
    if config.settings.env == "test":
        return TEST_PRIMARY.get(ev["id"])
    if ev["provider"] == "ticketmaster":
        return ticketmaster.get_event(ev["provider_id"])
    if ev["provider"] == "nycparks":
        return nycparks.get_event(ev["provider_id"])
    return None


def _confirms(dev_type: str, stored: dict, listing: dict) -> bool:
    if dev_type == "cancellation":
        return listing.get("status") == "cancelled"
    if dev_type == "schedule_change":
        return (listing.get("start_utc") != stored.get("start_utc")
                or listing.get("status") in ("postponed", "rescheduled"))
    return False


def verify_development(dev_id: str) -> dict:
    d = db.q1("SELECT * FROM development WHERE id=?", (dev_id,))
    if not d:
        return {"error": "unknown development"}
    events = db.q("SELECT e.* FROM development_event de JOIN event e ON e.id=de.event_id WHERE de.development_id=?", (dev_id,))
    ts = iso(now_utc())
    results = []
    if not events:
        if "official announcement" in db.uj(d["source_types"], []):
            db.ex("UPDATE development SET last_checked=? WHERE id=?", (ts, dev_id))
            _decision(dev_id, None, "Official record is itself the primary source; no event listing to compare.",
                      "Nearby foot traffic / access", "no change — keep monitoring the official record",
                      {"method": "official record", "confirmed": True})
            return {"development": dev_id, "result": "official_record"}
        db.ex("UPDATE development SET verification_state=?, last_checked=? WHERE id=?",
              ("unverified (no linked event listing to check)", ts, dev_id))
        _decision(dev_id, None, "No linked event record; reported change cannot be checked against a listing.",
                  "none", "no change — monitor for an official announcement", {"method": "none", "confirmed": None})
        return {"development": dev_id, "result": "no_linked_event"}
    for ev in events:
        stored = dict(ev)
        try:
            listing = primary_listing(ev)
        except SourceError as e:
            listing, err = None, str(e)
        else:
            err = None if listing else "no primary listing adapter for this provider"
        if listing is None:
            state = "unverified (primary source unavailable)"
            _decision(dev_id, ev["id"], f"Could not check the primary listing: {err}", "Event timing/status",
                      "no change yet — re-check scheduled", {"method": "primary listing", "confirmed": None, "error": err})
            _watch(ev["id"])
            results.append({"event": ev["id"], "result": "unavailable"})
            continue
        if _confirms(d["dev_type"], stored, listing):
            state = "verified (primary listing)"
            ingest.upsert_events([listing], provenance=ev["provenance"])  # triggers revision + before/after decision
            results.append({"event": ev["id"], "result": "confirmed"})
        else:
            state = "disputed (primary listing unchanged)"
            cid = "claim-" + uuid.uuid4().hex[:8]
            src = [s["source_id"] for s in db.q("SELECT source_id FROM development_source WHERE development_id=?", (dev_id,))]
            db.ex("""INSERT INTO claim(id,event_id,statement,classification,evidence_ids,conflicts,last_checked,review_state,created_at,created_by)
                     VALUES(?,?,?,?,?,?,?,?,?,?)""",
                  (cid, ev["id"], f"Reporting suggests a {d['dev_type'].replace('_', ' ')}: {d['headline']}", "disputed",
                   db.j(src + [f"src:{ev['id']}"]), db.j([f"src:{ev['id']}"]), ts, "pending", ts, "verification"))
            from .notify import _recommendation
            rec = _recommendation(ev["id"])
            _decision(dev_id, ev["id"], f"Reported: {d['headline']}", "Event timing/status",
                      "no change — primary listing still shows the original details; watching for confirmation",
                      {"method": "primary listing", "confirmed": False,
                       "listing": {k: listing.get(k) for k in ("status", "start_utc", "venue")}},
                      before={"recommendation": rec}, after={"recommendation": rec + " (unchanged; flagged disputed)"})
            _watch(ev["id"])
            results.append({"event": ev["id"], "result": "not_confirmed"})
        db.ex("UPDATE development SET verification_state=?, last_checked=? WHERE id=?", (state, ts, dev_id))
    return {"development": dev_id, "results": results}


def _watch(event_id: str) -> None:
    if not db.q1("SELECT 1 FROM watch WHERE event_id=? AND active=1", (event_id,)):
        ev = db.q1("SELECT version FROM event WHERE id=?", (event_id,))
        db.ex("INSERT INTO watch(id,event_id,conditions,last_version,created_at,active,created_by) VALUES(?,?,?,?,?,1,'verification')",
              ("watch-" + uuid.uuid4().hex[:8], event_id, db.j(["cancellation", "rescheduling", "relocation"]),
               ev["version"] if ev else 1, iso(now_utc())))


def _decision(dev_id, event_id, source_change, assumption, decision, verification, before=None, after=None):
    plan = db.q1("SELECT id FROM plan WHERE event_id=? AND state NOT IN ('archived','completed')", (event_id,)) if event_id else None
    db.ex("""INSERT INTO decision(id,development_id,event_id,plan_id,at,source_change,affected_assumption,before,after,decision,
             verification,evidence_ids,decided_by) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
          ("dec-" + uuid.uuid4().hex[:8], dev_id, event_id, plan["id"] if plan else None, iso(now_utc()), source_change,
           assumption, db.j(before), db.j(after), decision, db.j(verification),
           db.j([s["source_id"] for s in db.q("SELECT source_id FROM development_source WHERE development_id=?", (dev_id,))]),
           "rules"))

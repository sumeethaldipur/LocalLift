"""Persist normalized source records, detect material changes, link news and social evidence."""
from __future__ import annotations

import hashlib
import re

from . import db, notify
from .timeutil import iso, now_utc, parse

CHANGE_FIELDS = ("status", "start_utc", "venue", "lat", "lon")
NEIGHBORHOOD_TERMS = re.compile(
    r"\b(madison square garden|msg|penn station|herald square|koreatown|chelsea|hudson yards|garment district|"
    r"midtown|34th street|moynihan|seventh avenue|7th avenue|bryant park|times square|lirr|nj transit|amtrak)\b", re.I)
STOP = {"the", "and", "with", "tour", "live", "presents", "in", "at", "of", "a", "an", "vs", "vs.", "new", "york",
        "nyc", "show", "night", "concert", "2026", "2027", "event", "tickets", "for", "on", "to", "&", "-", "–"}


def _fingerprint(ev: dict) -> str:
    return hashlib.sha1(db.j([ev.get(k) for k in CHANGE_FIELDS]).encode()).hexdigest()[:16]


def _norm(s: str | None) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (s or "").lower()).strip()


def _classify_change(before: dict, after: dict) -> str | None:
    if before.get("status") != after.get("status"):
        if after.get("status") == "cancelled":
            return "cancellation"
        if after.get("status") in ("postponed", "rescheduled"):
            return "rescheduling"
        return "status_change"
    if before.get("start_utc") != after.get("start_utc"):
        return "rescheduling"
    if (_norm(before.get("venue")) != _norm(after.get("venue"))
            or (before.get("lat") is not None and after.get("lat") is not None
                and (abs(before["lat"] - after["lat"]) > 0.001 or abs(before["lon"] - after["lon"]) > 0.001))):
        return "relocation"
    return None


def upsert_events(events: list[dict], provenance: str = "real_source") -> dict:
    ts = iso(now_utc())
    stats = {"new": 0, "updated": 0, "changed": 0, "duplicates": 0, "changes": []}
    for ev in events:
        eid = f"{ev['provider']}:{ev['provider_id']}"
        sid = f"src:{eid}"
        db.ex("""INSERT INTO source_item(id,provider,kind,url,title,published_at,retrieved_at,content,access_status,license,provenance)
                 VALUES(?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET retrieved_at=excluded.retrieved_at,
                 content=excluded.content""",
              (sid, ev["provider"], "event", ev.get("url"), ev["title"], None, ts, db.j(ev.get("raw")), "ok",
               "provider terms; facts only", provenance))
        existing = db.q1("SELECT * FROM event WHERE id=?", (eid,))
        if not existing:
            # Merge duplicate listings (same title, venue and start) from the same or another provider.
            candidates = db.q("SELECT * FROM event WHERE start_utc=? AND id!=?", (ev.get("start_utc"), eid)) if ev.get("start_utc") else []
            dup = next((d for d in candidates if _norm(d["title"]) == _norm(ev["title"])
                        and _norm(d["venue"]) == _norm(ev.get("venue"))), None)
            if dup:
                ids = db.uj(dup["source_ids"], [])
                if sid not in ids:
                    ids.append(sid)
                    db.ex("UPDATE event SET source_ids=? WHERE id=?", (db.j(ids), dup["id"]))
                stats["duplicates"] += 1
                continue
            db.ex("""INSERT INTO event(id,provider,provider_id,title,venue,address,lat,lon,coord_quality,start_utc,end_utc,
                     start_local,all_day,status,url,category,performers,crowd,source_ids,first_seen,last_checked,last_success,
                     version,fingerprint,raw,provenance) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                  (eid, ev["provider"], ev["provider_id"], ev["title"], ev.get("venue"), ev.get("address"), ev.get("lat"),
                   ev.get("lon"), ev.get("coord_quality"), ev.get("start_utc"), ev.get("end_utc"), None,
                   int(bool(ev.get("all_day"))), ev.get("status"), ev.get("url"), ev.get("category"),
                   db.j(ev.get("performers") or []), None, db.j([sid]), ts, ts, ts, 1, _fingerprint(ev),
                   db.j({**(ev.get("raw") or {}), "description": ev.get("description"),
                         "sales_note": ev.get("sales_note"), "provider_status": ev.get("provider_status")}), provenance))
            stats["new"] += 1
            continue
        fp = _fingerprint(ev)
        if fp == existing["fingerprint"]:
            db.ex("UPDATE event SET last_checked=?, last_success=?, url=COALESCE(?,url) WHERE id=?", (ts, ts, ev.get("url"), eid))
            stats["updated"] += 1
            continue
        kind = _classify_change(existing, ev) or "update"
        version = existing["version"] + 1
        before = {k: existing.get(k) for k in CHANGE_FIELDS}
        after = {k: ev.get(k) for k in CHANGE_FIELDS}
        with db.tx() as c:
            c.execute("INSERT INTO event_history(event_id,version,changed_at,change_kind,before,after) VALUES(?,?,?,?,?,?)",
                      (eid, version, ts, kind, db.j(before), db.j(after)))
            c.execute("""UPDATE event SET status=?, start_utc=?, end_utc=?, venue=?, lat=?, lon=?, coord_quality=?, version=?,
                         fingerprint=?, last_checked=?, last_success=?, raw=? WHERE id=?""",
                      (ev.get("status"), ev.get("start_utc"), ev.get("end_utc"), ev.get("venue"), ev.get("lat"), ev.get("lon"),
                       ev.get("coord_quality"), version, fp, ts, ts,
                       db.j({**(ev.get("raw") or {}), "description": ev.get("description"),
                             "sales_note": ev.get("sales_note"), "provider_status": ev.get("provider_status")}), eid))
        stats["changed"] += 1
        stats["changes"].append({"event_id": eid, "kind": kind, "version": version, "before": before, "after": after})
        notify.on_event_change(eid, kind, version, before, after)
    return stats


# ------------------------------------------------------------------ news

def store_news(items: list[dict], provenance: str = "real_source") -> int:
    ts = iso(now_utc())
    n = 0
    for it in items:
        local = "neighborhood" if NEIGHBORHOOD_TERMS.search(f"{it['title']} {it.get('summary', '')}") else None
        cur = db.ex("""INSERT INTO news(id,provider,url,title,domain,published_at,retrieved_at,summary,dedupe_key,local_relevance,provenance)
                       VALUES(?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(url) DO UPDATE SET retrieved_at=excluded.retrieved_at""",
                    (it["id"], it["provider"], it["url"], it["title"], it.get("domain"), it.get("published_at"), ts,
                     it.get("summary", ""), it.get("dedupe_key"), local, provenance))
        n += cur.rowcount
    return n


def event_terms(ev: dict) -> list[str]:
    terms = [p for p in db.uj(ev.get("performers"), []) if p and len(p) > 3]
    title = re.sub(r"\(.*?\)", "", ev["title"] or "")
    words = [w for w in re.split(r"[\s:|/,]+", title) if w and w.lower().strip(".") not in STOP and len(w) > 2]
    if len(words) >= 2:
        terms.append(" ".join(words[:4]))
    for term in terms.copy():
        if "knicks" in term.lower():
            terms.append("Knicks")
    return list(dict.fromkeys(terms))


CITY_CONTEXT = re.compile(r"\b(new york|nyc|manhattan|brooklyn|queens|midtown)\b")
EVENT_CONTEXT = re.compile(r"\b(tour|concert|show|shows|residency|tickets?|setlist|gig|performs?|performance|stage|"
                           r"opening night|game|vs|playoffs?|season|broadway|musical|premiere|festival|cancel\w*|postpon\w*)\b")


def venue_aliases(venue: str | None) -> list[str]:
    v = _norm(venue)
    if not v:
        return []
    out = [v.split(" at ")[0]]
    if "madison square garden" in v:
        out += ["madison square garden", "msg", "the garden"]
    return [x for x in dict.fromkeys(out) if len(x) >= 3]


def news_relevance(ev: dict, title: str, summary: str = "") -> float:
    """0 = not about this event. An article must NAME the event/performer in its headline (or local-feed summary)
    and also show this event's context: the venue, the city, or event words (tour, concert, game, tickets…).
    Search engines match full text, so a celebrity's name anywhere in a gossip story is not enough."""
    terms = [t for t in (_norm(t) for t in event_terms(ev)) if t and (" " in t or len(t) >= 5)]
    head = _norm(f"{title} {summary}")
    if " vs " in _norm(ev.get("title", "")) and re.search(r"\bvs\b", head):
        teams = re.split(r"\s+vs\.?\s+", ev["title"], flags=re.I)
        if not all(_norm(team).split()[-1] in head.split() for team in teams):
            return 0.0
    if not any(re.search(rf"\b{re.escape(t)}\b", head) for t in terms):
        return 0.0
    if not re.search(r"\b(vs|knicks|basketball|hockey)\b", _norm(ev.get("title", ""))) and re.search(r"\b(supports?|attends?|watches?)\b.*\b(knicks|game)\b", head):
        return 0.0
    if any(re.search(rf"\b{re.escape(a)}\b", head) for a in venue_aliases(ev.get("venue"))):
        return 1.0
    if (CITY_CONTEXT.search(head) and EVENT_CONTEXT.search(head)) or re.search(r"\b(cancel\w*|postpon\w*)\b", head):
        return 0.8
    return 0.0


def link_news_for_event(ev: dict, items: list[dict], method: str) -> int:
    start = parse(ev.get("start_utc"))
    n = 0
    for it in items:
        rel = news_relevance(ev, it["title"], it.get("summary") or "")
        if rel <= 0:
            continue
        pub = parse(it.get("published_at"))
        retro = int(bool(start and pub and pub > start))
        db.ex("""INSERT INTO event_news(event_id,news_id,relevance,method,retrospective) VALUES(?,?,?,?,?)
                 ON CONFLICT(event_id,news_id) DO UPDATE SET relevance=excluded.relevance""",
              (ev["id"], it["id"], rel, method, retro))
        n += 1
    return n


def relink_all() -> dict:
    """Re-check every stored news↔event link against the current relevance rule; drop the ones that fail."""
    kept = dropped = 0
    for r in db.q("""SELECT en.event_id, en.news_id, n.title, n.summary FROM event_news en JOIN news n ON n.id=en.news_id""").copy():
        ev = db.q1("SELECT * FROM event WHERE id=?", (r["event_id"],))
        rel = news_relevance(ev, r["title"], r["summary"] or "") if ev else 0
        if rel <= 0:
            db.ex("DELETE FROM event_news WHERE event_id=? AND news_id=?", (r["event_id"], r["news_id"]))
            dropped += 1
        else:
            db.ex("UPDATE event_news SET relevance=? WHERE event_id=? AND news_id=?", (rel, r["event_id"], r["news_id"]))
            kept += 1
    return {"kept": kept, "dropped": dropped}


def link_feed_news() -> int:
    """Match stored publisher-feed items to upcoming events by performer/title terms."""
    items = db.q("SELECT * FROM news WHERE provider='gothamist'")
    total = 0
    for ev in db.q("SELECT * FROM event WHERE status!='cancelled' AND start_utc>=?", (iso(now_utc()),)):
        total += link_news_for_event(ev, items, "feed_term_match")
    return total


# ------------------------------------------------------------------ social

def store_social(event_id: str, result: dict, provenance: str = "real_source") -> list[str]:
    ts = iso(now_utc())
    ids = []
    prov = result["provider"]
    if prov == "bluesky":
        cur = result["windows"]["current"]
        oid = f"soc:{prov}:{event_id}:{ts}"
        db.ex("""INSERT INTO social_observation(id,event_id,provider,query,window_start,window_end,sample_size,unique_posts,
                 unique_authors,post_ids,truncated,pages,comparable_key,needs,excerpts,metrics,collected_at,status,detail,provenance)
                 VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
              (oid, event_id, prov, result["query"], cur["start"], cur["end"], cur["sample_size"], cur["unique_posts"],
               cur["unique_authors"], db.j(cur["post_ids"]), int(cur["truncated"]), cur["pages"],
               f"{prov}|{result['query']}|24h|{2}p", db.j(cur["needs"]), db.j(cur["excerpts"]),
               db.j({**cur["metrics"], "geo_specific_posts": cur.get("geo_specific_posts"),
                     "trend": result["trend"], "prior_window": {
                   k: result["windows"]["prior"][k] for k in ("start", "end", "unique_posts", "truncated", "sample_size")}}),
               ts, "ok", "", provenance))
        ids.append(oid)
    elif prov == "x":
        s = result.get("search")
        oid = f"soc:x:{event_id}:{ts}"
        counts = result.get("counts")
        tr = {"label": "Comparison not collected", "reason":
              "X post search is working. Two comparable daily counts were not collected; "
              + ("counts are disabled in source settings." if result.get("counts_error") == "counts not requested for this call"
                 else (result.get("counts_error") or "no counts are available."))}
        if counts:
            from .sources.social import x_counts_trend
            tr = x_counts_trend(counts)
        db.ex("""INSERT INTO social_observation(id,event_id,provider,query,window_start,window_end,sample_size,unique_posts,
                 unique_authors,post_ids,truncated,pages,comparable_key,needs,excerpts,metrics,collected_at,status,detail,provenance)
                 VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
              (oid, event_id, "x", result["query"], s and s["start"], s and s["end"], s and s["sample_size"],
               s and s["unique_posts"] or 0, s and s["unique_authors"], db.j(s and s["post_ids"]), int(bool(s and s["truncated"])),
               1, f"x|{result['query']}|24h", db.j(s and s["needs"] or {}), db.j(s and s["excerpts"] or []),
               db.j({"counts": counts, "trend": tr, "geo_specific_posts": (s or {}).get("geo_specific_posts"),
                     "search_error": result.get("search_error"),
                     "counts_error": result.get("counts_error")}), ts, "ok", "", provenance))
        ids.append(oid)
    return ids


def store_social_failure(event_id: str, provider: str, query: str, detail: str) -> None:
    ts = iso(now_utc())
    db.ex("""INSERT INTO social_observation(id,event_id,provider,query,collected_at,status,detail,provenance,unique_posts)
             VALUES(?,?,?,?,?,?,?,?,0)""", (f"soc:{provider}:{event_id}:{ts}", event_id, provider, query, ts, "failed", detail[:300], "real_source"))


def social_query(ev: dict, provider: str = "bluesky") -> str:
    """Event-specific query. Bluesky gets the phrase only (geo-specificity is measured on the
    returned text); X supports boolean operators so venue/city context is added server-side."""
    terms = event_terms(ev)
    main = (terms[0] if terms else ev["title"]).replace('"', "")
    if provider != "x":
        return f'"{main}"'
    venue = ev.get("venue") or ""
    short_venue = "MSG" if "madison square garden" in venue.lower() else venue.split(" at ")[0]
    q = f'"{main}"'
    if short_venue and len(short_venue) < 40:
        q += f' ("{short_venue}" OR NYC OR "New York")'
    return q

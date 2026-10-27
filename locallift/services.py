"""Shared operations used by the scheduler jobs, the agent tools and the API."""
from __future__ import annotations

from datetime import timedelta

from . import business, config, db, developments, ingest, notify, ranking
from .sources import news as news_src, nyc_official, nycparks, social, ticketmaster, wikidata
from .sources.http import SourceError
from .timeutil import iso, now_utc, parse


def _top_event() -> str | None:
    r = db.q1("SELECT event_id FROM opportunity WHERE rank=1")
    return r["event_id"] if r else None


def rerank() -> list[dict]:
    prev = _top_event()
    out = ranking.rank_all()
    new = _top_event()
    if prev:
        notify.top_changed(prev, new)
    return out


def discover_events(radius: float | None = None, days: int | None = None, keyword: str | None = None,
                    providers: tuple[str, ...] = ("ticketmaster", "nycparks")) -> dict:
    p = business.get_profile()
    radius = radius or p["discovery"]["radius_miles"]
    days = days or p["discovery"]["days_ahead"]
    lat, lon = p["location"]["lat"], p["location"]["lon"]
    report = {}
    for prov in providers:
        try:
            res = (ticketmaster.discover(lat, lon, radius, days, keyword) if prov == "ticketmaster"
                   else nycparks.discover(lat, lon, radius, days))
            stats = ingest.upsert_events(res["events"])
            report[prov] = {"ok": True, "fetched": len(res["events"]), "truncated": res.get("truncated"),
                            **{k: v for k, v in stats.items() if k != "changes"}, "changes": stats["changes"]}
        except SourceError as e:
            report[prov] = {"ok": False, "error": str(e), "kind": e.kind}
    ingest.link_feed_news()
    report["venue_capacity"] = enrich_venues()
    rerank()
    refresh_briefing()
    return report


def enrich_venues(limit: int = 25) -> dict:
    """Attach cited venue capacities (Wikidata) to upcoming ticketed events, cached per venue + use."""
    rows = db.q("""SELECT e.id, e.venue, e.category FROM event e WHERE e.provider='ticketmaster' AND e.status!='cancelled'
                   AND e.start_utc>=? AND e.venue IS NOT NULL AND NOT EXISTS (SELECT 1 FROM claim c WHERE c.event_id=e.id
                   AND c.numeric_kind='venue_capacity' AND c.superseded_by IS NULL)""", (iso(now_utc()),))
    looked, attached = 0, 0
    for r in rows:
        use = wikidata._use_for(r["category"]) or "any"
        key = f"venuecap:{r['venue'].lower()}:{use}"
        cached = db.get_meta(key)
        if cached is None:
            if looked >= limit:
                continue
            looked += 1
            cached = db.j(wikidata.venue_capacity(r["venue"], r["category"]) or {"none": True, "at": iso(now_utc())})
            db.set_meta(key, cached)
        info = db.uj(cached, {})
        if not info or info.get("none"):
            continue
        sid = f"wikidata:{info['qid']}:{info.get('use') or 'max'}"
        text = (f"{info['label']} ({info.get('description', '')}) — Wikidata maximum capacity (P1083)"
                f"{' for this use' if info.get('use') else ''}: {info['capacity']}. All recorded capacities: "
                + ", ".join(str(c["capacity"]) for c in info["all"]))
        db.ex("""INSERT OR IGNORE INTO source_item(id,provider,kind,url,title,retrieved_at,content,access_status,license,provenance)
                 VALUES(?,?,?,?,?,?,?,?,?,?)""", (sid, "wikidata", "reference", info["url"], f"{info['label']} capacity",
                 iso(now_utc()), db.j({"text": text}), "ok", "CC0 (Wikidata)", "real_source"))
        db.ex("""INSERT INTO claim(id,event_id,statement,classification,evidence_ids,conflicts,numeric_value,numeric_kind,
                 last_checked,review_state,created_at,created_by) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
              (f"claim-cap-{r['id']}", r["id"], f"{info['label']} holds {info['capacity']:,} for this kind of event per Wikidata",
               "sourced_fact", db.j([sid]), "[]", info["capacity"], "venue_capacity", iso(now_utc()), "auto", iso(now_utc()),
               "wikidata_lookup"))
        attached += 1
    return {"looked_up": looked, "attached": attached}

GEO_TOPIC_QUERIES = [
    # Independent geographic/topic discovery around the café (not tied to known events).
    '("Penn Station" OR "Madison Square Garden" OR "Herald Square" OR Koreatown OR "Moynihan Train Hall" OR "Hudson Yards")',
]


def refresh_briefing() -> dict:
    stats = developments.ingest_news()
    stats["briefing_items"] = developments.build_briefing()
    return stats


def discover_news() -> dict:
    """News-led discovery: publisher feed + GDELT geographic/topic search, then group into developments."""
    out = {"gothamist": refresh_feed_news(briefing=False)}
    for q in GEO_TOPIC_QUERIES:
        try:
            res = news_src.gdelt(q + " sourcecountry:US", timespan="3d", maxrecords=50)
            out["gdelt"] = {"ok": True, "items": len(res["items"]), "new": ingest.store_news(res["items"])}
        except SourceError as e:
            out["gdelt"] = {"ok": False, "error": str(e), "kind": e.kind}
    for name, fn in (("nyc_permits", nyc_official.permits), ("mta", nyc_official.mta_alerts)):
        try:
            res = fn()
            out[name] = {"ok": True, "items": len(res["items"]), **developments.ingest_official(res["items"])}
        except SourceError as e:
            out[name] = {"ok": False, "error": str(e), "kind": e.kind}
    ingest.link_feed_news()
    out["developments"] = refresh_briefing()
    return out


def refresh_feed_news(briefing: bool = True) -> dict:
    try:
        res = news_src.gothamist()
    except SourceError as e:
        return {"ok": False, "error": str(e)}
    n = ingest.store_news(res["items"])
    linked = ingest.link_feed_news()
    if briefing:
        refresh_briefing()
    return {"ok": True, "items": len(res["items"]), "new": n, "linked": linked}


def search_news_topic(query: str) -> dict:
    """Geographic/topic news search without an event id (Google News first; GDELT as backup)."""
    try:
        res = news_src.google_news(query)
    except SourceError:
        try:
            res = news_src.gdelt(query, timespan="7d", maxrecords=25)
        except SourceError as e:
            return {"ok": False, "error": str(e), "kind": e.kind, "query": query}
    ingest.store_news(res["items"])
    stats = developments.ingest_news()  # group into developments; the full briefing rebuild runs in the worker
    return {"ok": True, "query": query, "items": res["items"], "developments": stats}


def events_fresh(hours: float = 6) -> bool:
    r = db.q1("SELECT last_success FROM source_status WHERE provider='ticketmaster'")
    return bool(r and r["last_success"] and (now_utc() - parse(r["last_success"])).total_seconds() < hours * 3600)


def news_for_event(event_id: str, query: str | None = None, light: bool = False) -> dict:
    """light=True (agent calls): link results to this event only; the worker refreshes the full briefing later."""
    ev = db.q1("SELECT * FROM event WHERE id=?", (event_id,))
    if not ev:
        return {"ok": False, "error": "unknown event"}
    if not query:
        terms = ingest.event_terms(ev)
        if not terms:
            return {"ok": False, "error": "No distinctive search terms for this event."}
        subject = "Knicks" if any("knicks" in t.lower() for t in terms) else terms[0]
        venue = (ev.get("venue") or "New York").replace('"', "")
        query = f'"{subject}" ("{venue}" OR "New York") when:180d'
    try:
        res = news_src.google_news(query)
    except SourceError:
        try:
            res = news_src.gdelt(query.replace(" when:180d", ""))
        except SourceError as e:
            return {"ok": False, "error": str(e), "kind": e.kind, "query": query}
    ingest.store_news(res["items"])
    stored = []
    for it in res["items"]:
        row = db.q1("SELECT id FROM news WHERE url=?", (it["url"],))
        if row:
            it["id"] = row["id"]
            stored.append(it)
    if not light:
        ingest.relink_all()
    ingest.link_news_for_event(ev, stored, "news_search")
    if not light:
        refresh_briefing()
    return {"ok": True, "query": query, "items": res["items"], "note": res.get("note")}


def social_for_event(event_id: str, providers: tuple[str, ...] = ("bluesky", "x")) -> dict:
    ev = db.q1("SELECT * FROM event WHERE id=?", (event_id,))
    if not ev:
        return {"ok": False, "error": "unknown event"}
    out = {}
    for prov in providers:
        q = ingest.social_query(ev, prov)
        try:
            if prov == "bluesky":
                res = social.bluesky_search(q)
            else:
                if social.x_status()["status"] != "connected":
                    out["x"] = {"ok": False, "status": social.x_status()["status"],
                                "detail": "Twitter/X not connected (see Sources). Not substituted by other platforms."}
                    continue
                # Paid source: at most one X sample per event per X_MIN_HOURS_BETWEEN (repeat clicks don't spend twice).
                last = db.q1("""SELECT collected_at FROM social_observation WHERE event_id=? AND provider='x' AND status='ok'
                                ORDER BY collected_at DESC LIMIT 1""", (event_id,))
                gap = config.settings.x_min_hours_between
                if last and (now_utc() - parse(last["collected_at"])).total_seconds() < gap * 3600:
                    out["x"] = {"ok": True, "skipped": True, "detail": f"X posts already checked {ago_h(last['collected_at'])}; "
                                f"next post check allowed after {gap} h (saves paid reads)."}
                    if config.settings.x_include_counts:  # volume trend reads no posts; refresh it on the latest sample
                        try:
                            counts = social.x_counts(q)
                            row = db.q1("""SELECT id, metrics FROM social_observation WHERE event_id=? AND provider='x' AND status='ok'
                                           ORDER BY collected_at DESC LIMIT 1""", (event_id,))
                            m = db.uj(row["metrics"], {})
                            m.update({"counts": counts, "trend": social.x_counts_trend(counts), "counts_at": iso(now_utc())})
                            db.ex("UPDATE social_observation SET metrics=? WHERE id=?", (db.j(m), row["id"]))
                            out["x"]["trend"] = m["trend"]
                        except SourceError as e:
                            out["x"]["counts_error"] = str(e)
                    continue
                res = social.x_search(q, include_counts=config.settings.x_include_counts)
            ids = ingest.store_social(event_id, res)
            out[prov] = {"ok": True, "observation_ids": ids, "query": q,
                         "summary": res.get("windows", {}).get("current") or res.get("search"),
                         "trend": res.get("trend")}
        except SourceError as e:
            ingest.store_social_failure(event_id, prov, q, str(e))
            out[prov] = {"ok": False, "error": str(e), "query": q}
    ranking.rank_all()
    return out


def ago_h(ts: str) -> str:
    h = (now_utc() - parse(ts)).total_seconds() / 3600
    return f"{h:.0f} h ago" if h >= 1 else f"{h * 60:.0f} min ago"


def x_daily(n: int | None = None) -> dict:
    """Once-a-day X buzz sample for the top picks (paid; respects the monthly cap and the per-event gap)."""
    n = n or config.settings.x_daily_events
    out = {}
    for r in shortlist(n):
        out[r["title"][:40]] = social_for_event(r["event_id"], ("x",)).get("x")
    return out


def shortlist(n: int = 5) -> list[dict]:
    return db.q("""SELECT o.event_id, o.rank, e.title FROM opportunity o JOIN event e ON e.id=o.event_id
                   WHERE o.eligible=1 AND o.rank IS NOT NULL ORDER BY o.rank LIMIT ?""", (n,))


def selected_status_check() -> dict:
    """Re-check Ticketmaster status for followed/planned events starting within 24 h."""
    soon = db.q("""SELECT DISTINCT e.* FROM event e LEFT JOIN plan p ON p.event_id=e.id
                   WHERE e.provider='ticketmaster' AND e.start_utc BETWEEN ? AND ?
                   AND (e.followed=1 OR p.id IS NOT NULL)""", (iso(now_utc()), iso(now_utc() + timedelta(days=1))))
    checked = 0
    for ev in soon:
        try:
            res = ticketmaster.get_event(ev["provider_id"])
            ingest.upsert_events([res])
            checked += 1
        except SourceError:
            pass
    return {"checked": checked}

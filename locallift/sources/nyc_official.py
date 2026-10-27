"""Official NYC announcements that reveal developments without a ticket listing (free, no key):

* NYC Permitted Event Information (NYC Open Data tvpp-9vvx): permitted street fairs, parades, plaza events
  and full street closures. Filtered to the café's community boards / police precincts. No coordinates
  are published, so these never get a map pin.
* MTA service alerts (GTFS-realtime JSON, api-endpoint.mta.info): planned work and service changes at the
  stations serving the café (34 St–Penn Station, 34 St–Herald Sq).
"""
from __future__ import annotations

import hashlib
import re
import string
from datetime import datetime, timedelta, timezone

from ..timeutil import iso, local_naive_to_utc, now_ny, now_utc
from .http import SourceError, request, set_status, status_from_error

PERMITS = "https://data.cityofnewyork.us/resource/tvpp-9vvx.json"
PERMITS_PAGE = "https://data.cityofnewyork.us/City-Government/NYC-Permitted-Event-Information/tvpp-9vvx"
MTA_ALERTS = "https://api-endpoint.mta.info/Dataservice/mtagtfsfeeds/camsys%2Fsubway-alerts.json"
MTA_PAGE = "https://new.mta.info/alerts"

# Manhattan CB4 (Chelsea/Clinton) and CB5 (Midtown); precincts 10 (Chelsea), 14 (Midtown South), 13.
NEAR_BOARDS = {"4", "5"}
NEAR_PRECINCTS = {"10", "13", "14"}
# Field-sport and clean-up permits are excluded: small, routine, not demand-relevant for the café.
GATHERING_TYPES = re.compile(r"(street event|street festival|block party|parade|special event|plaza|athletic race|"
                             r"farmers market|single block festival|stationary demonstration|march)", re.I)
# Stops serving the café: 34 St-Penn (1/2/3 = 128, A/C/E = A28), 34 St-Herald Sq (B/D/F/M = D17, N/Q/R/W = R17)
NEAR_STOPS = {"128", "A28", "D17", "R17", "128N", "128S", "A28N", "A28S", "D17N", "D17S", "R17N", "R17S"}
# Small, routine permits that don't move foot traffic (classes, lawn maintenance, stroller groups…).
TRIVIAL = re.compile(r"\b(class(es)?|lawn closure|walkers|story ?time|rehearsal|yoga|fitness|bootcamp|photo shoot|"
                     r"tour group|maintenance|lawn)\b", re.I)
STATION_TEXT = re.compile(r"(34 st[- ]?penn|penn station|34 st[- ]?herald|herald sq)", re.I)
# In descriptions, only count the station when the alert says service there is affected (not "transfer at …").
STATION_AFFECTED = re.compile(r"(no |skip|bypass|not stop|closed|closure|elevator|escalator|entrance|exit)[^.]{0,60}"
                              r"(34 st[- ]?penn|penn station|34 st[- ]?herald|herald sq)", re.I)


def _clean(s: str | None) -> str:
    return re.sub(r"\s+", " ", s or "").strip()


def permits(days: int = 14) -> dict:
    start = now_ny().replace(tzinfo=None, microsecond=0)
    params = {"$limit": 2000, "$order": "start_date_time",
              "$where": f"event_borough='Manhattan' AND end_date_time >= '{start.isoformat()}' "
                        f"AND start_date_time < '{(start + timedelta(days=days)).isoformat()}'"}
    try:
        rows = request("nyc_permits", "GET", PERMITS, params=params).json()
    except SourceError as e:
        set_status("nyc_permits", "news", status_from_error(e), str(e), "not_required")
        raise
    items = []
    for r in rows:
        boards = {b.strip() for b in str(r.get("community_board", "")).split(",") if b.strip()}
        precincts = {p.strip() for p in str(r.get("police_precinct", "")).split(",") if p.strip()}
        if not (boards & NEAR_BOARDS or precincts & NEAR_PRECINCTS):
            continue
        etype = r.get("event_type") or ""
        if TRIVIAL.search(r.get("event_name") or ""):
            continue
        closure = r.get("street_closure_type") or ""
        full = "full street closure" in closure.lower()
        if GATHERING_TYPES.search(etype):
            dtype = "gathering"
        elif full:
            dtype = "access_change"
        else:
            continue  # sidewalk/curb production permits etc. do not affect demand or access materially
        st = local_naive_to_utc(r["start_date_time"]) if r.get("start_date_time") else None
        en = local_naive_to_utc(r["end_date_time"]) if r.get("end_date_time") else None
        if st and en and (en - st) > timedelta(days=3):
            continue  # long-running standing permits are not a development
        name = string.capwords(_clean(r.get("event_name")).lower())  # capwords keeps "Kid's" (title() gives "Kid'S")
        loc = string.capwords(_clean(r.get("event_location")).lower())
        items.append({
            "id": f"permit:{r.get('event_id')}", "provider": "nyc_permits", "source_type": "official announcement",
            "dev_type": dtype, "title": f"Permitted {etype.lower() or 'event'}: {name}",
            "summary": f"{name} — {loc[:220]}. Closure: {closure or 'not stated'}. Agency: {r.get('event_agency') or 'NYC'}.",
            "url": PERMITS_PAGE, "published_at": None, "effective_start": iso(st) if st else None,
            "effective_end": iso(en) if en else None, "location_text": loc[:200] or "Midtown/Chelsea (permit area)",
            "location_precision": "street segment text (no coordinates published; not pinned)",
            "cluster": hashlib.sha1(f"{name.lower()}|{iso(st) if st else r.get('event_id')}".encode()).hexdigest()[:12],
            "raw": {k: r.get(k) for k in ("event_id", "event_type", "community_board", "police_precinct",
                                          "street_closure_type", "event_agency")},
        })
    set_status("nyc_permits", "news", "healthy" if items else "no_relevant_results",
               f"{len(items)} relevant permits near the café of {len(rows)} Manhattan permits (official city records).",
               "not_required", success=True)
    return {"items": items, "retrieved_at": iso(now_utc())}


def _txt(block: dict | None) -> str:
    for t in (block or {}).get("translation", []):
        if t.get("language") in (None, "en"):
            return _clean(t.get("text"))
    return ""


def mta_alerts(days: int = 3) -> dict:
    try:
        data = request("mta", "GET", MTA_ALERTS, timeout=40).json()
    except SourceError as e:
        set_status("mta", "news", status_from_error(e), str(e), "not_required")
        raise
    now = now_utc()
    horizon = now + timedelta(days=days)
    items, seen = [], set()
    for ent in data.get("entity", []):
        a = ent.get("alert") or {}
        stops = {ie.get("stop_id") for ie in a.get("informed_entity", []) if ie.get("stop_id")}
        head, desc = _txt(a.get("header_text")), _txt(a.get("description_text"))
        # Route-wide alerts list every stop on a line, so require the station to be named in the alert text.
        if not (STATION_TEXT.search(head) or STATION_AFFECTED.search(desc)):
            continue
        periods = a.get("active_period") or [{}]
        nxt = None
        for p in periods:
            s = datetime.fromtimestamp(int(p.get("start", 0)), timezone.utc) if p.get("start") else now
            e = datetime.fromtimestamp(int(p["end"]), timezone.utc) if p.get("end") else None
            if (e is None or e >= now) and s <= horizon:
                nxt = (s, e)
                break
        if not nxt or not head:
            continue
        key = head.lower()
        if key in seen:
            continue
        seen.add(key)
        mer = a.get("transit_realtime.mercury_alert") or {}
        routes = sorted({ie.get("route_id") for ie in a.get("informed_entity", []) if ie.get("route_id")})
        items.append({
            "id": f"mta:{ent.get('id')}", "provider": "mta", "source_type": "official announcement",
            "dev_type": "access_change", "title": f"MTA: {head[:160]}",
            "summary": (desc or head)[:400] + (f" Lines: {', '.join(routes)}." if routes else ""),
            "url": MTA_PAGE, "published_at": iso(datetime.fromtimestamp(int(mer["created_at"]), timezone.utc))
            if mer.get("created_at") else None,
            "effective_start": iso(max(nxt[0], now)) if nxt[0] else None, "effective_end": iso(nxt[1]) if nxt[1] else None,
            "location_text": "34 St–Penn Station / 34 St–Herald Sq subway stations",
            "location_precision": "station (not pinned)", "cluster": hashlib.sha1(key.encode()).hexdigest()[:12],
            "raw": {"alert_type": mer.get("alert_type"), "routes": routes, "stops": sorted(stops & NEAR_STOPS)},
        })
    set_status("mta", "news", "healthy" if items else "no_relevant_results",
               f"{len(items)} alerts affecting Penn Station / Herald Sq in the next {days} days (official MTA feed).",
               "not_required", success=True)
    return {"items": items[:15], "retrieved_at": iso(now_utc())}

"""NYC Parks Public Events – Upcoming 14 Days (NYC Open Data, dataset w3wp-dpdi)."""
from __future__ import annotations

import html
import re
from datetime import timedelta

from ..geo import haversine_miles
from ..timeutil import iso, local_naive_to_utc, now_ny, now_utc
from .http import SourceError, request, set_status, status_from_error

RESOURCE = "https://data.cityofnewyork.us/resource/w3wp-dpdi.json"
META = "https://data.cityofnewyork.us/api/views/w3wp-dpdi.json"
DATASET_PAGE = "https://data.cityofnewyork.us/City-Government/NYC-Parks-Public-Events-Upcoming-14-Days/w3wp-dpdi"
MAX_ROWS = 2000


def _clean(s: str | None) -> str:
    if not s:
        return ""
    s = html.unescape(re.sub(r"<[^>]+>", " ", s))
    return re.sub(r"\s+", " ", s).strip()


def _coords(s: str | None) -> tuple[float | None, float | None]:
    if not s:
        return None, None
    first = s.split(";")[0]
    m = re.match(r"\s*(-?\d+\.\d+)\s*,\s*(-?\d+\.\d+)", first)
    if not m:
        return None, None
    return float(m.group(1)), float(m.group(2))


def dataset_freshness() -> dict:
    try:
        meta = request("nycparks", "GET", META).json()
        ts = meta.get("rowsUpdatedAt")
        from datetime import datetime, timezone
        return {"rows_updated_at": iso(datetime.fromtimestamp(ts, timezone.utc)) if ts else None,
                "name": meta.get("name")}
    except SourceError:
        return {"rows_updated_at": None}


def _normalize_row(r: dict) -> dict:
    la, lo = _coords(r.get("coordinates"))
    st = local_naive_to_utc(r["starttime"]) if r.get("starttime") else None
    en = local_naive_to_utc(r["endtime"]) if r.get("endtime") else None
    if en and st and en < st:  # cross-midnight event recorded with an earlier end clock time
        en = en + timedelta(days=1)
    link = (r.get("link") or {}).get("url") if isinstance(r.get("link"), dict) else r.get("link")
    return {
        "provider": "nycparks", "provider_id": str(r.get("guid")), "title": _clean(r.get("title")),
        "venue": _clean(r.get("location")) or r.get("parknames"), "address": r.get("parknames"),
        "lat": la, "lon": lo, "coord_quality": "dataset_coordinates" if la is not None else "missing",
        "start_utc": iso(st) if st else None, "end_utc": iso(en) if en else None, "all_day": False,
        "status": "scheduled", "url": link, "category": r.get("categories") or "Parks event",
        "performers": [], "description": _clean(r.get("description"))[:1200],
        "raw": {k: r.get(k) for k in ("guid", "parkids", "parknames", "location", "categories",
                                      "starttime", "endtime", "registration_url")},
    }


def get_event(guid: str) -> dict | None:
    rows = request("nycparks", "GET", RESOURCE, params={"guid": guid, "$limit": 1}).json()
    return _normalize_row(rows[0]) if rows else None


def discover(lat: float, lon: float, radius_miles: float, days: int) -> dict:
    start = now_ny().replace(tzinfo=None, microsecond=0)
    end = start + timedelta(days=days)
    params = {"$limit": MAX_ROWS, "$order": "starttime ASC",
              "$where": f"starttime >= '{(start - timedelta(hours=12)).isoformat()}' AND starttime < '{end.isoformat()}'"}
    try:
        rows = request("nycparks", "GET", RESOURCE, params=params).json()
    except SourceError as e:
        set_status("nycparks", "events", status_from_error(e), str(e), "not_required")
        raise
    fresh = dataset_freshness()
    events, unlocated = [], 0
    for r in rows:
        la, lo = _coords(r.get("coordinates"))
        if la is not None:
            if haversine_miles(lat, lon, la, lo) > radius_miles:
                continue
        else:
            # No coordinates: keep as an unlocated item only if it is a Manhattan park (parkids 'M...').
            if not str(r.get("parkids", "")).startswith("M"):
                continue
            unlocated += 1
        events.append(_normalize_row(r))
    set_status("nycparks", "events", "healthy" if events else "no_relevant_results",
               f"{len(events)} nearby of {len(rows)} citywide rows ({unlocated} unlocated). "
               f"Dataset rows updated {fresh.get('rows_updated_at') or 'unknown'}.",
               "not_required", success=True)
    return {"events": events, "retrieved_at": iso(now_utc()), "dataset_updated_at": fresh.get("rows_updated_at"),
            "citywide_rows": len(rows), "truncated": len(rows) >= MAX_ROWS}

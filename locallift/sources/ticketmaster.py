"""Ticketmaster Discovery API v2 adapter (ticketed concerts, sports, comedy, theatre)."""
from __future__ import annotations

from datetime import datetime, timedelta
import math

from .. import config
from ..geo import geohash
from ..timeutil import iso, local_naive_to_utc, now_utc, parse
from .http import SourceError, request, set_status, status_from_error

BASE = "https://app.ticketmaster.com/discovery/v2/events.json"
MAX_PAGES = 10  # API deep-paging ceiling: size * page < 1000
MAX_REQUESTS = 40  # hard per-refresh budget, incomplete coverage is reported
PAGE_SIZE = 100

# Ticketmaster status codes. NOTE: 'offsale' means tickets are not on sale; it is NOT "sold out".
STATUS_MAP = {"onsale": "scheduled", "offsale": "scheduled", "rescheduled": "rescheduled",
              "postponed": "postponed", "cancelled": "cancelled", "canceled": "cancelled"}


def _normalize(ev: dict) -> dict:
    venues = (ev.get("_embedded") or {}).get("venues") or [{}]
    v = venues[0]
    loc = v.get("location") or {}
    lat = float(loc["latitude"]) if loc.get("latitude") else None
    lon = float(loc["longitude"]) if loc.get("longitude") else None
    dates = ev.get("dates") or {}
    start = dates.get("start") or {}
    if start.get("dateTime"):
        start_utc = parse(start["dateTime"])
        all_day = False
    elif start.get("localDate"):
        t = start.get("localTime") or "00:00:00"
        start_utc = local_naive_to_utc(f"{start['localDate']}T{t}")
        all_day = not start.get("localTime")
    else:
        start_utc, all_day = None, False
    end_utc = parse((dates.get("end") or {}).get("dateTime")) if (dates.get("end") or {}).get("dateTime") else None
    code = ((dates.get("status") or {}).get("code") or "").lower()
    cls = (ev.get("classifications") or [{}])[0]
    seg = (cls.get("segment") or {}).get("name")
    genre = (cls.get("genre") or {}).get("name")
    attractions = (ev.get("_embedded") or {}).get("attractions") or []
    addr = ", ".join(x for x in [(v.get("address") or {}).get("line1"), (v.get("city") or {}).get("name")] if x)
    return {
        "provider": "ticketmaster", "provider_id": ev["id"], "title": ev.get("name", "Untitled"),
        "venue": v.get("name"), "venue_id": v.get("id"), "address": addr, "lat": lat, "lon": lon,
        "coord_quality": "venue_coordinates" if lat is not None else "missing",
        "start_utc": iso(start_utc) if start_utc else None, "end_utc": iso(end_utc) if end_utc else None,
        "all_day": all_day, "status": STATUS_MAP.get(code, code or "unknown"), "provider_status": code,
        "url": ev.get("url"), "category": " / ".join(x for x in [seg, genre] if x and x != "Undefined"),
        "performers": [a.get("name") for a in attractions if a.get("name")],
        "sales_note": "Ticketmaster 'offsale' means tickets are not currently on sale; it does not mean sold out."
                      if code == "offsale" else None,
        "raw": {"id": ev["id"], "dates": dates, "venue": {"id": v.get("id"), "name": v.get("name")},
                "classifications": ev.get("classifications"), "pleaseNote": ev.get("pleaseNote"),
                "info": ev.get("info")},
    }


def get_event(provider_id: str) -> dict:
    key = config.settings.ticketmaster_key
    if not key:
        raise SourceError("Ticketmaster API key not configured", kind="auth")
    data = request("ticketmaster", "GET", f"https://app.ticketmaster.com/discovery/v2/events/{provider_id}.json",
                   params={"apikey": key, "locale": "*"}).json()
    return _normalize(data)


def discover(lat: float, lon: float, radius_miles: float, days: int, keyword: str | None = None) -> dict:
    key = config.settings.ticketmaster_key
    if not key:
        set_status("ticketmaster", "events", "setup_required", "TICKETMASTER_API_KEY is not set.",
                   "missing", next_step="Create a free key at developer.ticketmaster.com and add it to .env.")
        raise SourceError("Ticketmaster API key not configured", kind="auth")
    start = now_utc().replace(microsecond=0)
    end = start + timedelta(days=days)
    params = {
        "apikey": key, "geoPoint": geohash(lat, lon, 9), "radius": max(1, math.ceil(radius_miles)), "unit": "miles",
        "startDateTime": start.strftime("%Y-%m-%dT%H:%M:%SZ"), "endDateTime": end.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "size": PAGE_SIZE, "sort": "date,asc", "locale": "*",
    }
    if keyword:
        params["keyword"] = keyword
    events, seen, requests, truncated = [], set(), 0, False
    ranges = [(start, end)]
    try:
        while ranges and requests < MAX_REQUESTS:
            lo, hi = ranges.pop()
            params.update(startDateTime=lo.strftime("%Y-%m-%dT%H:%M:%SZ"),
                          endDateTime=hi.strftime("%Y-%m-%dT%H:%M:%SZ"), page=0)
            data = request("ticketmaster", "GET", BASE, params=params).json()
            requests += 1
            total_pages = int((data.get("page") or {}).get("totalPages") or 1)
            if total_pages > MAX_PAGES and (hi - lo).total_seconds() > 1:
                middle = lo + timedelta(seconds=int((hi - lo).total_seconds() // 2))
                ranges.extend([(middle, hi), (lo, middle)])
                continue
            for page in range(min(total_pages, MAX_PAGES)):
                if page:
                    if requests >= MAX_REQUESTS:
                        truncated = True
                        break
                    params["page"] = page
                    data = request("ticketmaster", "GET", BASE, params=params).json()
                    requests += 1
                for ev in (data.get("_embedded") or {}).get("events", []):
                    if ev["id"] not in seen:
                        seen.add(ev["id"])
                        events.append(_normalize(ev))
            truncated = truncated or total_pages > MAX_PAGES
        truncated = truncated or bool(ranges)
    except SourceError as e:
        set_status("ticketmaster", "events", status_from_error(e), str(e), "configured")
        raise
    set_status("ticketmaster", "events", "healthy" if events else "no_relevant_results",
               f"{len(events)} events in {radius_miles} mi / {days} days" + (" (pagination truncated)" if truncated else ""),
               "configured", success=True)
    return {"events": events, "truncated": truncated, "requests": requests, "retrieved_at": iso(now_utc()),
            "query": {k: v for k, v in params.items() if k != "apikey"}}

"""Rush Radar: hour-by-hour expected extra walk-ins from nearby events for the next N days.

Uses the same inputs as campaign projections — crowd estimate (published / venue capacity × sell-through /
type ballpark), share of attendees passing the café by straight-line distance, and conversion — and spreads the
expected orders over the two hours before each event (60% / 40%). Shared capacity: all events in the same hour add
up against the café's orders/hour limit. Planning estimate, not a forecast; no campaign or staffing change is made.
"""
from __future__ import annotations

from datetime import timedelta

from . import business, db, ranking
from .geo import distance
from .plans import REACH_BY_DISTANCE
from .timeutil import NY, iso, now_utc, parse, to_ny

CONVERSION = 0.25          # base scenario, same as projections
PRE_SPLIT = (0.4, 0.6)     # share of walk-ins two hours before / one hour before start
STAFF_ORDERS_PER_HOUR = 16 # assumption: one extra trained person adds ~16 orders/hour at peak


def build(days: int = 7) -> dict:
    profile = business.get_profile()
    loc, hours = profile["location"], profile["hours"]
    cap = profile["capacity_orders_per_hour"]
    start = now_utc()
    end = start + timedelta(days=days)
    today = to_ny(start).date()
    grid: dict[str, dict[int, dict]] = {}
    for d in range(days):
        day = today + timedelta(days=d)
        base = business.baseline_for_date(day)["hours"]
        grid[day.isoformat()] = {h: {"hour": h, "baseline": round(base.get(h, {"orders": 0})["orders"], 1), "extra": 0.0, "events": []}
                                 for h in range(hours["open"], hours["close"])}
    rows = db.q("""SELECT e.*, o.eligible FROM event e JOIN opportunity o ON o.event_id=e.id
                   WHERE o.eligible=1 AND e.start_utc>=? AND e.start_utc<? AND e.lat IS NOT NULL""", (iso(start), iso(end)))
    seen_series = set()
    for ev in rows:
        st = parse(ev["start_utc"])
        s_ny = to_ny(st)
        if s_ny.hour == 0 and s_ny.minute == 0:
            continue  # date-only listings (e.g. all-day tours) have no reliable start hour
        key = (ev["venue"], s_ny.strftime("%Y-%m-%d %H"))
        if key in seen_series:
            continue  # ticket variants of the same performance
        seen_series.add(key)
        crowd = ranking.crowd_for(ev["id"])
        if not crowd.get("expected"):
            continue
        miles = (distance(loc["lat"], loc["lon"], ev["lat"], ev["lon"]) or {"miles": 99})["miles"]
        reach = next(r for lim, r in REACH_BY_DISTANCE if miles <= lim)["base"]
        walkins = crowd["expected"] * reach * CONVERSION
        if walkins < 0.5:
            continue
        day = grid.get(s_ny.date().isoformat())
        if not day:
            continue
        for share, h in zip(PRE_SPLIT, (s_ny.hour - 2, s_ny.hour - 1)):
            if h in day:
                day[h]["extra"] += walkins * share
                day[h]["events"].append({"id": ev["id"], "title": ev["title"], "start": ev["start_utc"],
                                         "walkins": round(walkins * share, 1), "crowd": crowd["expected"],
                                         "estimated": crowd.get("estimated", True)})
    out_days = []
    for dkey, hrs in grid.items():
        cells = []
        for h, c in sorted(hrs.items()):
            total = c["baseline"] + c["extra"]
            c.update({"extra": round(c["extra"], 1), "total": round(total, 1), "capacity": cap,
                      "over_capacity": total > cap, "unserved": round(max(0.0, total - cap), 1),
                      "extra_staff": int(-(-max(0.0, total - cap) // STAFF_ORDERS_PER_HOUR))})
            cells.append(c)
        peak = max(cells, key=lambda c: c["extra"]) if cells else None
        events = {e["id"]: e for c in cells for e in c["events"]}
        out_days.append({"date": dkey, "hours": cells, "extra_total": round(sum(c["extra"] for c in cells)),
                         "peak": peak and {"hour": peak["hour"], "extra": peak["extra"], "total": peak["total"]},
                         "over_hours": [c["hour"] for c in cells if c["over_capacity"]],
                         "staff_hours": sum(c["extra_staff"] for c in cells),
                         "events": sorted(({"id": e["id"], "title": e["title"], "start": e["start"], "crowd": e["crowd"]}
                                           for e in events.values()), key=lambda e: e["start"])})
    return {"days": out_days, "capacity": cap, "open": hours["open"], "close": hours["close"],
            "assumptions": {"conversion": CONVERSION, "reach_by_distance": REACH_BY_DISTANCE, "pre_split": PRE_SPLIT,
                            "staff_orders_per_hour": STAFF_ORDERS_PER_HOUR, "labor_cost_per_hour": profile["labor_cost_per_hour"]},
            "label": "Planning estimate from crowd estimates × share passing the café × 25% conversion. Not a forecast."}


def staff_briefing(date_str: str) -> str:
    data = build(8)
    day = next((d for d in data["days"] if d["date"] == date_str), None)
    if not day:
        return "No data for that day."
    prof = business.get_profile()
    dname = parse(date_str + "T16:00:00Z").astimezone(NY).strftime("%A %b %-d")
    lines = [f"Opening Act — staff briefing for {dname}", ""]
    if not day["events"]:
        lines.append("No big nearby events expected. Normal day — keep the usual setup.")
        return "\n".join(lines)
    walk = {}
    for c in day["hours"]:
        for e in c["events"]:
            walk[e["id"]] = walk.get(e["id"], 0) + e["walkins"]
    main = sorted((e for e in day["events"] if walk.get(e["id"], 0) >= 1), key=lambda e: -walk.get(e["id"], 0))[:6]
    if not main:
        lines.append("Nearby events are too far away to send many people our way. Normal setup.")
        return "\n".join(lines)
    lines.append("Events that should send people our way:")
    for e in sorted(main, key=lambda e: e["start"]):
        lines.append(f"• {e['title']} — starts {to_ny(parse(e['start'])).strftime('%-I:%M %p')} "
                     f"(~{int(e['crowd']):,} attending, ~{round(walk.get(e['id'], 0))} likely walk-ins)")
    busy = [c for c in day["hours"] if c["extra"] >= 3]
    if busy:
        lines += ["", "When it gets busy:"]
        for c in busy:
            h = c["hour"]
            lines.append(f"• {h % 12 or 12}{'am' if h < 12 else 'pm'}: ~{round(c['total'])} orders expected (normal ~{round(c['baseline'])})"
                         + (f" — OVER our {data['capacity']}/h limit by ~{round(c['unserved'])}" if c["over_capacity"] else ""))
    plan = db.q1("""SELECT p.data FROM plan p JOIN event e ON e.id=p.event_id WHERE p.state NOT IN ('archived','completed')
                    AND substr(e.start_utc,1,10) IN (?, ?) ORDER BY e.start_utc LIMIT 1""",
                 (date_str, (parse(date_str + "T00:00:00Z") + timedelta(days=1)).date().isoformat()))
    lines += ["", "Prep:"]
    if plan:
        o = db.uj(plan["data"])["offer"]
        lines.append(f"• Feature: {o['name']} — ${o['price']:.2f}. Code {o.get('tracking_code', '')}. Batch it 90 min before the rush.")
    lines.append("• Pre-wrap grab-and-go items; keep cold brew pre-poured on ice for the peak hour.")
    if day["staff_hours"]:
        lines.append(f"• Owner decision: ~{day['staff_hours']} extra staff hour(s) would cover the peak "
                     f"(≈${day['staff_hours'] * prof['labor_cost_per_hour']:.0f}). Otherwise switch to grab-and-go only at peak.")
    lines += ["", "Script: “Heading to the show? We’re ready in two minutes.”",
              "Estimates only — based on crowd size and distance, not ticket data."]
    return "\n".join(lines)

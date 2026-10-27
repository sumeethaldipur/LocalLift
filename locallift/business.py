"""Opening Act Café: fictional business profile and synthetic order history."""
from __future__ import annotations

import copy
import random
from datetime import date, timedelta

from . import db
from .timeutil import is_weekend, now_ny, now_utc, iso

BUSINESS_ID = "opening-act"

WEEKDAY_HOURLY = [10, 20, 24, 18, 12, 16, 16, 12, 10, 8, 8, 8, 8, 6, 4]
WEEKEND_HOURLY = [6, 10, 14, 18, 22, 24, 24, 20, 18, 14, 12, 12, 10, 8, 8]
FIRST_HOUR = 7  # slots 07:00 .. 21:00

MENU = [
    {"id": "espresso", "name": "Espresso", "price": 3.50, "unit_cost": 0.70, "prep_min": 1},
    {"id": "latte", "name": "Latte", "price": 5.50, "unit_cost": 1.50, "prep_min": 2},
    {"id": "cold_brew", "name": "Cold brew", "price": 5.00, "unit_cost": 1.20, "prep_min": 1},
    {"id": "matcha_latte", "name": "Matcha latte", "price": 6.00, "unit_cost": 1.90, "prep_min": 2},
    {"id": "ov_cold_brew", "name": "Orange vanilla cold brew", "price": 6.50, "unit_cost": 1.70, "prep_min": 1},
    {"id": "croissant", "name": "Butter croissant", "price": 4.50, "unit_cost": 1.50, "prep_min": 0},
    {"id": "cookie", "name": "Chocolate chunk cookie", "price": 3.75, "unit_cost": 1.05, "prep_min": 0},
    {"id": "focaccia", "name": "Egg and cheddar focaccia", "price": 8.50, "unit_cost": 2.80, "prep_min": 3},
    {"id": "pesto_sandwich", "name": "Tomato mozzarella pesto sandwich", "price": 10.50, "unit_cost": 3.70, "prep_min": 2},
    {"id": "chicken_sandwich", "name": "Chicken herb sandwich", "price": 11.50, "unit_cost": 4.20, "prep_min": 2},
]

DEFAULT_PROFILE = {
    "id": BUSINESS_ID,
    "name": "Opening Act Café",
    "fictional": True,
    "description": ("A fictional independent café serving coffee, portable meals and pastries to "
                    "neighborhood customers and people attending events. Friendly, quick, convenient."),
    "location": {
        "lat": 40.7480, "lon": -73.9920,
        "label": "Near Seventh Avenue & West 30th Street, Midtown Manhattan",
        "note": ("Scenario coordinate for a fictional business. This pin does not claim that a café "
                 "occupies any particular real storefront."),
    },
    "timezone": "America/New_York",
    "currency": "USD",
    "discovery": {"radius_miles": 2.0, "days_ahead": 14, "emphasis_days": 7},
    "hours": {"open": 7, "close": 22, "days": "Every day"},
    "seating": {"seats": 24, "layout": "six 2-person tables, three 4-person tables"},
    "service_model": "Counter service, dine-in, takeaway, owner-managed pickup orders",
    "peak_team": "Two beverage staff and one food/counter staff member",
    "capacity_orders_per_hour": 48,
    "dine_in_share": 0.35,
    "avg_visit_minutes": 35,
    "typical_weekday": {"orders": 180, "aov": 10.50, "sales": 1890.0},
    "typical_weekend": {"orders": 220, "aov": 11.00, "sales": 2420.0},
    "variable_cost_rate": 0.32,
    "payment_fee_rate": 0.03,
    "labor_cost_per_hour": 25.0,
    "budget": {"per_event": 75.0, "per_week": 200.0},
    "channels": {
        "instagram_followers": 850, "email_optins": 180,
        "note": "Fictional audiences; no real account is connected.",
        "other": ["In-store signage", "Owner-initiated organizer collaborations"],
    },
    "menu": MENU,
    "example_offer": {
        "name": "Latte + egg and cheddar focaccia", "items": ["latte", "focaccia"],
        "price": 12.00, "regular_price": 14.00, "unit_cost": 4.30,
    },
    "assumptions": {
        "one_order_one_customer": True,
        "note": "Prototype assumption: one customer order represents one customer served.",
    },
    "provenance": {
        "profile": "owner_assumption (fictional)",
        "history": "synthetic_business_history",
    },
}


def get_profile() -> dict:
    row = db.q1("SELECT data FROM business WHERE id=?", (BUSINESS_ID,))
    if row:
        return db.uj(row["data"])
    save_profile(copy.deepcopy(DEFAULT_PROFILE), created=True)
    return copy.deepcopy(DEFAULT_PROFILE)


def save_profile(profile: dict, created: bool = False) -> dict:
    ts = iso(now_utc())
    if created:
        db.ex("INSERT OR IGNORE INTO business(id,data,created_at,updated_at) VALUES(?,?,?,?)",
              (BUSINESS_ID, db.j(profile), ts, ts))
    else:
        db.ex("UPDATE business SET data=?, updated_at=? WHERE id=?", (db.j(profile), ts, BUSINESS_ID))
    return profile


def validate_profile_patch(patch: dict) -> list[str]:
    errs = []
    disc = patch.get("discovery", {})
    r = disc.get("radius_miles")
    if r is not None and not (0.5 <= float(r) <= 5):
        errs.append("Discovery radius must be between 0.5 and 5 miles.")
    b = patch.get("budget", {})
    for k in ("per_event", "per_week"):
        if k in b and float(b[k]) < 0:
            errs.append(f"Budget {k} cannot be negative.")
    for k in ("dine_in_share", "payment_fee_rate", "variable_cost_rate"):
        if k in patch and not (0 <= float(patch[k]) <= 1):
            errs.append(f"{k} must be between 0 and 1.")
    if "capacity_orders_per_hour" in patch and int(patch["capacity_orders_per_hour"]) <= 0:
        errs.append("Capacity must be positive.")
    h = patch.get("hours", {})
    if h and not (0 <= int(h.get("open", 7)) < int(h.get("close", 22)) <= 24):
        errs.append("Opening hours must satisfy 0 <= open < close <= 24.")
    return errs


def deep_merge(base: dict, patch: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in patch.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = deep_merge(out[k], v)
        else:
            out[k] = v
    return out


# ---------------------------------------------------------------- history

def ensure_history(seed: int | None = None) -> dict:
    """Generate 8 weeks of synthetic history once, ending the day before first init.

    Order counts follow the specified hourly profile exactly; basket composition varies
    with a persisted seed, and each day's sales are reconciled to the stated AOV.
    """
    existing = db.get_meta("history_meta")
    if existing:
        return db.uj(existing)
    seed = seed if seed is not None else 20261027
    end = now_ny().date() - timedelta(days=1)
    start = end - timedelta(days=55)
    rng = random.Random(seed)
    rows = []
    d = start
    while d <= end:
        weekend = is_weekend(d)
        hourly = WEEKEND_HOURLY if weekend else WEEKDAY_HOURLY
        target = 2420.0 if weekend else 1890.0
        raw = [o * rng.uniform(0.85, 1.15) for o in hourly]
        scale = target / sum(raw)
        sales = [round(x * scale, 2) for x in raw]
        sales[-1] = round(target - sum(sales[:-1]), 2)  # exact reconciliation
        for i, (o, s) in enumerate(zip(hourly, sales)):
            rows.append((BUSINESS_ID, d.isoformat(), FIRST_HOUR + i,
                         "weekend" if weekend else "weekday", o, s))
        d += timedelta(days=1)
    with db.tx() as c:
        c.executemany("INSERT OR IGNORE INTO business_history(business_id,date,hour,day_type,orders,sales)"
                      " VALUES(?,?,?,?,?,?)", rows)
    meta = {"seed": seed, "start": start.isoformat(), "end": end.isoformat(),
            "generated_at": iso(now_utc()), "provenance": "synthetic_business_history"}
    db.set_meta("history_meta", db.j(meta))
    return meta


def hourly_baseline(day_type: str) -> dict[int, dict]:
    """Average orders and sales by hour from the stored synthetic history."""
    rows = db.q("SELECT hour, AVG(orders) o, AVG(sales) s, COUNT(*) n FROM business_history"
                " WHERE business_id=? AND day_type=? GROUP BY hour ORDER BY hour", (BUSINESS_ID, day_type))
    if not rows:
        hourly = WEEKEND_HOURLY if day_type == "weekend" else WEEKDAY_HOURLY
        aov = 11.0 if day_type == "weekend" else 10.5
        return {FIRST_HOUR + i: {"orders": o, "sales": o * aov, "days": 0} for i, o in enumerate(hourly)}
    return {r["hour"]: {"orders": round(r["o"], 2), "sales": round(r["s"], 2), "days": r["n"]} for r in rows}


def baseline_for_date(d: date) -> dict:
    day_type = "weekend" if is_weekend(d) else "weekday"
    hours = hourly_baseline(day_type)
    return {"day_type": day_type, "hours": hours,
            "orders": round(sum(h["orders"] for h in hours.values())),
            "sales": round(sum(h["sales"] for h in hours.values()), 2),
            "provenance": "synthetic_business_history"}


def menu_item(item_id: str, profile: dict | None = None) -> dict | None:
    for m in (profile or get_profile())["menu"]:
        if m["id"] == item_id:
            return m
    return None

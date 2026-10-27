"""Deterministic demand, capacity and incremental-contribution scenarios.

Low/base/high are planning scenarios, not statistical confidence intervals.
Result = incremental contribution (excludes rent and other unchanged fixed costs),
never "profit" or guaranteed revenue.
"""
from __future__ import annotations

import math
from datetime import date

from . import business

SCENARIOS = ("low", "base", "high")


def r2(x: float) -> float:
    return round(x + 1e-9, 2) if x >= 0 else -round(-x + 1e-9, 2)


def offer_unit_contribution(price: float, unit_cost: float, fee_rate: float) -> float:
    """Contribution per order before labor and fixed campaign spend."""
    return r2(price - unit_cost - price * fee_rate)


def break_even_orders(fixed_costs: float, contribution_per_order: float) -> int | None:
    """Smallest whole number of incremental orders covering fixed costs, or None if impossible."""
    if contribution_per_order <= 0:
        return None
    if fixed_costs <= 0:
        return 0
    return math.ceil(round(fixed_costs / contribution_per_order, 9))


def incremental_contribution(net_sales: float, product_costs: float, payment_fees: float,
                             labor: float = 0, campaign_spend: float = 0, other: float = 0,
                             promo_losses: float = 0) -> float:
    return r2(net_sales - product_costs - payment_fees - labor - campaign_spend - other - promo_losses)


def occupied_seats(customers_per_hour: float, dine_in_share: float, visit_minutes: float) -> float:
    """Average occupied seats (Little's law). Not a guarantee about peak arrivals."""
    return round(customers_per_hour * dine_in_share * (visit_minutes / 60.0), 2)


def _scn(v, key):
    if isinstance(v, dict):
        return float(v.get(key, v.get("base", 0)) or 0)
    return float(v or 0)


DEFAULT_SCENARIO_INPUTS = {
    "offer_take_rate": 0.6,
    "regular_offer_redemptions": 3,
    "incremental_share": {"low": 0.6, "base": 0.75, "high": 0.85},
}


def resolved_inputs(inputs: dict) -> dict:
    """Return explicit model defaults so forms cannot silently introduce other assumptions."""
    return {**DEFAULT_SCENARIO_INPUTS, **inputs}


def project(inputs: dict, profile: dict | None = None) -> dict:
    """Run one modeling path (attendance OR baseline_change) for one event window."""
    inputs = resolved_inputs(inputs)
    profile = profile or business.get_profile()
    method = inputs.get("method", "baseline_change")
    if method not in ("attendance", "baseline_change"):
        raise ValueError("method must be 'attendance' or 'baseline_change'")
    d = date.fromisoformat(inputs["event_date"])
    base = business.baseline_for_date(d)
    open_h, close_h = profile["hours"]["open"], profile["hours"]["close"]
    hours = sorted({int(h) for h in inputs.get("affected_hours", []) if open_h <= int(h) < close_h})
    warnings = []
    dropped = [h for h in inputs.get("affected_hours", []) if int(h) not in hours]
    if dropped:
        warnings.append(f"Hours {dropped} are outside opening hours and were excluded (no extension assumed).")
    if not hours:
        warnings.append("No affected hours fall inside opening hours; no impact can be modeled.")
    cap = float(inputs.get("capacity_orders_per_hour", profile["capacity_orders_per_hour"]))
    dine_in = float(inputs.get("dine_in_share", profile["dine_in_share"]))
    visit = float(inputs.get("avg_visit_minutes", profile["avg_visit_minutes"]))
    seats = int(profile["seating"]["seats"])
    fee = float(inputs.get("payment_fee_rate", profile["payment_fee_rate"]))
    var_rate = float(inputs.get("variable_cost_rate", profile["variable_cost_rate"]))
    aov = profile["typical_weekend" if base["day_type"] == "weekend" else "typical_weekday"]["aov"]
    offer = inputs.get("offer") or profile["example_offer"]
    o_price, o_cost = float(offer["price"]), float(offer["unit_cost"])
    o_regular = float(offer.get("regular_price", o_price))
    take = inputs.get("offer_take_rate", 0.6)
    incr_share = inputs.get("incremental_share", {"low": 0.6, "base": 0.75, "high": 0.85})
    regular_redemptions = inputs.get("regular_offer_redemptions", 3)
    spend = float(inputs.get("campaign_spend", 0))
    labor_h = float(inputs.get("extra_labor_hours", 0))
    labor_rate = float(inputs.get("labor_cost_per_hour", profile["labor_cost_per_hour"]))
    other = float(inputs.get("other_costs", 0))
    other_events = {int(k): float(v) for k, v in (inputs.get("other_event_orders_by_hour") or {}).items()}

    if method == "attendance" and not inputs.get("attendance"):
        raise ValueError("Attendance path requires an attendance estimate; use baseline_change instead.")

    out = {"method": method, "event_date": d.isoformat(), "day_type": base["day_type"],
           "affected_hours": hours, "warnings": warnings, "scenarios": {},
           "capacity_orders_per_hour": cap, "seats": seats,
           "label": "Planning scenario: incremental contribution before rent and other unchanged fixed costs. "
                    "Not a forecast, guarantee, or total business profit.",
           "provenance": {
               "baseline": "synthetic_business_history",
               "attendance": (inputs.get("attendance") or {}).get("provenance", "n/a"),
               "rates": "owner_assumption", "offer": "owner_assumption", "menu_costs": "owner_assumption"}}

    for s in SCENARIOS:
        if method == "attendance":
            att = _scn(inputs["attendance"], s)
            associated = att * _scn(inputs.get("reachable_share", 0.02), s) * _scn(inputs.get("conversion", 0.25), s)
        else:
            pct = _scn(inputs.get("uplift_pct", {"low": 0.05, "base": 0.10, "high": 0.20}), s)
            associated = sum(base["hours"].get(h, {"orders": 0})["orders"] for h in hours) * pct
        incremental_demand = associated * _scn(incr_share, s)
        per_hour = incremental_demand / len(hours) if hours else 0
        rows, achievable, unmet = [], 0.0, 0.0
        for h in hours:
            b = base["hours"].get(h, {"orders": 0})["orders"]
            oth = other_events.get(h, 0)
            headroom = max(0.0, cap - b - oth)
            got = min(per_hour, headroom)
            achievable += got
            unmet += per_hour - got
            total = b + oth + got
            occ = occupied_seats(total, dine_in, visit)
            rows.append({"hour": h, "baseline_orders": round(b, 1), "other_event_orders": round(oth, 1),
                         "incremental_orders": round(got, 1), "unmet_demand": round(per_hour - got, 1),
                         "total_orders": round(total, 1), "capacity": cap,
                         "occupied_seats_avg": occ, "seats": seats,
                         "seat_pressure": occ > seats, "at_capacity": got < per_hour - 1e-9})
        achievable_whole = math.floor(achievable + 1e-9)
        t = _scn(take, s)
        offer_orders = achievable_whole * t
        regular_orders = achievable_whole - offer_orders
        net_sales = offer_orders * o_price + regular_orders * aov
        product_costs = offer_orders * o_cost + regular_orders * aov * var_rate
        fees = net_sales * fee
        promo = _scn(regular_redemptions, s) * max(0.0, o_regular - o_price)
        labor = labor_h * labor_rate
        contrib = incremental_contribution(net_sales, product_costs, fees, labor, spend, other, promo)
        blended = ((o_price - o_cost - o_price * fee) * t + (aov - aov * var_rate - aov * fee) * (1 - t))
        out["scenarios"][s] = {
            "event_associated_orders": round(associated, 1),
            "incremental_demand_orders": round(incremental_demand, 1),
            "achievable_incremental_orders": achievable_whole,
            "unmet_potential_demand": round(unmet, 1),
            "net_sales": r2(net_sales), "product_costs": r2(product_costs), "payment_fees": r2(fees),
            "labor": r2(labor), "campaign_spend": r2(spend), "other_costs": r2(other),
            "promotional_losses": r2(promo), "incremental_contribution": contrib,
            "contribution_per_incremental_order": r2(blended),
            "break_even_incremental_orders": break_even_orders(spend + labor + other + promo, blended),
            "hours": rows,
        }
    return out


def offer_test_example() -> dict:
    """The arithmetic example from the build spec (unit-test only)."""
    n, price, cost, fee_rate, spend = 24, 12.0, 4.30, 0.03, 30.0
    sales = n * price
    costs = n * cost
    fees = sales * fee_rate
    return {"net_sales": r2(sales), "product_costs": r2(costs), "payment_fees": r2(fees),
            "contribution": incremental_contribution(sales, costs, fees, campaign_spend=spend),
            "per_order": offer_unit_contribution(price, cost, fee_rate),
            "break_even": break_even_orders(spend, offer_unit_contribution(price, cost, fee_rate))}

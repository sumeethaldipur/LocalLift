from datetime import date

import pytest

from locallift import business, finance


def test_spec_arithmetic_example():
    r = finance.offer_test_example()
    assert r["net_sales"] == 288.00
    assert r["product_costs"] == 103.20
    assert r["payment_fees"] == 8.64
    assert r["contribution"] == 146.16
    assert r["per_order"] == 7.34
    assert r["break_even"] == 5


def test_break_even_non_positive_contribution():
    assert finance.break_even_orders(30, 0) is None
    assert finance.break_even_orders(30, -1.5) is None
    assert finance.break_even_orders(0, 5) == 0
    assert finance.break_even_orders(29.36, 7.34) == 4  # exact division does not round up


def test_menu_offer_economics():
    p = business.get_profile()
    latte, focaccia = business.menu_item("latte", p), business.menu_item("focaccia", p)
    assert latte["price"] + focaccia["price"] == 14.00
    assert round(latte["unit_cost"] + focaccia["unit_cost"], 2) == 4.30
    assert finance.offer_unit_contribution(12.0, 4.30, 0.03) == 7.34


def test_baseline_reconciles_with_profile():
    business.get_profile()
    business.ensure_history()
    assert sum(business.WEEKDAY_HOURLY) == 180 and sum(business.WEEKEND_HOURLY) == 220
    wd = business.baseline_for_date(date(2026, 10, 28))
    we = business.baseline_for_date(date(2026, 10, 31))
    assert wd["orders"] == 180 and abs(wd["sales"] - 1890) < 0.05
    assert we["orders"] == 220 and abs(we["sales"] - 2420) < 0.05
    assert wd["provenance"] == "synthetic_business_history"


def test_history_is_persisted_not_regenerated():
    a = business.ensure_history()
    b = business.ensure_history(seed=999)
    assert a == b


def test_seat_throughput():
    # 48 customers/h × 35% dine-in × 35/60 h = 9.8 average occupied seats
    assert finance.occupied_seats(48, 0.35, 35) == 9.8
    assert finance.occupied_seats(0, 0.35, 35) == 0


def test_capacity_caps_achievable_and_reports_unmet():
    business.get_profile(); business.ensure_history()
    r = finance.project({"method": "baseline_change", "event_date": "2026-10-28", "affected_hours": [9],
                         "uplift_pct": {"low": 2.0, "base": 3.0, "high": 4.0}, "incremental_share": 1.0})
    base = r["scenarios"]["base"]
    # 09:00 weekday baseline is 24 orders; capacity 48 → at most 24 incremental
    assert base["achievable_incremental_orders"] == 24
    assert base["unmet_potential_demand"] == pytest.approx(72 - 24, abs=0.1)
    assert base["hours"][0]["at_capacity"]


def test_overlapping_events_share_capacity():
    business.get_profile(); business.ensure_history()
    solo = finance.project({"method": "baseline_change", "event_date": "2026-10-28", "affected_hours": [9],
                            "uplift_pct": 3.0, "incremental_share": 1.0})
    shared = finance.project({"method": "baseline_change", "event_date": "2026-10-28", "affected_hours": [9],
                              "uplift_pct": 3.0, "incremental_share": 1.0, "other_event_orders_by_hour": {"9": 20}})
    assert shared["scenarios"]["base"]["achievable_incremental_orders"] == 4
    assert solo["scenarios"]["base"]["achievable_incremental_orders"] == 24


def test_hours_outside_opening_are_excluded():
    business.get_profile(); business.ensure_history()
    r = finance.project({"method": "baseline_change", "event_date": "2026-10-28", "affected_hours": [22, 23]})
    assert r["affected_hours"] == [] and r["warnings"]
    assert r["scenarios"]["base"]["achievable_incremental_orders"] == 0


def test_attendance_path_requires_estimate():
    with pytest.raises(ValueError):
        finance.project({"method": "attendance", "event_date": "2026-10-28", "affected_hours": [18]})


def test_negative_contribution_is_reported_not_hidden():
    business.get_profile(); business.ensure_history()
    r = finance.project({"method": "baseline_change", "event_date": "2026-10-28", "affected_hours": [20],
                         "uplift_pct": 0.01, "campaign_spend": 75})
    assert r["scenarios"]["base"]["incremental_contribution"] < 0


def test_discount_not_double_counted():
    business.get_profile(); business.ensure_history()
    r = finance.project({"method": "baseline_change", "event_date": "2026-10-28", "affected_hours": [9],
                         "uplift_pct": 1.0, "incremental_share": 1.0, "offer_take_rate": 1.0,
                         "regular_offer_redemptions": 0, "campaign_spend": 0})
    s = r["scenarios"]["base"]
    n = s["achievable_incremental_orders"]
    # Net sales already at the $12 offer price; no separate discount line when there are no non-incremental redemptions.
    assert s["net_sales"] == round(n * 12.0, 2)
    assert s["promotional_losses"] == 0
    assert s["incremental_contribution"] == round(n * 7.34, 2)


def test_busy_demand_does_not_reduce_default_margin_at_capacity():
    business.get_profile(); business.ensure_history()
    result = finance.project({"method": "baseline_change", "event_date": "2026-10-28",
                              "affected_hours": [9], "uplift_pct": {"low": .1, "base": 3, "high": 6}})
    low, base, high = [result['scenarios'][s] for s in finance.SCENARIOS]
    assert low['incremental_contribution'] <= base['incremental_contribution'] == high['incremental_contribution']
    assert high['unmet_potential_demand'] > base['unmet_potential_demand']

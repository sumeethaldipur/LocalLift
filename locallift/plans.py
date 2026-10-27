"""Phased marketing plans: deterministic schedule, budget and economics; editable copy.

The schedule, budget caps, offer economics and projections are computed here (never by
the language model). The agent may supply copy and rationale text through savePlanDraft;
without a configured model the copy is a clearly labelled template.
"""
from __future__ import annotations

import copy
import hashlib
import uuid
from datetime import datetime, timedelta

from . import business, db, finance, ranking, themes
from .geo import distance
from .timeutil import NY, fmt_ny, iso, iso_week_key, now_utc, parse, to_ny

STATES = ("draft", "ready", "active", "completed", "archived")
TRANSITIONS = {"draft": {"ready", "archived"}, "ready": {"draft", "active", "archived"},
               "active": {"completed", "archived"}, "completed": {"archived"}, "archived": set()}
DISCLAIMER = "Opening Act Café is independent and not affiliated with the event, performers, organizer or venue."

OFFERS = {
    "morning": {"name": "Early bird: latte + butter croissant", "items": ["latte", "croissant"],
                "price": 8.50, "regular_price": 10.00},
    "midday": {"name": "Grab-and-go: latte + egg and cheddar focaccia", "items": ["latte", "focaccia"],
               "price": 12.00, "regular_price": 14.00},
    "evening": {"name": "Pre-show combo: chicken herb sandwich + cold brew", "items": ["chicken_sandwich", "cold_brew"],
                "price": 14.50, "regular_price": 16.50},
}


def _offer_for(hour: float, profile: dict) -> dict:
    key = "morning" if hour < 11 else "midday" if hour < 16 else "evening"
    o = copy.deepcopy(OFFERS[key])
    items = [business.menu_item(i, profile) for i in o["items"]]
    o["unit_cost"] = round(sum(i["unit_cost"] for i in items), 2)
    o["regular_price"] = round(sum(i["price"] for i in items), 2)
    o["payment_fee"] = round(o["price"] * profile["payment_fee_rate"], 2)
    o["contribution_per_order"] = finance.offer_unit_contribution(o["price"], o["unit_cost"], profile["payment_fee_rate"])
    o["prep_minutes"] = max(i["prep_min"] for i in items)
    o["occasion"] = key
    return o


def _due(dt: datetime, floor: datetime) -> str:
    """Due times land in working hours (8 AM–8 PM New York), rounded to 15 minutes, never before `floor`."""
    local = to_ny(max(dt, floor))
    if local.hour < 8:
        local = local.replace(hour=8, minute=0, second=0, microsecond=0)
    elif local.hour >= 20:
        local = local.replace(hour=19, minute=45, second=0, microsecond=0)
    m = (local.minute + 14) // 15 * 15
    local = local.replace(minute=0, second=0, microsecond=0) + timedelta(minutes=m)
    if local.astimezone(NY) < to_ny(floor):  # evening clamp moved it into the past: next morning instead
        local = (to_ny(floor) + timedelta(days=1)).replace(hour=8, minute=0, second=0, microsecond=0)
    return iso(local)


def build_schedule(start: datetime, now: datetime, include_outreach: bool = True) -> dict:
    """Phase windows in local time; nothing is ever scheduled in the past."""
    lead_h = (start - now).total_seconds() / 3600
    floor = now + timedelta(minutes=15)
    s_ny = to_ny(start)
    day_open = s_ny.replace(hour=7, minute=0, second=0, microsecond=0)
    if lead_h < 24:
        mode = "same_day"
        act_start = max(floor, start - timedelta(hours=3))
        phases = [
            {"key": "prepare_now", "name": "Prepare now", "start": floor, "end": min(act_start, start)},
            {"key": "activate", "name": "Activate", "start": act_start, "end": start + timedelta(minutes=30)},
            {"key": "review", "name": "Review", "start": start + timedelta(days=1), "end": start + timedelta(days=2)},
        ]
    else:
        mode = "full" if lead_h >= 24 * 7 else "short"
        ideal = [
            ("assess", "Assess and prepare", start - timedelta(days=10), start - timedelta(days=7)),
            ("awareness", "Build awareness", start - timedelta(days=7), start - timedelta(days=3)),
            ("convert", "Convert interest", start - timedelta(days=2), start - timedelta(hours=20)),
            ("execute", "Execute", max(day_open, start - timedelta(hours=6)), start + timedelta(hours=1)),
            ("learn", "Learn and retain", start + timedelta(days=1), start + timedelta(days=3)),
        ]
        phases = []
        for key, name, a, b in ideal:
            if b <= floor:
                # Window already passed: compress into "immediately" with a short window.
                a, b = floor, floor + timedelta(hours=12)
                b = min(b, start - timedelta(hours=2)) if key != "learn" else b
            a = max(a, floor)
            if b <= a:
                b = a + timedelta(hours=2)
            phases.append({"key": key, "name": name, "start": a, "end": b, "compressed": a == floor})
    return {"mode": mode, "lead_hours": round(lead_h, 1), "phases": phases, "floor": floor}


def _budget(mode: str, profile: dict, start: datetime, exclude_plan: str | None) -> dict:
    items = [{"item": "Window + counter signs (in-house print, 10 sheets)", "amount": 8.0, "phase": "awareness"},
             {"item": "Existing A-frame chalkboard", "amount": 0.0, "phase": "execute"}]
    if mode != "same_day":
        items += [{"item": "Tasting cups for in-store samples (50)", "amount": 10.0, "phase": "execute"},
                  {"item": "Small offer cards handed out at the counter (100, B/W)", "amount": 15.0, "phase": "convert"}]
    total = round(sum(i["amount"] for i in items), 2)
    cap_e, cap_w = profile["budget"]["per_event"], profile["budget"]["per_week"]
    week = iso_week_key(start)
    committed = ranking.week_committed_budget(week, exclude_plan)
    while total > cap_e or committed + total > cap_w:
        paid = [i for i in items if i["amount"] > 0]
        if not paid:
            break
        items.remove(max(paid, key=lambda i: i["amount"]))
        total = round(sum(i["amount"] for i in items), 2)
    return {"items": items, "total": total, "cap_event": cap_e, "cap_week": cap_w, "week": week,
            "week_committed_other_plans": round(committed, 2), "week_total": round(committed + total, 2),
            "within_caps": total <= cap_e and committed + total <= cap_w, "paid_media": 0.0,
            "optional_paid_amplification": [{"item": "Instagram post boost (owner decision; never auto-purchased)",
                                             "amount": 25.0, "included": False}],
            "note": "Zero-paid-media plan. Spend is a planning cap, not permission to spend."}


def _phase_content(key: str, ev: dict, offer: dict, dist: dict | None, mode: str) -> dict:
    where = f"{dist['miles']} mi straight-line from {ev.get('venue') or 'the venue'}" if dist else "near the venue"
    C = {
        "assess": dict(objective="Confirm the event facts and decide go/no-go with a costed offer.", owner="Owner",
                       tasks=["Re-check event status and time against the primary listing",
                              "Confirm offer ingredients are in stock and par levels for the window",
                              "Check pre-event hour capacity vs. 48 orders/h and 24 seats",
                              "Decide go/no-go using the conditions in this plan"],
                       deliverables=["Evidence check", "Chosen offer + unit economics", "Go/no-go decision"],
                       success="Go/no-go decided with all owners assigned."),
        "awareness": dict(objective="Make nearby event-goers and regulars aware of a quick pre-event option.",
                          owner="Owner", tasks=["Post Instagram caption + story sequence (owned audience, 850)",
                                                "Put up window and counter signs",
                                                "Send organizer collaboration request (draft only; owner sends if desired)"],
                          deliverables=["Caption and stories", "Sign copy", "Organizer outreach draft"],
                          success="Signs up; post published by owner; outreach sent or consciously skipped."),
        "convert": dict(objective="Turn interest into visits with a clear offer, location and redemption code.",
                        owner="Owner", tasks=["Send opted-in email (180) with offer code",
                                              "Brief staff on redemption code and script",
                                              "Final stock + staffing checklist for the window"],
                        deliverables=["Email", "Tracking code", "Stock/staff checklist"],
                        success="Email sent; staff briefed; stock prepared."),
        "execute": dict(objective="Serve the pre-event rush quickly without breaking service.", owner="Counter lead",
                        tasks=["Prep sandwich/focaccia batch 90 min before the window",
                               "Story reminder 2 h before start", "Run in-store script; log every code redemption",
                               "Monitor queue: switch to grab-and-go only above ~40 orders/h",
                               "Check event status 3 h before start"],
                        deliverables=["Timed posts", "Redemption log"], success="Redemptions logged; queue under ~10 min."),
        "learn": dict(objective="Record results and keep the relationship with new customers.", owner="Owner",
                      tasks=["Enter redemptions, sales and actual costs in Results",
                             "Send thank-you to opted-in list (no new addresses collected without consent)",
                             "Write 3 lessons for future events"],
                      deliverables=["Outcome record", "Learning summary"], success="Outcome recorded within 3 days."),
        "prepare_now": dict(objective="Same-day: decide quickly and prepare a zero-cost activation.", owner="Owner",
                            tasks=["Re-check event status now", "Confirm stock for the offer",
                                   "Write A-frame + counter sign", "Brief staff on offer and code"],
                            deliverables=["Go/no-go", "Signs"], success="Signs up before the pre-event window."),
        "activate": dict(objective="Capture the pre-event window.", owner="Counter lead",
                         tasks=["Post story with offer and location", "Run in-store script; log redemptions",
                                "Watch queue and capacity"], deliverables=["Story", "Redemption log"],
                         success="Redemptions logged."),
        "review": dict(objective="Record what happened.", owner="Owner",
                       tasks=["Enter results", "Note lessons"], deliverables=["Outcome record"],
                       success="Outcome recorded."),
    }
    c = C[key]
    if mode != "full" and key == "awareness":
        c = dict(c, tasks=[t for t in c["tasks"] if "organizer" not in t.lower()] +
                 ["Organizer collaboration omitted: lead time too short for a permission-based placement"])
    c["where"] = where
    return c


def template_copy(ev: dict, offer: dict, dist: dict | None, code: str) -> dict:
    when = fmt_ny(ev.get("start_utc"))
    d = f"{dist['miles']} mi" if dist else "a short trip"
    name = offer["name"].split(":")[-1].strip()
    theme = offer.get("theme") or {}
    if theme:
        story, tag = theme.get("story") or "", theme.get("tagline") or ""
        return {
            "authored_by": "template",
            "instagram_caption": (f"{story} Heading to {ev.get('venue') or 'the show'} on {when}? Our {name} ({theme['components'].lower()}) "
                                  f"is ${offer['price']:.2f} before the show — ready in minutes at Seventh Ave & W 30th. Show code {code}. "
                                  f"Fan-inspired, unofficial. {DISCLAIMER}"),
            "story_sequence": [f"Story 1 (2 days before): reveal the {name} — photo + '{tag or story}'",
                               f"Story 2 (day of, 3 h before): map pin + 'just {d} from the venue' + price ${offer['price']:.2f}",
                               "Story 3 (1 h before): live queue update — 'no line right now, grab yours on the way'"],
            "email_subject": f"A {name} for your night out",
            "email_body": (f"Hi! Going out on {when}? {story} Show code {code} at the counter for the {name} at "
                           f"${offer['price']:.2f} (normally ${offer['regular_price']:.2f}). One per customer while supplies last. "
                           f"{DISCLAIMER} Reply STOP to unsubscribe."),
            "sign_copy": f"{tag or name} — {name}, ${offer['price']:.2f}. Ready in 2 minutes. Ask for {code}.",
            "staff_script": (f"\"Going to the show tonight? Try our {name} — {theme['components'].lower()}, ${offer['price']:.2f}, "
                             f"ready in a couple of minutes.\" Log code {code} on every redemption."),
            "organizer_outreach": (f"Hello — we're Opening Act Café, an independent café near Seventh Ave & W 30th. For your event on "
                                   f"{when} we're serving a fan-inspired {name} for ${offer['price']:.2f}. Would you be open to "
                                   f"mentioning it in attendee info? We won't use your branding without permission. (DRAFT — not sent)"),
            "visual_direction": (f"Close-up of the real {name} on the counter, colours that match the theme; hand-lettered sign. "
                                 "No artist photos, logos or official event artwork."),
        }
    return {
        "authored_by": "template",
        "instagram_caption": (f"Heading to {ev.get('venue') or 'the show'} on {when}? Grab {name} for ${offer['price']:.2f} "
                              f"before you go — quick counter service at Seventh Ave & W 30th. Show code {code}. "
                              f"{DISCLAIMER}"),
        "story_sequence": ["Story 1 (2 days before): the combo, price, and 'ready in ~5 min'",
                           "Story 2 (day of, 3 h before): map pin + 'straight-line distance " + d + "'",
                           "Story 3 (1 h before): live queue update or 'no line right now'"],
        "email_subject": f"A quick bite before {ev.get('venue') or 'your plans'}",
        "email_body": (f"Hi! If you're heading out on {when}, we're open until 10 p.m. Show code {code} at the counter "
                       f"for {name} at ${offer['price']:.2f} (normally ${offer['regular_price']:.2f}). "
                       f"One per customer while supplies last. {DISCLAIMER} Reply STOP to unsubscribe."),
        "sign_copy": f"Going to the show? {name} — ${offer['price']:.2f}. Ready in minutes. Ask for {code}.",
        "staff_script": (f"\"Heading to the event tonight? Our {name} is ${offer['price']:.2f} and ready in a few minutes.\" "
                         f"Log code {code} on every redemption."),
        "organizer_outreach": (f"Hello — we're Opening Act Café, an independent café near Seventh Ave & W 30th. "
                               f"We'd love to offer attendees of your event on {when} a {name} for "
                               f"${offer['price']:.2f}. Would you be open to mentioning it in attendee info? "
                               f"We won't use your branding without permission. (DRAFT — not sent)"),
        "visual_direction": ("Warm off-white background, charcoal type, terracotta accent; photo of the actual combo on "
                             "the café counter. No celebrity images, logos or official event artwork."),
    }



def _phases(ev: dict, sched: dict, offer: dict, dist: dict | None, budget: dict, start: datetime) -> list[dict]:
    phases = []
    spend_by_phase: dict[str, float] = {}
    for b in budget["items"]:
        spend_by_phase[b["phase"]] = spend_by_phase.get(b["phase"], 0) + b["amount"]
    for i, ph in enumerate(sched["phases"]):
        c = _phase_content(ph["key"], ev, offer, dist, sched["mode"])
        span = (ph["end"] - ph["start"]) / max(1, len(c["tasks"]))
        tasks = []
        for n, t in enumerate(c["tasks"]):
            due = ph["start"] + span * (n + 1)
            if ph["key"] == "execute" and "3 h before" in t:
                due = start - timedelta(hours=3)
            if ph["key"] == "execute" and "2 h before" in t:
                due = start - timedelta(hours=2)
            tasks.append({"id": f"{ph['key']}-{n + 1}", "title": t, "due": _due(due, sched["floor"]),
                          "owner": c["owner"], "depends_on": f"{sched['phases'][i - 1]['key']}" if i else None})
        phases.append({"key": ph["key"], "name": ph["name"], "objective": c["objective"],
                       "start": iso(ph["start"]), "end": iso(ph["end"]), "owner": c["owner"],
                       "dependencies": [sched["phases"][i - 1]["key"]] if i else [],
                       "tasks": tasks, "deliverables": c["deliverables"],
                       "spend": round(spend_by_phase.get(ph["key"], 0), 2), "success_measure": c["success"],
                       "compressed": ph.get("compressed", False)})
    # Spend from phases that were merged/omitted still has to land somewhere visible.
    orphan = sum(v for k, v in spend_by_phase.items() if k not in {p["key"] for p in phases})
    if orphan:
        phases[0]["spend"] = round(phases[0]["spend"] + orphan, 2)
    return phases


REACH_BY_DISTANCE = [  # (max miles, low/base/high share of attendees who pass the café with time to stop) — owner assumption.
    # Falls off steeply: beyond a few blocks, attendees' routes (subway → venue) rarely pass the café.
    (0.25, {"low": 0.02, "base": 0.04, "high": 0.06}), (0.5, {"low": 0.005, "base": 0.01, "high": 0.02}),
    (1.0, {"low": 0.0005, "base": 0.0015, "high": 0.003}), (99, {"low": 0.0, "base": 0.0, "high": 0.0})]


def _projection_inputs(event_id: str, s_ny: datetime, offer: dict, budget: dict, profile: dict) -> dict:
    pre_hours = [h for h in (s_ny.hour - 2, s_ny.hour - 1) if profile["hours"]["open"] <= h < profile["hours"]["close"]]
    crowd = ranking.crowd_for(event_id)
    has_size = bool(crowd.get("expected"))  # published, capacity-based or type ballpark — always an explicit range
    method = "attendance" if has_size else "baseline_change"
    proj_inputs = {"method": method, "event_date": s_ny.date().isoformat(), "affected_hours": pre_hours or [s_ny.hour],
                   "offer": {"price": offer["price"], "regular_price": offer["regular_price"], "unit_cost": offer["unit_cost"]},
                   "campaign_spend": budget["total"]}
    if method == "attendance":
        ev = db.q1("SELECT lat, lon FROM event WHERE id=?", (event_id,))
        loc = profile["location"]
        dist = (distance(loc["lat"], loc["lon"], ev["lat"], ev["lon"]) or {"miles": 99})["miles"]
        reach = next(r for lim, r in REACH_BY_DISTANCE if dist <= lim)
        proj_inputs.update({"attendance": {"low": crowd["low"], "base": crowd["expected"], "high": crowd["high"],
                                           "provenance": {"Published estimate": "published estimate",
                                                          "Venue scale proxy": "estimated: venue capacity × assumed sell-through"}
                                           .get(crowd["basis"], "ballpark for this type of event")},
                            "reachable_share": reach, "conversion": {"low": 0.15, "base": 0.25, "high": 0.35}})
    return proj_inputs


def retime(plan_id: str, new_start_iso: str) -> dict:
    """Recompute only timing-dependent parts for a new event start: schedule, offer validity and the
    projection (re-run only because its date/hours inputs changed). Returns revision changes + before/after."""
    p = db.q1("SELECT * FROM plan WHERE id=?", (plan_id,))
    data = db.uj(p["data"])
    profile = business.get_profile()
    ev = db.q1("SELECT * FROM event WHERE id=?", (p["event_id"],))
    start = parse(new_start_iso)
    now = now_utc()
    sched = build_schedule(start, now)
    s_ny = to_ny(start)
    offer = data["offer"]
    loc = profile["location"]
    dist = distance(loc["lat"], loc["lon"], ev["lat"], ev["lon"])
    budget = data["budget"]
    phases = _phases(ev, sched, offer, dist, budget, start)
    old_inputs = db.uj((db.q1("SELECT inputs FROM projection WHERE plan_id=? ORDER BY created_at DESC LIMIT 1", (plan_id,)) or {}).get("inputs"), {})
    new_inputs = {**old_inputs, **{k: v for k, v in _projection_inputs(p["event_id"], s_ny, offer, budget, profile).items()
                                   if k in ("event_date", "affected_hours")}}
    changes = {"event_start": iso(start), "mode": sched["mode"], "lead_hours": sched["lead_hours"], "phases": phases,
               "offer.validity": f"{fmt_ny(start - timedelta(hours=3), False)}–{fmt_ny(start, False)} on {fmt_ny(start).split(',')[0]}"}
    recalculated = new_inputs != old_inputs
    before_proj = data.get("projection_summary", {}).get("base", {})
    after_proj = before_proj
    if recalculated:
        proj = finance.project(new_inputs, profile)
        changes["projection_summary"] = {s: {k: v for k, v in proj["scenarios"][s].items() if k != "hours"}
                                         for s in finance.SCENARIOS}
        after_proj = changes["projection_summary"]["base"]
        changes["_projection_inputs"] = new_inputs
    ba = {"before": {"event_start": data["event_start"], "affected_hours": old_inputs.get("affected_hours"),
                     "base_contribution": before_proj.get("incremental_contribution"), "mode": data["mode"]},
          "after": {"event_start": iso(start), "affected_hours": new_inputs.get("affected_hours"),
                    "base_contribution": after_proj.get("incremental_contribution"), "mode": sched["mode"]},
          "recalculated": recalculated}
    return {"changes": changes, "before_after": ba}


def generate(event_id: str, created_by: str = "owner") -> dict:
    profile = business.get_profile()
    ev = db.q1("SELECT * FROM event WHERE id=?", (event_id,))
    if not ev:
        raise ValueError("Unknown event")
    ok, why = ranking.eligibility(ev)
    if not ok:
        raise ValueError(f"Event not eligible: {why}")
    start = parse(ev["start_utc"])
    if not start:
        raise ValueError("Event start time unknown; cannot schedule a plan.")
    now = now_utc()
    sched = build_schedule(start, now)
    s_ny = to_ny(start)
    themed = ev["provider"] == "ticketmaster" or s_ny.hour >= 16
    offer = themes.offer_for_event(ev, profile) if themed else _offer_for(s_ny.hour + s_ny.minute / 60, profile)
    theme = offer.get("theme") or {}
    if sched["mode"] == "same_day" and theme.get("addon"):  # same-day: no new ingredients to buy
        offer = themes.build(offer["items"], offer["name"], theme.get("story", ""), theme.get("tagline", ""), None,
                             theme.get("source", "template"), profile)
        theme = offer["theme"]
    loc = profile["location"]
    dist = distance(loc["lat"], loc["lon"], ev["lat"], ev["lon"])
    plan_id = "plan-" + uuid.uuid4().hex[:8]
    code = "OA" + hashlib.sha1(event_id.encode()).hexdigest()[:4].upper()
    budget = _budget(sched["mode"], profile, start, None)
    phases = _phases(ev, sched, offer, dist, budget, start)
    proj_inputs = _projection_inputs(event_id, s_ny, offer, budget, profile)
    method = proj_inputs["method"]
    proj = finance.project(proj_inputs, profile)
    proj_id = "proj-" + uuid.uuid4().hex[:8]
    db.ex("INSERT INTO projection(id,event_id,plan_id,method,inputs,result,created_at) VALUES(?,?,?,?,?,?,?)",
          (proj_id, event_id, plan_id, method, db.j(proj_inputs), db.j(proj), iso(now)))
    go_no_go = ["Event still scheduled at last check", "Offer ingredients in stock",
                "Peak team scheduled for the window (no extra staff assumed)", "Budget within caps"]
    if proj["scenarios"]["base"]["incremental_contribution"] < 0:
        go_no_go.append("Base scenario contribution is negative under current assumptions: prefer the signage-only "
                        "alternative or skip unless new evidence raises expected demand.")
    opp = db.q1("SELECT * FROM opportunity WHERE event_id=?", (event_id,)) or {}
    rationale = db.uj(opp.get("rationale"), {})
    sources = db.uj(ev["source_ids"], [])
    news_ids = [r["news_id"] for r in db.q("SELECT news_id FROM event_news WHERE event_id=? AND retrospective=0", (event_id,))]
    social_ids = [r["id"] for r in db.q("SELECT id FROM social_observation WHERE event_id=? AND status='ok'", (event_id,))]
    data = {
        "event_id": event_id, "event_title": ev["title"], "event_start": ev["start_utc"], "venue": ev["venue"],
        "mode": sched["mode"], "lead_hours": sched["lead_hours"],
        "objective": f"Win {proj['scenarios']['base']['achievable_incremental_orders']} incremental pre-event orders "
                     f"(base scenario) without exceeding capacity or ${budget['total']:.0f} spend.",
        "rationale": {"text": "; ".join(rationale.get("reasons", [])) or "See opportunity factors.",
                      "largest_uncertainty": opp.get("uncertainty"), "source_ids": sources + news_ids + social_ids,
                      "classification": "inference"},
        "target_occasion": {"morning": "People wanting coffee and a pastry on the way in",
                            "midday": "People seeking a quick portable lunch before or between activities",
                            "evening": "People seeking a quick meal before the performance",
                            "themed": "Fans grabbing a quick, on-theme drink and treat on the walk to the event"}[offer["occasion"]],
        "positioning": (f"The fan-favourite stop before the show: {theme['story']}" if theme.get("story") else
                        "The fast, friendly stop before the show — ready in minutes, a short walk from the venue."),
        "offer": {**offer, "quantity_limit": "One per customer; while supplies last (cap 60 for the window)",
                  "validity": f"{fmt_ny(start - timedelta(hours=3), False)}–{fmt_ny(start, False)} on {fmt_ny(start).split(',')[0]}",
                  "redemption": f"Show or say code {code} at the counter; staff log each redemption.",
                  "tracking_code": code, "classification": "creative suggestion"},
        "menu_fit": (f"Built from your menu: {theme['components']}."
                     + (f" Adds {theme['addon']['name']} (≈${theme['addon']['cost']:.2f}/serving) — buy it ahead; needs your OK."
                        if theme.get("addon") else " No new ingredients or equipment.")) if theme else
                    "Uses existing menu items only; no new ingredients or equipment.",
        "special_recipe": {
            "name": offer["name"], "recipe": theme["components"] + " — sealed to-go cup, hand-written sticker",
            "ingredient_availability": (f"Needs {theme['addon']['name']} — buy before the event" if theme.get("addon") else "Uses existing stock"),
            "price": offer["price"], "unit_cost": offer["unit_cost"], "prep_minutes": offer["prep_minutes"],
            "story": theme.get("story"), "source": theme.get("source"), "unofficial": True,
            "owner_approval_required": True} if theme and sched["mode"] != "same_day" else None,
        "channels": [
            {"channel": "In-store signage", "type": "available", "why": "Reaches walk-by traffic on Seventh Ave."},
            {"channel": "Instagram (850 followers, fictional)", "type": "available", "why": "Owned audience; does not reach all attendees."},
            {"channel": "Email (180 opted-in, fictional)", "type": "available", "why": "Regulars and past customers only."},
            {"channel": "Organizer/venue mention", "type": "permission_dependent",
             "why": "Could reach attendees directly, only with organizer permission; draft provided, not sent.",
             "included": sched["mode"] == "full"},
        ],
        "how_customers_learn": ("Mostly through walk-by signage and the café's own followers/email list; attendees who do "
                                "not already know the café are reached only if the organizer agrees to mention it. "
                                "An owned post does not reach all attendees."),
        "phases": phases,
        "copy": template_copy(ev, offer, dist, code),
        "budget": budget,
        "measurement": {"primary": f"Redemptions of code {code}", "secondary": ["Pre-event hour orders vs. baseline",
                        "Average order value in window"], "attribution_limits":
                        "Redemptions include some regulars who would have come anyway; uplift vs. baseline is noisy."},
        "contingencies": {
            "cancellation": "Pull signs and posts; keep offer as a regular-day special; no extra prep.",
            "low_response": "After 45 min with <5 redemptions, stop extra prep and shift staff to normal service.",
            "stockout": "Substitute pesto sandwich or croissant at the same price; never extend hours.",
            "overload": "Above ~40 orders/h switch to pre-wrapped grab-and-go only; pause dine-in seating requests."},
        "go_no_go": go_no_go,
        "alternatives": [
            {"name": "Signage-only activation", "summary": "$8 in signs, no email/outreach; lowest effort."},
            {"name": "Skip and focus on a better-fit event", "summary": "If the timing or evidence weakens, hold budget."}],
        "projection_id": proj_id, "projection_method": method,
        "projection_summary": {s: {k: v for k, v in proj["scenarios"][s].items() if k != "hours"} for s in finance.SCENARIOS},
        "capacity_warnings": [f"{r['hour']}:00 at capacity" for r in proj["scenarios"]["high"]["hours"] if r["at_capacity"]],
        "disclaimer": DISCLAIMER,
        "authored_by": "template",
    }
    ts = iso(now)
    with db.tx() as c:
        c.execute("""INSERT INTO plan(id,event_id,business_id,version,state,data,owner_edits,pending_revision,created_at,updated_at,created_by)
                     VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                  (plan_id, event_id, profile["id"], 1, "draft", db.j(data), db.j({}), None, ts, ts, created_by))
        for ph in phases:
            for t in ph["tasks"]:
                c.execute("INSERT INTO task(plan_id,id,phase,title,due_local,owner,done) VALUES(?,?,?,?,?,?,0)",
                          (plan_id, t["id"], ph["key"], t["title"], t["due"], t["owner"]))
    return get(plan_id)


# ------------------------------------------------------------------ edits, versions, revisions

def _set_path(d: dict, path: str, value):
    keys = path.split(".")
    cur = d
    for k in keys[:-1]:
        if isinstance(cur, list):
            cur = cur[int(k)]
        else:
            cur = cur.setdefault(k, {})
    last = keys[-1]
    if isinstance(cur, list):
        cur[int(last)] = value
    else:
        cur[last] = value


def effective(data: dict, edits: dict) -> dict:
    out = copy.deepcopy(data)
    for path, v in (edits or {}).items():
        try:
            _set_path(out, path, v)
        except (KeyError, IndexError, ValueError, TypeError):
            pass
    return out


EDITABLE_PREFIXES = ("copy.", "objective", "positioning", "target_occasion", "how_customers_learn", "contingencies.",
                     "offer.name", "offer.quantity_limit", "offer.validity", "offer.redemption", "alternatives",
                     "measurement.", "go_no_go", "special_recipe")


def edit(plan_id: str, path: str, value) -> dict:
    if not any(path == p.rstrip(".") or path.startswith(p) for p in EDITABLE_PREFIXES):
        raise ValueError(f"'{path}' is not owner-editable here (numbers come from the calculator; edit assumptions in Results).")
    if isinstance(value, str) and len(value) > 4000:
        raise ValueError("Text too long (max 4000 characters).")
    p = db.q1("SELECT owner_edits FROM plan WHERE id=?", (plan_id,))
    if not p:
        raise ValueError("Unknown plan")
    edits = db.uj(p["owner_edits"], {})
    edits[path] = value
    db.ex("UPDATE plan SET owner_edits=?, updated_at=? WHERE id=?", (db.j(edits), iso(now_utc()), plan_id))
    return get(plan_id)


def set_state(plan_id: str, state: str) -> dict:
    p = db.q1("SELECT state, data FROM plan WHERE id=?", (plan_id,))
    if not p:
        raise ValueError("Unknown plan")
    if state not in TRANSITIONS[p["state"]]:
        raise ValueError(f"Cannot move from {p['state']} to {state}.")
    if state == "active":
        data = db.uj(p["data"])
        if not data["budget"]["within_caps"]:
            raise ValueError("Budget exceeds the per-event or weekly cap; adjust before activating.")
    db.ex("UPDATE plan SET state=?, updated_at=? WHERE id=?", (state, iso(now_utc()), plan_id))
    return get(plan_id)


def propose_revision(plan_id: str, reason: str, changes: dict, evidence_ids: list[str], kind: str,
                     before_after: dict | None = None, development_id: str | None = None) -> None:
    rev = {"reason": reason, "changes": changes, "evidence_ids": evidence_ids, "kind": kind, "created_at": iso(now_utc()),
           "before_after": before_after, "development_id": development_id}
    db.ex("UPDATE plan SET pending_revision=?, updated_at=? WHERE id=?", (db.j(rev), iso(now_utc()), plan_id))


def propose_revisions_for_change(event_id: str, kind: str, version: int, before: dict, after: dict) -> None:
    for p in db.q("SELECT id FROM plan WHERE event_id=? AND state NOT IN ('archived','completed')", (event_id,)):
        if kind == "cancellation":
            changes = {"objective": "Event cancelled — stand down this campaign; keep the offer as a regular-day special.",
                       "go_no_go": ["NO-GO: event listed as cancelled by the primary source"],
                       "alternatives": [{"name": "Switch to the next ranked opportunity",
                                         "summary": "See Opportunities for the re-ranked list."}]}
        elif kind == "rescheduling" and after.get("start_utc") and parse(after["start_utc"]) > now_utc():
            rt = retime(p["id"], after["start_utc"])
            rt["changes"]["go_no_go"] = [f"Event moved {fmt_ny(before.get('start_utc'))} → {fmt_ny(after.get('start_utc'))} "
                                         "(primary listing); confirm stock and staff for the new window"]
            propose_revision(p["id"], f"Source change v{version}: rescheduling — new campaign timing",
                             rt["changes"], [f"src:{event_id}"], kind, rt["before_after"])
            continue
        else:
            changes = {"go_no_go": [f"Re-check: {kind} reported by the primary source"]}
        propose_revision(p["id"], f"Source change v{version}: {kind}", changes, [f"src:{event_id}"], kind,
                         {"before": {k: before.get(k) for k in ("status", "start_utc", "venue")},
                          "after": {k: after.get(k) for k in ("status", "start_utc", "venue")}})


def resolve_revision(plan_id: str, accept: bool) -> dict:
    p = db.q1("SELECT * FROM plan WHERE id=?", (plan_id,))
    if not p or not p["pending_revision"]:
        raise ValueError("No pending revision")
    rev = db.uj(p["pending_revision"])
    if not accept:
        db.ex("UPDATE plan SET pending_revision=NULL WHERE id=?", (plan_id,))
        return get(plan_id)
    data = db.uj(p["data"])
    edits = db.uj(p["owner_edits"], {})
    kept = []
    new_proj_inputs = None
    for path, v in rev["changes"].items():
        if path == "_projection_inputs":
            new_proj_inputs = v
            continue
        if path in edits:
            kept.append(path)  # owner edit wins; revision recorded in history only
            continue
        _set_path(data, path, v)
    ts = iso(now_utc())
    if new_proj_inputs:
        proj = finance.project(new_proj_inputs, business.get_profile())
        pid_ = "proj-" + uuid.uuid4().hex[:8]
        db.ex("INSERT INTO projection(id,event_id,plan_id,method,inputs,result,created_at) VALUES(?,?,?,?,?,?,?)",
              (pid_, p["event_id"], plan_id, proj["method"], db.j(new_proj_inputs), db.j(proj), ts))
        data["projection_id"] = pid_
    if "phases" in rev["changes"]:  # resync task rows; completion state is kept for tasks with the same id
        done = {t["id"]: t["done"] for t in db.q("SELECT id, done FROM task WHERE plan_id=?", (plan_id,))}
        db.ex("DELETE FROM task WHERE plan_id=?", (plan_id,))
        for ph in data["phases"]:
            for t in ph["tasks"]:
                db.ex("INSERT INTO task(plan_id,id,phase,title,due_local,owner,done) VALUES(?,?,?,?,?,?,?)",
                      (plan_id, t["id"], ph["key"], t["title"], t["due"], t["owner"], done.get(t["id"], 0)))
    with db.tx() as c:
        c.execute("INSERT INTO plan_version(plan_id,version,data,owner_edits,archived_at,reason,evidence_ids) VALUES(?,?,?,?,?,?,?)",
                  (plan_id, p["version"], p["data"], p["owner_edits"], ts, rev["reason"], db.j(rev["evidence_ids"])))
        c.execute("UPDATE plan SET data=?, version=?, pending_revision=NULL, updated_at=? WHERE id=?",
                  (db.j({**data, "revision_note": {"reason": rev["reason"], "owner_edits_kept": kept,
                                                   "before_after": rev.get("before_after")}}),
                   p["version"] + 1, ts, plan_id))
    return get(plan_id)


def apply_agent_draft(plan_id: str, fields: dict, source_ids: list[str], model: str) -> dict:
    """Agent-written text fields. Numbers are ignored; cited source IDs must exist."""
    valid_src = []
    for sid in source_ids or []:
        if db.q1("SELECT 1 FROM source_item WHERE id=? UNION SELECT 1 FROM news WHERE id=? UNION "
                 "SELECT 1 FROM social_observation WHERE id=?", (sid, sid, sid)):
            valid_src.append(sid)
    p = db.q1("SELECT * FROM plan WHERE id=?", (plan_id,))
    if not p:
        raise ValueError("Unknown plan")
    data = db.uj(p["data"])
    allowed_copy = {"instagram_caption", "story_sequence", "email_subject", "email_body", "sign_copy",
                    "staff_script", "organizer_outreach", "visual_direction"}
    changes = {}
    for k, v in (fields.get("copy") or {}).items():
        if k in allowed_copy and isinstance(v, (str, list)):
            changes[f"copy.{k}"] = v
    for k in ("objective", "positioning", "target_occasion", "how_customers_learn"):
        if isinstance(fields.get(k), str):
            changes[k] = fields[k]
    if isinstance(fields.get("rationale"), str):
        changes["rationale.text"] = fields["rationale"]
        changes["rationale.source_ids"] = valid_src or data["rationale"]["source_ids"]
    if isinstance(fields.get("alternatives"), list):
        changes["alternatives"] = [a for a in fields["alternatives"][:2] if isinstance(a, dict)]
    changes["copy.authored_by"] = f"agent:{model}"
    changes["authored_by"] = f"agent:{model}"
    if p["version"] == 1 and not p["pending_revision"] and p["state"] == "draft" and not db.uj(p["owner_edits"], {}):
        for path, v in changes.items():
            _set_path(data, path, v)
        db.ex("UPDATE plan SET data=?, updated_at=? WHERE id=?", (db.j(data), iso(now_utc()), plan_id))
    else:
        propose_revision(plan_id, "Agent-proposed copy/rationale update", changes, valid_src, "agent_revision")
    return {"applied": list(changes), "valid_source_ids": valid_src,
            "rejected_source_ids": [s for s in source_ids or [] if s not in valid_src]}


def get(plan_id: str) -> dict:
    p = db.q1("SELECT * FROM plan WHERE id=?", (plan_id,))
    if not p:
        raise ValueError("Unknown plan")
    data = db.uj(p["data"])
    edits = db.uj(p["owner_edits"], {})
    tasks = {t["id"]: t for t in db.q("SELECT * FROM task WHERE plan_id=?", (plan_id,))}
    latest = db.q1("SELECT id,inputs FROM projection WHERE plan_id=? ORDER BY created_at DESC, rowid DESC LIMIT 1", (plan_id,))
    if latest:
        current = finance.project(db.uj(latest["inputs"]))
        data["projection_id"] = latest["id"]
        data["projection_summary"] = {key: {k: v for k, v in val.items() if k != "hours"}
                                      for key, val in current["scenarios"].items()}
    eff = effective(data, edits)
    for ph in eff["phases"]:
        for t in ph["tasks"]:
            t["done"] = bool(tasks.get(t["id"], {}).get("done"))
    versions = db.q("SELECT version, archived_at, reason, evidence_ids FROM plan_version WHERE plan_id=? ORDER BY version DESC", (plan_id,))
    ev = db.q1("SELECT status FROM event WHERE id=?", (p["event_id"],))
    return {"id": plan_id, "event_id": p["event_id"], "version": p["version"], "state": p["state"],
            "data": eff, "owner_edits": edits, "pending_revision": db.uj(p["pending_revision"]),
            "versions": versions, "created_at": p["created_at"], "updated_at": p["updated_at"],
            "event_status": ev["status"] if ev else None}


def list_plans() -> list[dict]:
    out = []
    for p in db.q("SELECT id FROM plan ORDER BY updated_at DESC"):
        g = get(p["id"])
        d = g["data"]
        tasks = [t for ph in d["phases"] for t in ph["tasks"]]
        out.append({"id": g["id"], "event_id": g["event_id"], "event_title": d["event_title"], "event_start": d["event_start"],
                    "state": g["state"], "version": g["version"], "budget": d["budget"]["total"],
                    "tasks_done": sum(t["done"] for t in tasks), "tasks_total": len(tasks),
                    "pending_revision": bool(g["pending_revision"]), "event_status": g["event_status"],
                    "authored_by": d.get("authored_by")})
    return out


def toggle_task(plan_id: str, task_id: str, done: bool) -> None:
    db.ex("UPDATE task SET done=? WHERE plan_id=? AND id=?", (int(done), plan_id, task_id))


def to_markdown(plan_id: str) -> str:
    g = get(plan_id)
    d = g["data"]
    L = [f"# Campaign: {d['event_title']}", f"_State: {g['state']} · version {g['version']} · {d['mode']} plan · "
         f"copy by {d['copy'].get('authored_by')}_", "", f"**Objective:** {d['objective']}",
         f"**Target occasion:** {d['target_occasion']}", f"**Positioning:** {d['positioning']}",
         f"**How customers learn about us:** {d['how_customers_learn']}", "",
         f"## Offer\n{d['offer']['name']} — ${d['offer']['price']:.2f} (regular ${d['offer']['regular_price']:.2f}); "
         f"unit cost ${d['offer']['unit_cost']:.2f}; contribution/order ${d['offer']['contribution_per_order']:.2f}. "
         f"{d['offer']['redemption']} {d['offer']['quantity_limit']}.", "", "## Phases"]
    for ph in d["phases"]:
        L.append(f"### {ph['name']} ({fmt_ny(ph['start'])} → {fmt_ny(ph['end'])}) — owner: {ph['owner']}, spend ${ph['spend']:.2f}")
        L.append(f"Objective: {ph['objective']}  \nSuccess: {ph['success_measure']}")
        for t in ph["tasks"]:
            L.append(f"- [{'x' if t['done'] else ' '}] {t['title']} — due {fmt_ny(t['due'])} ({t['owner']})")
    L += ["", "## Copy"] + [f"**{k.replace('_', ' ').title()}:** {v if isinstance(v, str) else '; '.join(v)}"
                            for k, v in d["copy"].items() if k != "authored_by"]
    L += ["", "## Budget"] + [f"- {i['item']}: ${i['amount']:.2f}" for i in d["budget"]["items"]] + \
         [f"**Total ${d['budget']['total']:.2f}** (cap ${d['budget']['cap_event']:.0f}/event; week total "
          f"${d['budget']['week_total']:.2f} of ${d['budget']['cap_week']:.0f})"]
    L += ["", "## Contingencies"] + [f"- **{k}:** {v}" for k, v in d["contingencies"].items()]
    L += ["", f"_{d['disclaimer']}_"]
    return "\n".join(L)

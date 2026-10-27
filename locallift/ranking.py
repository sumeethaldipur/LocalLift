"""Transparent, deterministic opportunity ranking.

Four indicators stay separate: crowd potential, social attention, opportunity fit (this
score) and evidence quality. The score is a heuristic for comparing options; it is
NOT a probability of success.

Score = Σ weight × factor/5 × 100 over KNOWN factors; unknown factors add 0 and lower coverage,
so missing evidence never earns credit. Rankings with coverage < 100% are labelled provisional.

Rubrics (each factor 0–5, or None = unknown):

distance_access (25%)   ≤0.25 mi:5  ≤0.5:4  ≤1.0:3  ≤1.5:2  ≤2.0:1  else 0   (straight-line)
timing_hours (20%)      both pre-event hours (start-2h, start-1h) inside opening hours:5;
                        one:3; none but post-event hour open:2; otherwise 0
menu_occasion (20%)     start <11:00 coffee/pastry:5; 11–16 lunch:4; 16–22 ticketed evening
                        pre-event meal:4, other evening:3; outside hours:1; virtual:0
demand_evidence (15%)   crowd band High(published):5 / High(venue proxy):4 / Medium:3 / Low:2;
                        +0.5/+1 for independent upcoming news (≥1/≥3), +0.5/+1 for sampled
                        social posts (≥5/≥20); cap 5. No crowd, news or social evidence → unknown
lead_time (10%)         ≥7 days:5  3–7d:4  1–3d:3  ≥4h:2  <4h:1
budget_capacity (10%)   min order headroom in pre-event hours ≥20:5 ≥10:4 ≥5:3 >0:2 else 1;
                        capped at 1 if the week's remaining marketing budget < minimum plan cost
"""
from __future__ import annotations

import re
from datetime import timedelta

from . import business, db
from .geo import distance
from .timeutil import iso, now_utc, parse, to_ny, iso_week_key

WEIGHTS = {"distance_access": 0.25, "timing_hours": 0.20, "menu_occasion": 0.20,
           "demand_evidence": 0.15, "lead_time": 0.10, "budget_capacity": 0.10}
LABELS = {"distance_access": "Distance/access fit", "timing_hours": "Timing & opening-hour fit",
          "menu_occasion": "Menu/occasion fit", "demand_evidence": "Credible demand evidence",
          "lead_time": "Preparation lead time", "budget_capacity": "Budget/capacity feasibility"}
CROWD_BANDS = [(5000, "High"), (500, "Medium"), (0, "Low")]
MIN_PLAN_COST = 15.0
TICKETED = ("music", "sports", "arts", "theatre", "comedy", "concert", "family")


def band_for(n: float) -> str:
    for threshold, name in CROWD_BANDS:
        if n >= threshold:
            return name
    return "Low"


SELL_THROUGH = {"low": 0.60, "base": 0.80, "high": 0.95}  # owner assumption: share of capacity actually attending
# Ballpark crowd by event type when nothing better is published (owner-adjustable assumptions, always labelled EST).
# (provider or "*", regex on category/title, low, expected, high, reason)
TYPE_BALLPARK = [
    ("*", r"festival|parade|street fair|fireworks|marathon|concert in the park|block party", 300, 1000, 3000, "a typical outdoor festival or parade"),
    ("nycparks", r"fitness|exercise|yoga|pilates|dance class|shape up", 8, 20, 40, "a typical NYC Parks fitness class"),
    ("nycparks", r"tour|history|nature|education|talk|birding", 10, 25, 50, "a typical guided tour or talk"),
    ("nycparks", r"kids|family|arts & crafts|games|carrousel|story", 15, 40, 100, "a typical family activity in a park"),
    ("nycparks", r"volunteer|it's my park", 10, 25, 60, "a typical volunteer day"),
    ("nycparks", r"", 15, 50, 150, "a typical NYC Parks public event"),
    ("*", r"theatre|theater|performance art|comedy|cabaret|magic", 60, 150, 400, "a typical small theater or comedy room"),
    ("*", r"music|jazz|rock|pop|country|r&b|latin|hip-hop|electronic|classical", 80, 250, 700, "a typical club or small-venue show"),
    ("*", r"sports|basketball|hockey|football|soccer|baseball|boxing|wrestling", 500, 2000, 8000, "a typical non-arena sporting event"),
    ("*", r"", 30, 150, 500, "a typical small local event"),
]



def crowd_for(event_id: str) -> dict:
    """Crowd potential, always with its basis. Order of evidence:
    1. published attendance estimate (claim)            → "Published estimate"
    2. venue capacity (Wikidata/official, claim)        → estimated crowd = capacity × assumed sell-through
    3. event type heuristic (small park/theater events) → band only, labelled rough estimate
    4. nothing                                          → Unknown
    Capacity is never presented as attendance; the estimate shows its assumption."""
    rows = db.q("""SELECT * FROM claim WHERE event_id=? AND numeric_kind IN ('published_attendance_estimate','venue_capacity')
                   AND superseded_by IS NULL AND review_state != 'rejected' ORDER BY created_at DESC""", (event_id,))
    est = [r for r in rows if r["numeric_kind"] == "published_attendance_estimate"]
    cap = [r for r in rows if r["numeric_kind"] == "venue_capacity"]
    if est:
        vals = [r["numeric_value"] for r in est]
        lo, hi = min(vals), max(vals)
        b_lo, b_hi = band_for(lo), band_for(hi)
        return {"band": b_lo if b_lo == b_hi else f"{b_lo}–{b_hi}", "basis": "Published estimate",
                "low": lo, "high": hi, "expected": round((lo + hi) / 2), "estimated": False,
                "source_claims": [r["id"] for r in est]}
    if cap:
        v = cap[0]["numeric_value"]
        exp = {k: round(v * f) for k, f in SELL_THROUGH.items()}
        b_lo, b_hi = band_for(exp["low"]), band_for(exp["high"])
        return {"band": b_lo if b_lo == b_hi else f"{b_lo}–{b_hi}", "basis": "Venue scale proxy",
                "capacity": v, "low": exp["low"], "high": exp["high"], "expected": exp["base"], "estimated": True,
                "source_claims": [cap[0]["id"]],
                "method": f"Venue holds {int(v):,} (source: {cap[0]['statement'].split(' per ')[-1] if ' per ' in cap[0]['statement'] else 'cited source'}); "
                          f"assumes {int(SELL_THROUGH['low']*100)}–{int(SELL_THROUGH['high']*100)}% of seats filled.",
                "note": "Estimate from venue capacity × assumed sell-through — not a published attendance figure."}
    ev = db.q1("SELECT provider, category, title FROM event WHERE id=?", (event_id,)) or {}
    text = f"{ev.get('category') or ''} {ev.get('title') or ''}"
    for prov, pat, lo, exp, hi, why in TYPE_BALLPARK:
        if prov not in ("*", ev.get("provider")) or not re.search(pat, text, re.I):
            continue
        b_lo, b_hi = band_for(lo), band_for(hi)
        return {"band": b_lo if b_lo == b_hi else f"{b_lo}–{b_hi}", "basis": "Typical for this kind of event",
                "low": lo, "high": hi, "expected": exp, "estimated": True, "source_claims": [],
                "method": f"No attendance or venue capacity published; ballpark for {why} ({lo:,}–{hi:,} people).",
                "note": "Rough ballpark from the event type — adjust it in Results if you know better."}
    return {"band": "Unknown", "basis": "No estimate possible", "low": None, "high": None,
            "expected": None, "estimated": False, "source_claims": []}


def news_count(event_id: str) -> int:
    r = db.q1("""SELECT COUNT(DISTINCT n.dedupe_key) c FROM event_news en JOIN news n ON n.id=en.news_id
                 WHERE en.event_id=? AND en.retrospective=0""", (event_id,))
    return int(r["c"]) if r else 0


def latest_social(event_id: str) -> dict | None:
    rows = db.q("""SELECT * FROM social_observation WHERE event_id=? AND status='ok' ORDER BY collected_at DESC""", (event_id,))
    if not rows:
        return None
    by_provider = {}
    for r in rows:
        by_provider.setdefault(r["provider"], r)
    return by_provider


def social_summary(event_id: str) -> dict:
    lp = latest_social(event_id)
    if not lp:
        failed = db.q1("SELECT provider, detail, collected_at FROM social_observation WHERE event_id=? ORDER BY collected_at DESC LIMIT 1", (event_id,))
        return {"label": "Not sampled" if not failed else "Sampling failed", "posts": None,
                "detail": failed["detail"] if failed else "No social sample collected yet."}
    total = sum(r["unique_posts"] or 0 for r in lp.values())
    by_provider = {p: (r["unique_posts"] or 0) for p, r in lp.items()}
    failed = [r["provider"] for r in db.q("""SELECT provider, MAX(collected_at) at FROM social_observation WHERE event_id=? AND status='failed'
                                             GROUP BY provider""", (event_id,)) if r["provider"] not in lp]
    trends = {p: db.uj(r["metrics"], {}).get("trend", {}).get("label", "Trend unavailable") for p, r in lp.items()}
    return {"label": f"{total} sampled posts", "posts": total, "providers": sorted(lp.keys()), "trends": trends,
            "by_provider": by_provider, "unavailable": failed,
            "collected_at": max(r["collected_at"] for r in lp.values())}


def week_committed_budget(week: str, exclude_plan: str | None = None) -> float:
    total = 0.0
    for p in db.q("SELECT p.id, p.data, e.start_utc FROM plan p JOIN event e ON e.id=p.event_id WHERE p.state NOT IN ('archived')"):
        if p["id"] == exclude_plan or not p["start_utc"]:
            continue
        if iso_week_key(parse(p["start_utc"])) == week:
            total += float((db.uj(p["data"], {}).get("budget") or {}).get("total", 0))
    return total


def factors_for(ev: dict, profile: dict) -> dict:
    loc = profile["location"]
    f: dict[str, float | None] = {}
    notes: dict[str, str] = {}
    dist = distance(loc["lat"], loc["lon"], ev.get("lat"), ev.get("lon"))
    if dist is None:
        f["distance_access"] = None
        notes["distance_access"] = "Venue coordinates missing; distance unknown."
    else:
        mi = dist["miles"]
        f["distance_access"] = next(s for lim, s in [(0.25, 5), (0.5, 4), (1.0, 3), (1.5, 2), (2.0, 1), (1e9, 0)] if mi <= lim)
        notes["distance_access"] = f"{mi} mi straight-line from the café."
    start = parse(ev.get("start_utc"))
    open_h, close_h = profile["hours"]["open"], profile["hours"]["close"]
    if not start or ev.get("all_day"):
        f["timing_hours"] = None
        f["menu_occasion"] = None
        notes["timing_hours"] = "Start time not published."
    else:
        s_ny = to_ny(start)
        h = s_ny.hour + s_ny.minute / 60
        pre = [hh for hh in (int(h) - 2, int(h) - 1) if open_h <= hh < close_h]
        end = parse(ev.get("end_utc"))
        post_open = bool(end and open_h <= to_ny(end).hour < close_h)
        f["timing_hours"] = 5 if len(pre) == 2 else 3 if len(pre) == 1 else 2 if post_open else 0
        notes["timing_hours"] = (f"Pre-event hours open: {', '.join(f'{x}:00' for x in pre) or 'none'}"
                                 + ("; post-event hour open." if post_open else "."))
        cat = (ev.get("category") or "").lower()
        if "virtual" in cat or "online" in (ev.get("venue") or "").lower():
            f["menu_occasion"], notes["menu_occasion"] = 0, "Virtual event."
        elif h < open_h + 1 or h > close_h:
            f["menu_occasion"], notes["menu_occasion"] = 1, "Event timing is mostly outside café hours."
        elif h < 11:
            f["menu_occasion"], notes["menu_occasion"] = 5, "Morning event: coffee and pastry occasion."
        elif h < 16:
            f["menu_occasion"], notes["menu_occasion"] = 4, "Midday event: portable lunch occasion."
        elif any(t in cat for t in TICKETED):
            f["menu_occasion"], notes["menu_occasion"] = 4, "Evening ticketed event: quick pre-event meal."
        else:
            f["menu_occasion"], notes["menu_occasion"] = 3, "Evening event: coffee/snack occasion."
    crowd = crowd_for(ev["id"])
    n_news = news_count(ev["id"])
    soc = social_summary(ev["id"])
    base = {"High": 5 if crowd["basis"] == "Published estimate" else 4, "Medium": 3, "Low": 2}.get(
        crowd["band"].split("–")[-1], None)
    if crowd["basis"] == "Typical for this kind of event":
        base = 1.5  # type heuristic: weaker evidence than a cited capacity
    bump = (1 if n_news >= 3 else 0.5 if n_news >= 1 else 0) + (
        1 if (soc.get("posts") or 0) >= 20 else 0.5 if (soc.get("posts") or 0) >= 5 else 0)
    if base is None and bump == 0:
        f["demand_evidence"] = None
        notes["demand_evidence"] = "No attendance, news or social evidence yet."
    else:
        f["demand_evidence"] = min(5.0, (base if base is not None else 1.5) + bump)
        notes["demand_evidence"] = f"Crowd {crowd['band']} ({crowd['basis']}); {n_news} independent upcoming articles; {soc['label']}."
    if start:
        hrs = (start - now_utc()).total_seconds() / 3600
        f["lead_time"] = 5 if hrs >= 168 else 4 if hrs >= 72 else 3 if hrs >= 24 else 2 if hrs >= 4 else 1
        notes["lead_time"] = f"{hrs / 24:.1f} days to prepare."
    else:
        f["lead_time"] = None
    if start:
        d = to_ny(start).date()
        base_h = business.baseline_for_date(d)["hours"]
        cap = profile["capacity_orders_per_hour"]
        s_ny = to_ny(start)
        pre_hours = [hh for hh in (s_ny.hour - 2, s_ny.hour - 1) if open_h <= hh < close_h] or [s_ny.hour]
        head = min(cap - base_h.get(hh, {"orders": cap})["orders"] for hh in pre_hours)
        sc = 5 if head >= 20 else 4 if head >= 10 else 3 if head >= 5 else 2 if head > 0 else 1
        remaining = profile["budget"]["per_week"] - week_committed_budget(iso_week_key(start))
        if remaining < MIN_PLAN_COST:
            sc = min(sc, 1)
        f["budget_capacity"] = sc
        notes["budget_capacity"] = f"~{head:.0f} orders/h headroom before the event; ${remaining:.0f} weekly budget left."
    else:
        f["budget_capacity"] = None
    return {"factors": f, "notes": notes, "crowd": crowd, "social": soc, "news_count": n_news, "distance": dist}


def score(factors: dict) -> tuple[float, float]:
    """Unknown factors contribute nothing (no free credit); coverage shows how much is known.
    The maximum reachable score with the current evidence is coverage × 100."""
    known = {k: v for k, v in factors.items() if v is not None}
    cov = sum(WEIGHTS[k] for k in known)
    s = sum(WEIGHTS[k] * (v / 5) for k, v in known.items()) * 100
    return round(s, 1), round(cov, 2)


def eligibility(ev: dict) -> tuple[bool, str]:
    if re.search(r"\b(premium seating|vip add[- ]?on|not a concert ticket|parking pass|fast lane|club access|ticket upgrade)\b", ev.get("title", ""), re.I):
        return False, "Ticket add-on or seating variant, not a separate event"
    if ev.get("status") == "cancelled":
        return False, "Cancelled"
    if ev.get("dismissed"):
        return False, "Dismissed by owner"
    st = parse(ev.get("start_utc"))
    if st and st < now_utc() - timedelta(hours=1):
        return False, "Historical (event has started or passed)"
    return True, ""


def explain(fx: dict) -> dict:
    f, notes = fx["factors"], fx["notes"]
    contrib = sorted(((WEIGHTS[k] * v / 5, k) for k, v in f.items() if v is not None), reverse=True)
    reasons = [f"{LABELS[k]}: {notes.get(k, '')}" for _, k in contrib[:2]]
    unknown = sorted(((WEIGHTS[k], k) for k, v in f.items() if v is None), reverse=True)
    if unknown:
        unc = f"{LABELS[unknown[0][1]]} is unknown: {notes.get(unknown[0][1], 'no evidence')}"
    elif fx["crowd"]["band"] == "Unknown":
        unc = "Expected attendance is unknown; demand rests on news/social attention only."
    elif fx["crowd"]["basis"] == "Venue scale proxy":
        unc = "Crowd is estimated from venue capacity; actual attendance isn’t published."
    elif fx["crowd"]["basis"] == "Typical for this kind of event":
        unc = "Crowd size is a rough guess from the event type."
    elif not fx["social"].get("posts"):
        unc = "No sampled social discussion yet (absence is not evidence of no demand)."
    else:
        unc = "Conversion of attendees into café customers is an untested assumption."
    skip = None
    if f.get("timing_hours") == 0:
        skip = "Consider skipping: the café is closed for the useful pre/post-event window."
    elif f.get("lead_time") == 1:
        skip = "Very short lead time: only a same-day, zero-cost activation is realistic."
    return {"reasons": reasons, "uncertainty": unc, "skip_suggestion": skip}


def rank_all() -> list[dict]:
    profile = business.get_profile()
    rows = db.q("SELECT * FROM event")
    ts = iso(now_utc())
    results = []
    for ev in rows:
        ok, why = eligibility(ev)
        fx = factors_for(ev, profile)
        st = parse(ev.get("start_utc"))
        if ok and fx["distance"] and fx["distance"]["miles"] > profile["discovery"]["radius_miles"]:
            ok, why = False, "Outside the café discovery radius"
        if ok and st and st > now_utc() + timedelta(days=profile["discovery"]["days_ahead"]):
            ok, why = False, "Outside the discovery window"
        sc, cov = score(fx["factors"])
        ex = explain(fx)
        results.append((ev["id"], ok, why, fx, sc, cov, ex))
    elig = sorted([r for r in results if r[1]], key=lambda r: (-r[4], -r[5]))
    rank_of = {r[0]: i + 1 for i, r in enumerate(elig)}
    with db.tx() as c:
        c.execute("DELETE FROM opportunity")
        for eid, ok, why, fx, sc, cov, ex in results:
            c.execute("""INSERT INTO opportunity(event_id,business_id,factors,score,coverage,provisional,eligible,rank,
                         rationale,uncertainty,crowd,computed_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                      (eid, profile["id"], db.j({"factors": fx["factors"], "notes": fx["notes"], "weights": WEIGHTS,
                                                 "news_count": fx["news_count"], "social": fx["social"],
                                                 "distance": fx["distance"], "ineligible_reason": why}),
                       sc, cov, int(cov < 0.999), int(ok), rank_of.get(eid),
                       db.j({"reasons": ex["reasons"], "skip_suggestion": ex["skip_suggestion"]}),
                       ex["uncertainty"], db.j(fx["crowd"]), ts))
    return [{"event_id": r[0], "score": r[4], "coverage": r[5], "rank": rank_of[r[0]]} for r in elig]

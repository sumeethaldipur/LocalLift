"""News-led developments and the Local Briefing.

A Development groups reporting, official/event-listing changes and social observations about one
evolving story. Briefing items add the café-specific interpretation and a recommended action.

Without a configured model, interpretations here are *rule-based* and labelled as such
(implication_basis='rule_based'); they are never presented as agent research. Agent drafts are
stored separately with review_state='agent_draft_pending_review'.

Briefing priority (editorial, separate from the opportunity score):
  business relevance  affects an active plan +40; linked eligible event +15; located within radius +10
  type                cancellation 30, schedule_change 25, access_change 20, gathering 15, event_news 10
  time to act         effective within 3 days +15, within 7 days +8
  freshness           published/updated within 24 h +10, within 72 h +5
  evidence            ≥2 independent sources +5; official/listing source +5; disputed −5
  local reporting     covered by a news outlet +20
"""
from __future__ import annotations

import hashlib
import re
from datetime import date, datetime, timedelta

from . import business, db
from .geo import distance
from .timeutil import NY, UTC, fmt_ny, iso, now_utc, parse, to_ny

TYPE_PATTERNS = [
    ("cancellation", r"\b(cancel(?:led|ed|s|lation)?|called off|scrapped)\b"),
    ("schedule_change", r"\b(postpon\w*|reschedul\w*|moved to|new date|delayed until|earlier start|start time changed?|pushed back)\b"),
    ("access_change", r"\b(closures?|closed|closing|shut ?down|suspend\w*|detours?|delays?|outage|service changes?|"
                      r"construction|track work|evacuat\w*|police activity|street closure|lane closure|reroute\w*)\b"),
    ("gathering", r"\b(festival|parade|march|rally|celebration|fair|market|block party|concert|tournament|marathon|"
                  r"vigil|protest|pop-?up|premiere|tree lighting|fireworks|halloween|street fair|crowds?)\b"),
]
TYPE_WEIGHT = {"cancellation": 30, "schedule_change": 25, "access_change": 20, "gathering": 15, "event_news": 10}
NEIGHBORHOOD = {
    "madison square garden": "Madison Square Garden", "msg": "Madison Square Garden", "penn station": "Penn Station",
    "moynihan": "Moynihan Train Hall", "herald square": "Herald Square", "koreatown": "Koreatown",
    "garment district": "Garment District", "hudson yards": "Hudson Yards", "chelsea": "Chelsea",
    "34th street": "34th Street", "bryant park": "Bryant Park", "times square": "Times Square",
    "midtown": "Midtown", "seventh avenue": "Seventh Avenue", "7th avenue": "Seventh Avenue",
    "nomad": "NoMad", "flatiron": "Flatiron", "lirr": "LIRR (Penn Station)", "nj transit": "NJ Transit (Penn Station)",
    "amtrak": "Amtrak (Penn Station)", "port authority": "Port Authority",
}
NB_RE = re.compile(r"\b(" + "|".join(re.escape(k) for k in sorted(NEIGHBORHOOD, key=len, reverse=True)) + r")\b", re.I)
MONTHS = {m: i for i, m in enumerate(["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}
DATE_RE = re.compile(r"\b(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sept?|Oct|Nov|Dec)[a-z]*\.?\s+(\d{1,2})(?:st|nd|rd|th)?\b", re.I)
# Multi-topic digests ("Early Addition", newsletters) are not one development; skip rather than misattribute.
ROUNDUP_RE = re.compile(r"^\s*(early addition|morning memo|the morning|evening edition|newsletter|weekend guide|"
                        r"things to do|what to do this|news roundup|daily briefing)\b", re.I)
STOP = {"the", "a", "an", "of", "in", "on", "at", "to", "for", "and", "is", "with", "after", "nyc", "new", "york", "s"}


def classify(text: str) -> str | None:
    for name, pat in TYPE_PATTERNS:
        if re.search(pat, text or "", re.I):
            return name
    return None


def extract_effective_date(text: str, published: datetime | None) -> tuple[str | None, str]:
    """Explicit month-day mentions only. Publication date is never used as the effective date."""
    m = DATE_RE.search(text or "")
    if not m:
        return None, "not stated in source text"
    mon = MONTHS[m.group(1).lower()[:3]]
    day = int(m.group(2))
    ref = (published or now_utc()).astimezone(NY).date()
    year = ref.year
    try:
        d = date(year, mon, day)
        if d < ref - timedelta(days=180):
            d = date(year + 1, mon, day)
    except ValueError:
        return None, "unparseable date mention"
    return d.isoformat(), f"extracted from text ('{m.group(0)}') — inference"


def _known_venues() -> list[dict]:
    rows = db.q("""SELECT venue, AVG(lat) lat, AVG(lon) lon FROM event WHERE venue IS NOT NULL AND lat IS NOT NULL
                   GROUP BY venue""")
    return [r for r in rows if r["venue"] and len(r["venue"]) >= 8]


def locate(text: str) -> dict:
    low = (text or "").lower()
    for v in sorted(_known_venues(), key=lambda r: -len(r["venue"])):
        name = v["venue"].lower().split(" (")[0]
        if len(name) >= 8 and name in low:
            return {"location_text": v["venue"], "location_precision": "venue (from event listing coordinates)",
                    "lat": v["lat"], "lon": v["lon"]}
    m = NB_RE.search(text or "")
    if m:
        return {"location_text": NEIGHBORHOOD[m.group(1).lower()], "location_precision": "neighborhood (no pin)",
                "lat": None, "lon": None}
    return {"location_text": None, "location_precision": "unknown (no pin)", "lat": None, "lon": None}


def _tokens(s: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9]+", (s or "").lower()) if w not in STOP and len(w) > 2}


def _similar(a: str, b: str) -> bool:
    ta, tb = _tokens(a), _tokens(b)
    return bool(ta and tb) and len(ta & tb) / len(ta | tb) >= 0.5


def _add_source(dev_id: str, kind: str, sid: str, stype: str) -> bool:
    cur = db.ex("INSERT OR IGNORE INTO development_source(development_id,source_kind,source_id,source_type,added_at) VALUES(?,?,?,?,?)",
                (dev_id, kind, sid, stype, iso(now_utc())))
    return cur.rowcount == 1


def _bump(dev_id: str, what: str, source_ids: list[str]) -> int:
    d = db.q1("SELECT version FROM development WHERE id=?", (dev_id,))
    v = d["version"] + 1
    ts = iso(now_utc())
    db.ex("UPDATE development SET version=?, last_seen=? WHERE id=?", (v, ts, dev_id))
    db.ex("INSERT INTO development_version(development_id,version,at,what_changed,source_ids) VALUES(?,?,?,?,?)",
          (dev_id, v, ts, what, db.j(source_ids)))
    return v


def _link_events(dev_id: str, news_id: str | None = None, event_id: str | None = None) -> None:
    if event_id:
        db.ex("INSERT OR IGNORE INTO development_event(development_id,event_id,method) VALUES(?,?,?)", (dev_id, event_id, "listing"))
    if news_id:
        for r in db.q("SELECT event_id FROM event_news WHERE news_id=? AND retrospective=0", (news_id,)):
            db.ex("INSERT OR IGNORE INTO development_event(development_id,event_id,method) VALUES(?,?,?)",
                  (dev_id, r["event_id"], "news_term_match"))


def out_of_market(news: dict) -> bool:
    text = f"{news.get('title', '')} {news.get('summary', '')}".lower()
    explicit_nyc = re.search(r"\b(new york|nyc|manhattan|madison square garden|penn station)\b", text)
    elsewhere = re.search(r"\b(los angeles|seoul|london|chicago|atlanta|houston)\b", text)
    la_source = "laist.com" in (news.get("url") or "") or "laist" in (news.get("domain") or "").lower()
    return bool((elsewhere or la_source) and not explicit_nyc)


def ingest_news(provenance: str = "real_source") -> dict:
    """Group relevant local reporting into developments (independent of the event list)."""
    stats = {"considered": 0, "relevant": 0, "new_developments": 0, "merged": 0, "material_updates": 0}
    profile = business.get_profile()
    loc = profile["location"]
    radius = profile["discovery"]["radius_miles"]
    cutoff = iso(now_utc() - timedelta(days=10))
    for n in db.q("SELECT * FROM news WHERE COALESCE(published_at, retrieved_at) >= ? ORDER BY published_at", (cutoff,)):
        stats["considered"] += 1
        if out_of_market(n):
            db.ex("""UPDATE development SET status='out_of_market' WHERE id IN
                     (SELECT development_id FROM development_source WHERE source_kind='news' AND source_id=?)""", (n["id"],))
            continue
        if ROUNDUP_RE.search(n["title"] or ""):
            continue
        text = f"{n['title']}. {n['summary'] or ''}"
        linked = db.q("SELECT event_id FROM event_news WHERE news_id=? AND retrospective=0", (n["id"],))
        place = locate(text)
        near = place["location_precision"].startswith("neighborhood") or (
            place["lat"] is not None and (distance(loc["lat"], loc["lon"], place["lat"], place["lon"]) or {"miles": 99})["miles"] <= radius)
        dtype = classify(text) or ("event_news" if linked else None)
        if not dtype:
            continue
        # Gatherings/access changes must be near the café; a linked event alone is not enough (performers tour
        # elsewhere). Plain event coverage counts only from the local publisher; national/global coverage stays
        # in the event's own "What is being reported" tab instead of the briefing.
        # Search coverage can mention a past concert without announcing a new gathering.
        # Keep these headlines in event evidence, not the actionable neighborhood briefing.
        if dtype == "gathering" and n["provider"] in ("google_news", "gdelt") and linked:
            db.ex("""UPDATE development SET status='background_coverage' WHERE id IN
                     (SELECT development_id FROM development_source WHERE source_kind='news' AND source_id=?)""", (n["id"],))
            continue
        if dtype in ("gathering", "access_change") and not near:
            continue
        if dtype == "event_news" and n["provider"] != "gothamist":
            continue
        if dtype in ("cancellation", "schedule_change") and not (near or linked):
            continue
        stats["relevant"] += 1
        pub = parse(n["published_at"])
        eff, eff_basis = extract_effective_date(text, pub)
        if eff and pub and date.fromisoformat(eff) < to_ny(pub).date():
            continue  # retrospective story; not an upcoming development
        # Same development? exact dedupe key, or similar headline of the same type in the last 4 days.
        existing = db.q1("""SELECT d.* FROM development d JOIN development_source s ON s.development_id=d.id
                            JOIN news n2 ON n2.id=s.source_id WHERE s.source_kind='news' AND n2.dedupe_key=?""", (n["dedupe_key"],))
        if not existing:
            for d in db.q("SELECT * FROM development WHERE dev_type=? AND last_seen>=?",
                          (dtype, iso(now_utc() - timedelta(days=4)))):
                if _similar(d["headline"], n["title"]):
                    existing = d
                    break
        if existing:
            if _add_source(existing["id"], "news", n["id"], "reporting"):
                stats["merged"] += 1
                same_story = db.q1("""SELECT 1 FROM development_source s JOIN news n2 ON n2.id=s.source_id
                                      WHERE s.development_id=? AND s.source_kind='news' AND n2.dedupe_key=? AND n2.id!=?""",
                                   (existing["id"], n["dedupe_key"], n["id"]))
                if not same_story:  # a genuinely new report (not a syndicated copy) is a material update
                    _bump(existing["id"], f"New report: {n['title']} ({n['domain']})", [n["id"]])
                    stats["material_updates"] += 1
                    _on_material(existing["id"], f"New reporting: {n['title']}", [n["id"]])
                _link_events(existing["id"], news_id=n["id"])
            continue
        dev_id = "dev-" + hashlib.sha1(n["id"].encode()).hexdigest()[:10]
        ts = iso(now_utc())
        db.ex("""INSERT OR IGNORE INTO development(id,headline,dev_type,summary,source_types,location_text,location_precision,lat,lon,
                 published_at,effective_start,effective_basis,first_seen,last_seen,last_checked,version,verification_state,status,
                 cluster_key,provenance) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,1,?,?,?,?)""",
              (dev_id, n["title"], dtype, (n["summary"] or "")[:400], db.j(["reporting"]), place["location_text"],
               place["location_precision"], place["lat"], place["lon"], n["published_at"], eff, eff_basis, ts, ts, ts,
               "reported (single source)", "open", n["dedupe_key"], provenance))
        _add_source(dev_id, "news", n["id"], "reporting")
        db.ex("INSERT INTO development_version(development_id,version,at,what_changed,source_ids) VALUES(?,?,?,?,?)",
              (dev_id, 1, ts, f"First reported by {n['domain']}", db.j([n["id"]])))
        _link_events(dev_id, news_id=n["id"])
        stats["new_developments"] += 1
        if dtype in ("cancellation", "schedule_change"):
            from . import jobs
            jobs.request_verification(dev_id)
    for d in db.q("SELECT id FROM development"):
        n_src = db.q1("SELECT COUNT(DISTINCT n.dedupe_key) c FROM development_source s JOIN news n ON n.id=s.source_id "
                      "WHERE s.development_id=? AND s.source_kind='news'", (d["id"],))["c"]
        cur = db.q1("SELECT verification_state FROM development WHERE id=?", (d["id"],))["verification_state"]
        if cur.startswith("reported") and n_src >= 2:
            db.ex("UPDATE development SET verification_state='corroborated (multiple outlets)' WHERE id=?", (d["id"],))
    return stats


def ingest_official(items: list[dict], provenance: str = "real_source") -> dict:
    """Official records (city permits, MTA alerts) become developments directly — no ticket listing needed."""
    stats = {"new_developments": 0, "updated": 0}
    ts = iso(now_utc())
    for it in items:
        db.ex("""INSERT INTO source_item(id,provider,kind,url,title,published_at,retrieved_at,content,access_status,license,provenance)
                 VALUES(?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET retrieved_at=excluded.retrieved_at,
                 content=excluded.content, title=excluded.title""",
              (it["id"], it["provider"], "official", it["url"], it["title"], it.get("published_at"), ts,
               db.j({"text": it["summary"], **it.get("raw", {}), "effective_start": it.get("effective_start"),
                     "effective_end": it.get("effective_end")}), "ok", "official open data; facts only", provenance))
        dev_id = f"dev-off-{it['cluster']}"
        prev = db.q1("SELECT * FROM development WHERE id=?", (dev_id,))
        if prev:
            _add_source(dev_id, "official", it["id"], it["source_type"])
            db.ex("UPDATE development SET headline=?, summary=?, location_text=? WHERE id=?",
                  (it["title"], it["summary"], it["location_text"], dev_id))
            if prev["effective_start"] != it.get("effective_start"):
                db.ex("UPDATE development SET effective_start=?, last_checked=? WHERE id=?", (it.get("effective_start"), ts, dev_id))
                _bump(dev_id, f"Official record changed time: {prev['effective_start']} → {it.get('effective_start')}", [it["id"]])
                _on_material(dev_id, f"Updated: {it['title']}", [it["id"]])
                stats["updated"] += 1
            else:
                db.ex("UPDATE development SET last_checked=? WHERE id=?", (ts, dev_id))
            continue
        db.ex("""INSERT INTO development(id,headline,dev_type,summary,source_types,location_text,location_precision,lat,lon,
                 published_at,effective_start,effective_basis,first_seen,last_seen,last_checked,version,verification_state,status,
                 cluster_key,provenance) VALUES(?,?,?,?,?,?,?,NULL,NULL,?,?,?,?,?,?,1,?,?,?,?)""",
              (dev_id, it["title"], it["dev_type"], it["summary"], db.j([it["source_type"]]), it["location_text"],
               it["location_precision"], it.get("published_at"), it.get("effective_start"),
               "from the official record", ts, ts, ts, "official record", "open", it["cluster"], provenance))
        _add_source(dev_id, "official", it["id"], it["source_type"])
        db.ex("INSERT INTO development_version(development_id,version,at,what_changed,source_ids) VALUES(?,?,?,?,?)",
              (dev_id, 1, ts, f"First seen in {it['provider']} official record", db.j([it["id"]])))
        stats["new_developments"] += 1
    return stats


def from_event_change(event_id: str, kind: str, version: int, before: dict, after: dict) -> str:
    """A change in a primary event listing is itself a development (source type: event listing)."""
    ev = db.q1("SELECT * FROM event WHERE id=?", (event_id,))
    dev_id = f"dev-ev-{hashlib.sha1(f'{event_id}:{kind}:{version}'.encode()).hexdigest()[:10]}"
    ts = iso(now_utc())
    dtype = {"cancellation": "cancellation", "rescheduling": "schedule_change", "relocation": "schedule_change"}.get(kind, "event_news")
    head = {"cancellation": f"{ev['title']} is listed as cancelled",
            "rescheduling": f"{ev['title']} changed: {fmt_ny(before.get('start_utc'))} → {fmt_ny(after.get('start_utc'))}",
            "relocation": f"{ev['title']} moved: {before.get('venue')} → {after.get('venue')}"}.get(kind, f"{ev['title']}: listing updated")
    # Reuse an open development for the same event (e.g. one first reported by news) so coverage merges.
    existing = db.q1("""SELECT d.id FROM development d JOIN development_event de ON de.development_id=d.id
                        WHERE de.event_id=? AND d.dev_type=? AND d.status='open'""", (event_id, dtype))
    if existing:
        dev_id = existing["id"]
        db.ex("""UPDATE development SET verification_state='verified (primary listing)', last_seen=?, last_checked=?,
                 source_types=? WHERE id=?""", (ts, ts, db.j(["reporting", "event listing"]), dev_id))
        _bump(dev_id, f"Primary listing confirms: {head}", [f"src:{event_id}"])
    else:
        db.ex("""INSERT OR IGNORE INTO development(id,headline,dev_type,summary,source_types,location_text,location_precision,lat,lon,
                 published_at,effective_start,effective_basis,first_seen,last_seen,last_checked,version,verification_state,status,
                 cluster_key,provenance) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,1,?,?,?,?)""",
              (dev_id, head, dtype, f"Detected by comparing the provider listing (version {version}) with the previous check.",
               db.j(["event listing"]), ev["venue"], "venue (from event listing coordinates)" if ev["lat"] is not None else "unknown (no pin)",
               ev["lat"], ev["lon"], ts, (after.get("start_utc") or ev["start_utc"]), "from primary event listing",
               ts, ts, ts, "verified (primary listing)", "open", f"event:{event_id}", ev["provenance"]))
        db.ex("INSERT INTO development_version(development_id,version,at,what_changed,source_ids) VALUES(?,?,?,?,?)",
              (dev_id, 1, ts, head, db.j([f"src:{event_id}"])))
    _add_source(dev_id, "event", f"src:{event_id}", "event listing")
    _link_events(dev_id, event_id=event_id)
    return dev_id


def _on_material(dev_id: str, what: str, source_ids: list[str]) -> None:
    d = db.q1("SELECT followed FROM development WHERE id=?", (dev_id,))
    plans_ = affected_plans(dev_id)
    if d and (d["followed"] or plans_):
        from . import notify
        v = db.q1("SELECT version FROM development WHERE id=?", (dev_id,))["version"]
        notify.add(f"dev:{dev_id}:v{v}", "evidence_change", what, None, impact="Review the development in the briefing.",
                   development_id=dev_id, affected_plans=plans_, source_ids=source_ids)


def affected_plans(dev_id: str) -> list[str]:
    return [r["id"] for r in db.q("""SELECT p.id FROM plan p JOIN development_event de ON de.event_id=p.event_id
                                     WHERE de.development_id=? AND p.state NOT IN ('archived','completed')""", (dev_id,))]


# ------------------------------------------------------------------ briefing

def _rule_based_item(d: dict, profile: dict) -> dict:
    loc = profile["location"]
    dist = distance(loc["lat"], loc["lon"], d["lat"], d["lon"])
    events = db.q("""SELECT e.*, o.eligible, o.rank FROM development_event de JOIN event e ON e.id=de.event_id
                     LEFT JOIN opportunity o ON o.event_id=e.id WHERE de.development_id=?""", (d["id"],))
    plans_ = affected_plans(d["id"])
    eff = d["effective_start"]
    eff_dt = parse(eff) if eff and "T" in eff else (datetime.fromisoformat(eff).replace(tzinfo=NY) if eff else None)
    n_sources = db.q1("SELECT COUNT(*) c FROM development_source WHERE development_id=?", (d["id"],))["c"]
    types = db.uj(d["source_types"], [])
    t = d["dev_type"]
    where = d["location_text"] or "location not stated"
    if dist:
        where += f" · {dist['miles']} mi straight-line"
    hours = profile["hours"]
    in_hours = bool(eff_dt and "T" in (eff or "") and hours["open"] <= to_ny(eff_dt).hour < hours["close"])
    when = (fmt_ny(eff_dt) if eff and "T" in eff else (eff_dt.strftime("%a %b %-d") if eff_dt else "Not stated"))
    place = d["location_text"].split(",")[0] if d["location_text"] else "nearby"
    time_known = bool(eff and "T" in eff)
    soon = ((eff_dt.astimezone(UTC) - now_utc()).total_seconds() / 86400) if eff_dt else None
    if eff_dt:
        dd = (to_ny(eff_dt).date() - to_ny(now_utc()).date()).days
        rel = "today" if dd <= 0 else "tomorrow" if dd == 1 else f"on {to_ny(eff_dt).strftime('%A')}" if dd < 7 else f"on {when}"
    else:
        rel = "soon"
    # Why it matters (rule-based inference, labelled) and what to do.
    if t == "cancellation":
        imp = ("Removes expected visitors for the linked event; any campaign for it should stand down."
               if events else "May reduce foot traffic if it concerns a nearby gathering; no linked café plan.")
        action = "Review affected plan" if plans_ else ("Monitor" if events else "No action")
        why = "A cancellation is a risk, not a marketing opportunity; it is excluded from the shortlist."
    elif t == "schedule_change":
        imp = "Changes when visitors arrive; pre-event offer windows and prep timing may need to move."
        action = "Review affected plan" if plans_ else "Investigate"
        why = "Verify against the primary listing before changing any timing."
    elif t == "access_change":
        imp = (f"Could make it harder to reach the café around {place}" + (f" on {when}" if eff_dt else "")
               + " — check staff commutes and any campaign that day.")
        if plans_:
            action, why = "Review affected plan", "A campaign you planned falls in this window; check its timing."
        elif soon is not None and soon <= 2:
            action, why = "Prepare", (f"It's {rel}: tell staff, expect fewer walk-ins from that direction, "
                                       "and don't schedule a promo that depends on that route.")
        else:
            action, why = "Monitor", "Not urgent yet — follow it and re-check closer to the date."
    elif t == "gathering":
        elig = [e for e in events if e["eligible"]]
        street = bool(re.search(r"street|block party|festival|fair|parade|market|plaza", d["headline"] or "", re.I))
        if eff_dt and in_hours:
            imp = f"People gathering at {place} on {when}, while you're open — a chance for extra walk-ins."
        elif eff_dt:
            imp = f"People gathering at {place} on {when}; it may fall outside your opening hours."
        else:
            imp = f"A new gathering at {place}; no date yet, so the impact on your café is unclear."
        if elig:
            action, why = "Build a campaign", "It's linked to one of your ranked opportunities — plan it properly."
        elif not eff_dt:
            action, why = "Investigate", "No date yet — confirm when and where it happens before doing anything."
        elif time_known and not in_hours:
            action, why = "No action", f"It's at {fmt_ny(eff_dt, False)}, when you're closed — skip it."
        elif soon is not None and soon <= 3:
            action = "Quick promo"
            why = (f"It's {rel}{' while you’re open' if time_known else ' (time not stated)'}. A sidewalk sign and an Instagram story cost $0 and can catch "
                   f"people {'walking to the street event' if street else 'heading there'} — no campaign needed.")
        else:
            action, why = "Monitor", f"It's {rel} — follow it and decide on a quick promo closer to the day."
    else:
        imp = "New coverage of a linked upcoming event; may indicate attention but not attendance."
        action = "Investigate" if events else "No action"
        why = "More coverage does not automatically mean more customers."
    limits = []
    if n_sources < 2:
        limits.append("single source")
    if not eff:
        limits.append("effective date not stated")
    if d["lat"] is None:
        limits.append("no precise location (not pinned)")
    if "event listing" not in types and t in ("cancellation", "schedule_change"):
        limits.append("not yet confirmed by the primary listing")
    disputed = db.q1("SELECT 1 FROM claim c JOIN development_event de ON de.event_id=c.event_id WHERE de.development_id=? "
                     "AND c.classification='disputed'", (d["id"],))
    certainty = {"verification": d["verification_state"], "sources": n_sources, "source_types": types,
                 "limitations": limits, "conflicts": bool(disputed),
                 "would_change": {"cancellation": "The organizer or listing reinstating the event.",
                                  "schedule_change": "The primary listing showing the original time.",
                                  "access_change": "An official notice that service/access is restored.",
                                  "gathering": "Confirmed date, place inside café hours, and an organizer size estimate.",
                                  "event_news": "Reporting that changes the event's time, place or status."}.get(t, "")}
    # Editorial priority
    pf = {"affects_plan": 40 if plans_ else 0,
          "linked_eligible_event": 15 if any(e["eligible"] for e in events) else 0,
          "located_within_radius": 10 if dist and dist["miles"] <= profile["discovery"]["radius_miles"] else 0,
          "type": TYPE_WEIGHT.get(t, 5)}
    if eff_dt:
        days = (eff_dt.astimezone(UTC) - now_utc()).total_seconds() / 86400
        pf["time_to_act"] = 15 if 0 <= days <= 3 else 8 if 0 <= days <= 7 else 0
    else:
        pf["time_to_act"] = 0
    last = parse(d["last_seen"]) or parse(d["published_at"])
    age_h = (now_utc() - last).total_seconds() / 3600 if last else 999
    pf["freshness"] = 10 if age_h <= 24 else 5 if age_h <= 72 else 0
    pf["local_reporting"] = 20 if "reporting" in types else 0  # a journalist found it newsworthy
    pf["evidence"] = (5 if n_sources >= 2 else 0) + (5 if "event listing" in types or "official announcement" in types else 0) - (5 if disputed else 0)
    return {"summary": d["summary"] or d["headline"], "when": when, "when_basis": d["effective_basis"], "where": where,
            "implication": imp, "implication_basis": "rule_based", "action": action, "action_rationale": why,
            "certainty": certainty, "urgency": sum(pf.values()), "priority_factors": pf,
            "affected": {"events": [e["id"] for e in events], "plans": plans_}}


def build_briefing() -> int:
    profile = business.get_profile()
    ts = iso(now_utc())
    # Close developments whose effective time is well past.
    for d in db.q("SELECT id, effective_start FROM development WHERE status='open' AND effective_start IS NOT NULL"):
        e = d["effective_start"]
        end = parse(e) if "T" in e else datetime.fromisoformat(e).replace(tzinfo=NY) + timedelta(days=1)
        if end and end < now_utc() - timedelta(hours=12):
            db.ex("UPDATE development SET status='past' WHERE id=?", (d["id"],))
    n = 0
    for d in db.q("SELECT * FROM development WHERE status='open'"):
        item = _rule_based_item(d, profile)
        prev = db.q1("SELECT * FROM briefing_item WHERE development_id=?", (d["id"],))
        review = prev["review_state"] if prev and prev["review_state"] == "agent_draft_pending_review" else "auto_rule_based"
        fields = (item["summary"], db.j({"text": item["implication"], "when": item["when"], "when_basis": item["when_basis"],
                                         "where": item["where"], "affected": item["affected"]}),
                  item["implication_basis"], item["action"], item["action_rationale"], db.j(item["certainty"]),
                  item["urgency"], db.j(item["priority_factors"]), review, ts)
        if prev:  # `seen` stores the development version the owner last saw (0 = never)
            db.ex("""UPDATE briefing_item SET summary=?, implication=?, implication_basis=?, action=?, action_rationale=?,
                     certainty=?, urgency=?, priority_factors=?, review_state=?, updated_at=? WHERE development_id=?""",
                  (*fields, d["id"]))
        else:
            db.ex("""INSERT INTO briefing_item(id,business_id,development_id,summary,implication,implication_basis,action,
                     action_rationale,certainty,urgency,priority_factors,review_state,updated_at,generated_at,seen)
                     VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,0)""",
                  ("bi-" + d["id"][4:], profile["id"], d["id"], *fields, ts))
        n += 1
    return n


def briefing(limit: int = 5) -> list[dict]:
    rows = db.q("""SELECT b.*, d.headline, d.dev_type, d.source_types, d.location_text, d.location_precision, d.lat, d.lon,
                   d.published_at, d.effective_start, d.last_seen, d.last_checked, d.version, d.verification_state, d.followed,
                   d.first_seen FROM briefing_item b JOIN development d ON d.id=b.development_id
                   WHERE d.status='open' ORDER BY b.urgency DESC, (d.source_types LIKE "%reporting%") DESC, d.effective_start ASC LIMIT ?""", (limit,))
    return [hydrate(r) for r in rows]


def hydrate(r: dict) -> dict:
    r["implication"] = db.uj(r["implication"], {})
    r["certainty"] = db.uj(r["certainty"], {})
    r["priority_factors"] = db.uj(r["priority_factors"], {})
    r["source_types"] = db.uj(r["source_types"], [])
    r["agent_draft"] = db.uj(r.get("agent_draft"))
    r["sources"] = sources(r["development_id"])
    return r


def sources(dev_id: str) -> list[dict]:
    out = []
    for s in db.q("SELECT * FROM development_source WHERE development_id=? ORDER BY added_at", (dev_id,)):
        if s["source_kind"] == "news":
            n = db.q1("SELECT id, title, url, domain, provider, published_at, retrieved_at, dedupe_key FROM news WHERE id=?", (s["source_id"],))
            if n:
                out.append({**n, "source_type": s["source_type"], "kind": "news"})
        else:
            si = db.q1("SELECT id, title, url, provider, retrieved_at FROM source_item WHERE id=?", (s["source_id"],))
            if si:
                out.append({**si, "source_type": s["source_type"], "kind": s["source_kind"], "published_at": None})
    return out


def history(dev_id: str) -> dict:
    d = db.q1("SELECT * FROM development WHERE id=?", (dev_id,))
    if not d:
        raise ValueError("Unknown development")
    b = db.q1("SELECT * FROM briefing_item WHERE development_id=?", (dev_id,))
    events = db.q("SELECT e.id, e.title, e.start_utc, e.status, e.venue FROM development_event de JOIN event e ON e.id=de.event_id "
                  "WHERE de.development_id=?", (dev_id,))
    plan_versions = []
    for pid in affected_plans(dev_id):
        p = db.q1("SELECT id, version, state, pending_revision FROM plan WHERE id=?", (pid,))
        plan_versions.append({**p, "pending_revision": db.uj(p["pending_revision"]),
                              "archived_versions": db.q("SELECT version, archived_at, reason FROM plan_version WHERE plan_id=?", (pid,))})
    return {"development": {**d, "source_types": db.uj(d["source_types"], [])},
            "briefing_item": hydrate(dict(b, **{k: d[k] for k in ("headline", "dev_type", "source_types", "lat", "lon")})) if b else None,
            "versions": db.q("SELECT version, at, what_changed, source_ids FROM development_version WHERE development_id=? ORDER BY version", (dev_id,)),
            "sources": sources(dev_id), "events": events, "plans": plan_versions,
            "decisions": [{**x, "before": db.uj(x["before"]), "after": db.uj(x["after"]), "evidence_ids": db.uj(x["evidence_ids"], []),
                           "verification": db.uj(x["verification"])}
                          for x in db.q("SELECT * FROM decision WHERE development_id=? ORDER BY at", (dev_id,))]}


def save_agent_draft(dev_id: str, fields: dict, source_ids: list[str], model: str) -> dict:
    b = db.q1("SELECT id FROM briefing_item WHERE development_id=?", (dev_id,))
    if not b:
        raise ValueError("Unknown development or no briefing item yet")
    valid = [s for s in source_ids or [] if db.q1(
        "SELECT 1 FROM news WHERE id=? UNION SELECT 1 FROM source_item WHERE id=? UNION SELECT 1 FROM social_observation WHERE id=?",
        (s, s, s))]
    draft = {k: fields[k] for k in ("summary", "implication", "action", "action_rationale", "limitations", "would_change")
             if isinstance(fields.get(k), str)}
    draft.update({"source_ids": valid, "model": model, "at": iso(now_utc())})
    db.ex("UPDATE briefing_item SET agent_draft=?, review_state='agent_draft_pending_review', updated_at=? WHERE id=?",
          (db.j(draft), iso(now_utc()), b["id"]))
    return {"saved": True, "valid_source_ids": valid,
            "rejected_source_ids": [s for s in source_ids or [] if s not in valid], "review_state": "agent_draft_pending_review"}

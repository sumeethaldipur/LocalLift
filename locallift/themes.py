"""Event-themed specials built from the existing menu.

The AI proposes a fan-inspired name, pairing and one-line story for the event (e.g. a song or team-colour
reference). Code then validates every item against the real menu, caps any new add-on ingredient, and
computes price, cost and margin. Without a model, a category-based themed default is used.
Specials are labelled unofficial and always need owner approval; no artist, team or venue name appears in the
product name, so nothing implies endorsement.
"""
from __future__ import annotations

import math
import re

from . import business, db, finance
from .timeutil import parse, to_ny

DRINKS = {"espresso", "latte", "cold_brew", "matcha_latte", "ov_cold_brew"}
SNACKS = {"croissant", "cookie"}
DISCOUNT = 0.15          # specials are ~15% under buying the items separately
MAX_ADDON_COST = 0.40    # per-serving cost cap for one new garnish/syrup (needs owner approval)

DEFAULTS = [  # (category regex, drink, snack, name, story)
    (r"music|concert|pop|rock|jazz|r&b|country|latin|hip-hop", "ov_cold_brew", "cookie",
     "Encore Cold Brew + Cookie", "A quick, cold, sweet pick-me-up you can carry into the show."),
    (r"basketball|hockey|sports", "ov_cold_brew", "cookie",
     "Game-Day Cold Brew + Cookie", "Grab-and-go fuel for the walk to the game."),
    (r"theatre|theater|broadway|musical|comedy|performance", "latte", "cookie",
     "Curtain-Call Latte + Cookie", "A warm drink and a treat before curtain up."),
]


def _price(regular: float) -> float:
    return max(0.25, math.floor(regular * (1 - DISCOUNT) * 4) / 4)  # round down to the nearest $0.25


def build(items: list[str], name: str, story: str, tagline: str, addon: dict | None, source: str, profile: dict) -> dict:
    menu = [business.menu_item(i, profile) for i in items]
    addon_cost = float(addon["cost"]) if addon else 0.0
    regular = round(sum(m["price"] for m in menu) + (0.50 if addon else 0), 2)
    unit_cost = round(sum(m["unit_cost"] for m in menu) + addon_cost, 2)
    price = _price(regular)
    fee = profile["payment_fee_rate"]
    return {
        "name": name, "items": items, "price": price, "regular_price": regular, "unit_cost": unit_cost,
        "payment_fee": round(price * fee, 2), "contribution_per_order": finance.offer_unit_contribution(price, unit_cost, fee),
        "prep_minutes": max(m["prep_min"] for m in menu) + (1 if addon else 0), "occasion": "themed",
        "theme": {"story": story, "tagline": tagline, "source": source, "unofficial": True,
                  "components": " + ".join(m["name"] for m in menu) + (f" + {addon['name']}" if addon else ""),
                  "addon": addon},
    }


def default_offer(ev: dict, profile: dict) -> dict:
    text = f"{ev.get('category') or ''} {ev.get('title') or ''}"
    for pat, drink, snack, name, story in DEFAULTS:
        if re.search(pat, text, re.I):
            return build([drink, snack], name, story, "Fan-inspired special — not affiliated with the event.", None, "template", profile)
    return build(["latte", "croissant"], "Opening Act Combo", "A quick coffee and pastry on the way.", "", None, "template", profile)


PROMPT = """You name a fan-inspired special for a small independent café next to an event venue. Return ONLY JSON.

Event: {title}
Performer/team: {performers}
Category: {category}
Venue: {venue}
Starts (local): {start}

Menu (id: name, price):
{menu}

Rules:
- Pick exactly one drink id from: {drinks}; optionally one snack id from: {snacks}. Use ids exactly.
- Make it clearly relevant to THIS event: e.g. reference a well-known song, album, tour theme, team colours or nickname.
- The product "name" must NOT contain the artist's, team's, venue's or tour's actual name (no implied endorsement); a song or
  nickname reference is fine. Max 40 characters.
- Optional "addon": ONE cheap garnish or syrup (e.g. "mint leaves", "watermelon syrup") with estimated per-serving "cost" in USD
  (max {max_addon}), only if it makes the theme work. Otherwise null.
- Reference song/album TITLES only — never quote song lyrics (copyright).
- "story": one plain sentence (max 140 chars) explaining the reference to a customer. "tagline": max 70 chars for a sign,
  in the café's own words.

JSON shape: {{"name": "...", "drink": "...", "snack": "..." or null, "addon": {{"name": "...", "cost": 0.3}} or null, "story": "...", "tagline": "..."}}"""


def ai_offer(ev: dict, profile: dict) -> dict | None:
    from .agent.providers import BudgetExceeded, complete_json
    performers = ", ".join(db.uj(ev.get("performers"), []) or []) or ev["title"]
    menu = "\n".join(f"{m['id']}: {m['name']}, ${m['price']:.2f}" for m in profile["menu"])
    prompt = PROMPT.format(title=ev["title"], performers=performers, category=ev.get("category") or "", venue=ev.get("venue") or "",
                           start=to_ny(parse(ev["start_utc"])).strftime("%a %b %-d, %-I:%M %p"),
                           menu=menu, drinks=", ".join(sorted(DRINKS)), snacks=", ".join(sorted(SNACKS)), max_addon=MAX_ADDON_COST)
    try:
        data, model = complete_json(prompt)
    except BudgetExceeded:
        return None
    if not data:
        return None
    drink, snack = data.get("drink"), data.get("snack")
    if drink not in DRINKS or (snack and snack not in SNACKS):
        return None  # model picked something not on the menu: reject rather than invent
    name = str(data.get("name") or "").strip()[:40]
    banned = [w for w in re.findall(r"[a-z]{4,}", " ".join([ev["title"], performers, ev.get("venue") or ""]).lower())
              if w not in {"together", "with", "night", "live", "tour", "show", "vs", "the", "york", "square"}]
    if not name or any(re.search(rf"\b{re.escape(w)}\b", name.lower()) for w in banned):
        return None  # empty, or uses the artist/team/venue name
    addon = data.get("addon") if isinstance(data.get("addon"), dict) else None
    if addon:
        try:
            addon = {"name": str(addon["name"])[:40], "cost": min(float(addon["cost"]), MAX_ADDON_COST)}
        except (KeyError, TypeError, ValueError):
            addon = None
    items = [drink] + ([snack] if snack else [])
    return build(items, name, str(data.get("story") or "")[:160], str(data.get("tagline") or "")[:80], addon, f"ai:{model}", profile)


def offer_for_event(ev: dict, profile: dict, refresh: bool = False) -> dict:
    """Themed special for an evening/ticketed event, cached per event so the AI isn't called repeatedly."""
    key = f"theme:{ev['id']}"
    cached = None if refresh else db.get_meta(key)
    if cached:
        return db.uj(cached)
    offer = ai_offer(ev, profile) or default_offer(ev, profile)
    db.set_meta(key, db.j(offer))
    return offer

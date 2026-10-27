"""Venue capacity from Wikidata (free, structured, cited). Used only as a *venue scale proxy*:
capacity is not attendance. Capacity is chosen per use (P518 qualifier) when Wikidata records one,
e.g. Madison Square Garden: concert 20,000 / basketball 19,812 / ice hockey 18,006."""
from __future__ import annotations

import re

from .http import SourceError, request, set_status

API = "https://www.wikidata.org/w/api.php"
NYC_PLACES = {"Q11299", "Q60", "Q18419", "Q18424", "Q18426", "Q18432"}  # Manhattan, NYC, boroughs
USE = {  # P518 "applies to part" values
    "concert": "Q182832", "basketball": "Q5372", "hockey": "Q41466", "boxing": "Q32112", "wrestling": "Q131359",
}


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (s or "").lower()).strip()


def _use_for(category: str) -> str | None:
    c = (category or "").lower()
    if "basketball" in c:
        return USE["basketball"]
    if "hockey" in c:
        return USE["hockey"]
    if "boxing" in c:
        return USE["boxing"]
    if "wrestling" in c:
        return USE["wrestling"]
    if "music" in c or "concert" in c:
        return USE["concert"]
    return None


def venue_capacity(name: str, category: str = "") -> dict | None:
    """Return {qid, label, capacity, use, url, all} for a NYC venue, or None if no confident match."""
    tries = [name]
    if " at " in name:
        tries.append(name.split(" at ")[0])
    for q in tries:
        try:
            hits = request("wikidata", "GET", API, params={"action": "wbsearchentities", "search": q, "language": "en",
                                                           "format": "json", "limit": 5}, retries=1).json().get("search", [])
        except SourceError as e:
            set_status("wikidata", "events", "unavailable", str(e), "not_required")
            return None
        for h in hits:
            if _norm(h.get("label")) != _norm(q):
                continue
            ent = request("wikidata", "GET", API, params={"action": "wbgetentities", "ids": h["id"], "props": "claims",
                                                          "format": "json"}, retries=1).json()["entities"][h["id"]]
            claims = ent.get("claims", {})
            places = {c["mainsnak"].get("datavalue", {}).get("value", {}).get("id") for c in claims.get("P131", [])}
            desc = (h.get("description") or "").lower()
            if not (places & NYC_PLACES or "new york" in desc or "manhattan" in desc):
                continue
            caps = []
            for c in claims.get("P1083", []):
                amt = c["mainsnak"].get("datavalue", {}).get("value", {}).get("amount")
                if not amt:
                    continue
                uses = [v.get("datavalue", {}).get("value", {}).get("id") for v in c.get("qualifiers", {}).get("P518", [])]
                caps.append({"capacity": int(float(amt)), "use": uses[0] if uses else None})
            if not caps:
                continue
            # Plausibility: Wikidata has errors (e.g. a Broadway house recorded at 15,408 seats). No NYC theater
            # exceeds ~6,000 (Radio City Music Hall ≈ 5,945), so larger "theater" capacities are rejected.
            if re.search(r"theat", f"{h.get('label', '')} {desc}", re.I):
                caps = [c for c in caps if c["capacity"] <= 6000]
                if not caps:
                    continue
            want = _use_for(category)
            pick = next((c for c in caps if want and c["use"] == want), None) or max(caps, key=lambda c: c["capacity"])
            set_status("wikidata", "events", "healthy", f"Venue capacity lookups working (last: {h.get('label')}).",
                       "not_required", success=True)
            return {"qid": h["id"], "label": h.get("label"), "description": h.get("description"),
                    "capacity": pick["capacity"], "use": pick["use"], "all": caps,
                    "url": f"https://www.wikidata.org/wiki/{h['id']}"}
    return None

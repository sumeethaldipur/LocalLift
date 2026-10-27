"""Straight-line (great-circle) distance. Not a walking route."""
from __future__ import annotations

import math

EARTH_RADIUS_MI = 3958.7613
KM_PER_MI = 1.609344


def haversine_miles(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = p2 - p1
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * EARTH_RADIUS_MI * math.asin(min(1.0, math.sqrt(a)))


def distance(lat1, lon1, lat2, lon2) -> dict | None:
    if None in (lat1, lon1, lat2, lon2):
        return None
    mi = haversine_miles(lat1, lon1, lat2, lon2)
    return {"miles": round(mi, 2), "km": round(mi * KM_PER_MI, 2), "label": "straight-line distance"}


_B32 = "0123456789bcdefghjkmnpqrstuvwxyz"


def geohash(lat: float, lon: float, precision: int = 9) -> str:
    """Encode a geohash (Ticketmaster `geoPoint` replaces the deprecated `latlong`)."""
    lat_r, lon_r = [-90.0, 90.0], [-180.0, 180.0]
    out, bits, bit, even = [], 0, 0, True
    while len(out) < precision:
        rng, val = (lon_r, lon) if even else (lat_r, lat)
        mid = (rng[0] + rng[1]) / 2
        if val >= mid:
            bits = (bits << 1) | 1
            rng[0] = mid
        else:
            bits <<= 1
            rng[1] = mid
        even = not even
        bit += 1
        if bit == 5:
            out.append(_B32[bits])
            bits, bit = 0, 0
    return "".join(out)

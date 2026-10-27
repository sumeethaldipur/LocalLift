from datetime import datetime, timezone

from locallift import geo, timeutil


def test_haversine_known_distance():
    # JFK (40.6413, -73.7781) to LAX (33.9416, -118.4085) ≈ 2,475 statute miles
    d = geo.haversine_miles(40.6413, -73.7781, 33.9416, -118.4085)
    assert 2465 < d < 2485


def test_haversine_symmetry_and_zero():
    a = (40.7480, -73.9920)
    b = (40.7505, -73.9934)
    assert geo.haversine_miles(*a, *b) == geo.haversine_miles(*b, *a)
    assert geo.haversine_miles(*a, *a) == 0


def test_distance_missing_coordinates_is_none():
    assert geo.distance(40.7, -73.9, None, None) is None


def test_distance_labels_straight_line():
    d = geo.distance(40.7480, -73.9920, 40.7505, -73.9934)
    assert d["label"] == "straight-line distance" and d["km"] > d["miles"]


def test_geohash():
    assert geo.geohash(57.64911, 10.40744, 11) == "u4pruydqqvj"


def test_dst_local_conversion():
    # Before DST ends (Nov 1 2026): EDT = UTC-4; after: EST = UTC-5
    assert timeutil.local_naive_to_utc("2026-10-31T19:00:00").hour == 23
    assert timeutil.local_naive_to_utc("2026-11-02T19:00:00").hour == 0


def test_display_in_new_york_time():
    s = timeutil.fmt_ny(datetime(2026, 11, 2, 0, 30, tzinfo=timezone.utc))
    assert s.startswith("Sun Nov 1") and "7:30 PM" in s

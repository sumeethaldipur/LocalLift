"""Deterministic TEST fixtures. Refuses to run unless LOCALLIFT_ENV=test (separate database file),
so fixtures can never leak into the live dashboard.

    LOCALLIFT_ENV=test .venv/bin/python -m locallift.fixtures
"""
from __future__ import annotations

import json
from datetime import timedelta
from pathlib import Path

from . import business, config, db, ingest, ranking
from .timeutil import iso, now_utc

FIXTURE_DIR = Path(__file__).resolve().parent.parent / "tests" / "fixtures"


def _guard() -> None:
    if config.settings.env != "test":
        raise RuntimeError("Fixtures load only in test mode (LOCALLIFT_ENV=test).")


def events(now=None) -> list[dict]:
    now = now or now_utc()
    raw = json.loads((FIXTURE_DIR / "events.json").read_text())
    out = []
    for e in raw:
        e = dict(e)
        if "start_in_hours" in e:
            e["start_utc"] = iso(now + timedelta(hours=e.pop("start_in_hours")))
        if "end_in_hours" in e:
            e["end_utc"] = iso(now + timedelta(hours=e.pop("end_in_hours")))
        e.setdefault("end_utc", None)
        e.setdefault("performers", [])
        e.setdefault("raw", {})
        out.append(e)
    return out


def news(now=None) -> list[dict]:
    now = now or now_utc()
    raw = json.loads((FIXTURE_DIR / "news.json").read_text())
    for n in raw:
        n["published_at"] = iso(now + timedelta(hours=n.pop("published_in_hours")))
    return raw


def load() -> dict:
    _guard()
    business.get_profile()
    business.ensure_history()
    stats = ingest.upsert_events(events(), provenance="test_fixture")
    ingest.store_news(news(), provenance="test_fixture")
    ingest.link_feed_news()
    ranking.rank_all()
    return stats


if __name__ == "__main__":
    print(load())

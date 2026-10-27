"""Timezone-safe helpers. Everything is stored as UTC ISO-8601; displayed in New York time."""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

NY = ZoneInfo("America/New_York")
UTC = timezone.utc

# Tests can freeze "now" by setting this.
_frozen_now: datetime | None = None


def freeze(dt: datetime | None) -> None:
    global _frozen_now
    _frozen_now = dt.astimezone(UTC) if dt else None


def now_utc() -> datetime:
    return _frozen_now or datetime.now(UTC)


def now_ny() -> datetime:
    return now_utc().astimezone(NY)


def iso(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        raise ValueError("naive datetime")
    return dt.astimezone(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def parse(s: str | None) -> datetime | None:
    if not s:
        return None
    s = s.strip()
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    dt = datetime.fromisoformat(s)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt


def local_naive_to_utc(s: str) -> datetime:
    """Interpret a naive local NY timestamp (e.g. NYC Open Data) correctly across DST."""
    dt = datetime.fromisoformat(s.replace("Z", ""))
    return dt.replace(tzinfo=NY).astimezone(UTC)


def to_ny(dt: datetime) -> datetime:
    return dt.astimezone(NY)


def fmt_ny(dt: datetime | str | None, with_date: bool = True) -> str:
    if dt is None:
        return "Unknown"
    if isinstance(dt, str):
        dt = parse(dt)
    d = dt.astimezone(NY)
    t = d.strftime("%-I:%M %p").replace(":00 ", " ")
    return f"{d.strftime('%a %b %-d')}, {t}" if with_date else t


def ny_day_bounds(d: date) -> tuple[datetime, datetime]:
    start = datetime(d.year, d.month, d.day, tzinfo=NY)
    return start.astimezone(UTC), (start + timedelta(days=1)).astimezone(UTC)


def iso_week_key(dt: datetime) -> str:
    y, w, _ = dt.astimezone(NY).isocalendar()
    return f"{y}-W{w:02d}"


def is_weekend(d: date) -> bool:
    return d.weekday() >= 5

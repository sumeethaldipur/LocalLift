"""Hosted demo data. On Vercel each server starts from the snapshot the daily GitHub Action publishes as a release
asset, and swaps in a newer one every few hours, so the live site never runs on stale data. If the download fails,
it keeps what it has (at first, the snapshot bundled with the deployment). Local runs are unaffected."""
from __future__ import annotations

import os
import threading
import time
from pathlib import Path

import httpx

from . import config

URL = os.getenv("LOCALLIFT_SNAPSHOT_URL",
                "https://github.com/sumeethaldipur/LocalLift/releases/download/demo-data/locallift.db")
REFRESH_SECONDS = 3 * 3600
_state = {"loaded_at": 0.0, "busy": False}
_lock = threading.Lock()


def _download() -> bool:
    dest = Path(f"/tmp/locallift-{time.time_ns()}.db")  # unique: never overwrite the file in use
    part = dest.with_suffix(".part")
    try:
        with httpx.stream("GET", URL, follow_redirects=True, timeout=20) as r:
            r.raise_for_status()
            with open(part, "wb") as f:
                for chunk in r.iter_bytes():
                    f.write(chunk)
        with open(part, "rb") as f:
            if f.read(16) != b"SQLite format 3\x00":
                raise ValueError("not a SQLite file")
        part.rename(dest)
    except Exception:
        part.unlink(missing_ok=True)
        return False
    old = config.settings.db_path
    config.settings.live_db = str(dest)  # new connections open the new file; db.conn() keys connections by path
    if old != dest and old.parent == dest.parent and old.name.startswith("locallift-"):
        for f in (old, Path(f"{old}-wal"), Path(f"{old}-shm")):  # open connections keep reading until they close
            f.unlink(missing_ok=True)
    return True


def _refresh() -> None:
    try:
        _download()
    finally:
        _state["loaded_at"] = time.time()  # also after a failure, so a GitHub outage isn't retried on every request
        _state["busy"] = False


def ensure() -> None:
    """Called per request on Vercel: blocking on the first request of a server, in the background afterwards."""
    if not os.getenv("VERCEL") or os.getenv("LOCALLIFT_SNAPSHOT_URL") == "off":
        return
    if _state["loaded_at"] and time.time() - _state["loaded_at"] < REFRESH_SECONDS:
        return
    with _lock:
        if _state["busy"] or (_state["loaded_at"] and time.time() - _state["loaded_at"] < REFRESH_SECONDS):
            return
        _state["busy"] = True
        first = not _state["loaded_at"]
    if first:
        _refresh()
    else:
        threading.Thread(target=_refresh, daemon=True).start()

"""LocalLift background worker. Keep it running (separately from the web app) so sources refresh
and changes are detected even when no browser is open.

    .venv/bin/python worker.py            # loop forever (checks every 30 s)
    .venv/bin/python worker.py --once     # run due jobs once and exit (for cron/launchd)
    .venv/bin/python worker.py --job discover_nycparks   # force one job now
"""
from __future__ import annotations

import argparse
import json
import signal
import time

from locallift import business, jobs

_stop = False


def _handle(sig, frame):
    global _stop
    _stop = True


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--job")
    ap.add_argument("--interval", type=int, default=30)
    a = ap.parse_args()
    business.get_profile()
    business.ensure_history()
    if a.job:
        print(json.dumps(jobs.execute(a.job, trigger="manual"), indent=2))
        return
    signal.signal(signal.SIGINT, _handle)
    signal.signal(signal.SIGTERM, _handle)
    while not _stop:
        for r in jobs.tick():
            print(time.strftime("%H:%M:%S"), r["job"], r["status"], r.get("error") or "", flush=True)
        if a.once:
            break
        for _ in range(a.interval):
            if _stop:
                break
            time.sleep(1)
            if _ % 25 == 0:
                jobs.heartbeat()


if __name__ == "__main__":
    main()

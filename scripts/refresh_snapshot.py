"""One-shot refresh of the hosted demo's data. Runs daily in GitHub Actions (.github/workflows/refresh-data.yml):
start from the latest published snapshot, run every worker job once, strip secrets from the logs, and leave a
compact database ready to publish. The live site downloads that file (locallift/snapshot.py).

    LOCALLIFT_DB=data/locallift.db python scripts/refresh_snapshot.py
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from locallift import business, config, db, jobs  # noqa: E402


def sanitize(path: Path) -> None:
    """Request URLs can carry API keys in their query strings; the published file must not."""
    c = sqlite3.connect(str(path))
    c.execute("UPDATE request_log SET url=substr(url,1,instr(url,'?')-1) WHERE instr(url,'?')>0")
    c.execute("UPDATE job_state SET lock_owner=NULL, lock_until=NULL")
    c.commit()
    c.execute("PRAGMA journal_mode=DELETE")
    c.execute("VACUUM")
    c.close()


def main() -> int:
    business.get_profile()
    business.ensure_history()
    db.ex("UPDATE job_state SET lock_owner=NULL, lock_until=NULL")
    failed = 0
    for name in jobs.SCHEDULE:
        r = jobs.execute(name, trigger="daily-refresh")
        print(f"{r['job']:24} {r['status']:8} {r.get('error') or ''}", flush=True)
        failed += r["status"] == "error"
    jobs.heartbeat()
    db.conn().close()
    sanitize(config.settings.db_path)
    print(f"snapshot ready: {config.settings.db_path} ({config.settings.db_path.stat().st_size / 1e6:.1f} MB)")
    return 1 if failed == len(jobs.SCHEDULE) else 0  # a few flaky sources are fine; all failing is not


if __name__ == "__main__":
    sys.exit(main())

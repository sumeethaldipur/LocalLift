import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Tests always run against an isolated test database; never against live data.
os.environ["LOCALLIFT_ENV"] = "test"
os.environ["MODEL_PROVIDER"] = "none"
os.environ["TICKETMASTER_API_KEY"] = ""
os.environ["X_BEARER_TOKEN"] = ""
os.environ["X_API_KEY"] = ""
os.environ["X_API_SECRET"] = ""
os.environ["X_INCLUDE_COUNTS"] = "false"
os.environ["ENABLE_PAID_SOURCES"] = "false"

from locallift import config, db, timeutil  # noqa: E402

# Wednesday 2026-10-28 14:00 UTC (10:00 New York, EDT)
FROZEN = datetime(2026, 10, 28, 14, 0, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def fresh_db(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALLIFT_TEST_DB", str(tmp_path / "test.db"))
    monkeypatch.setenv("LOCALLIFT_ENV", "test")
    config.reload_settings()
    db.reset_connections()
    timeutil.freeze(FROZEN)
    yield
    timeutil.freeze(None)
    db.reset_connections()


@pytest.fixture
def loaded():
    from locallift import fixtures
    return fixtures.load()

"""SQLite persistence.

Live and test data live in *separate database files* (see config.db_path), so a
live query can never join a test fixture. Every observation row also carries a
`provenance` value: real_source | synthetic_business_history | owner_assumption
| owner_entered | test_fixture | derived.
"""
from __future__ import annotations

import json
import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from . import config

SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);

CREATE TABLE IF NOT EXISTS business (
  id TEXT PRIMARY KEY, data TEXT NOT NULL, created_at TEXT, updated_at TEXT
);

CREATE TABLE IF NOT EXISTS business_history (
  business_id TEXT, date TEXT, hour INTEGER, day_type TEXT, orders INTEGER,
  sales REAL, provenance TEXT DEFAULT 'synthetic_business_history',
  PRIMARY KEY (business_id, date, hour)
);

CREATE TABLE IF NOT EXISTS source_item (
  id TEXT PRIMARY KEY, provider TEXT, kind TEXT, url TEXT, title TEXT,
  published_at TEXT, retrieved_at TEXT, content TEXT, access_status TEXT,
  license TEXT, provenance TEXT
);

CREATE TABLE IF NOT EXISTS event (
  id TEXT PRIMARY KEY, provider TEXT, provider_id TEXT, title TEXT,
  venue TEXT, address TEXT, lat REAL, lon REAL, coord_quality TEXT,
  start_utc TEXT, end_utc TEXT, start_local TEXT, all_day INTEGER DEFAULT 0,
  status TEXT, url TEXT, category TEXT, performers TEXT,
  crowd TEXT, source_ids TEXT, first_seen TEXT, last_checked TEXT,
  last_success TEXT, version INTEGER DEFAULT 1, fingerprint TEXT, raw TEXT,
  followed INTEGER DEFAULT 0, dismissed INTEGER DEFAULT 0, provenance TEXT
);

CREATE TABLE IF NOT EXISTS event_history (
  id INTEGER PRIMARY KEY AUTOINCREMENT, event_id TEXT, version INTEGER,
  changed_at TEXT, change_kind TEXT, before TEXT, after TEXT
);

CREATE TABLE IF NOT EXISTS news (
  id TEXT PRIMARY KEY, provider TEXT, url TEXT UNIQUE, title TEXT,
  domain TEXT, published_at TEXT, retrieved_at TEXT, summary TEXT,
  dedupe_key TEXT, local_relevance TEXT, provenance TEXT
);

CREATE TABLE IF NOT EXISTS event_news (
  event_id TEXT, news_id TEXT, relevance REAL, method TEXT, retrospective INTEGER DEFAULT 0,
  PRIMARY KEY (event_id, news_id)
);

CREATE TABLE IF NOT EXISTS social_observation (
  id TEXT PRIMARY KEY, event_id TEXT, provider TEXT, query TEXT,
  window_start TEXT, window_end TEXT, sample_size INTEGER, unique_posts INTEGER,
  unique_authors INTEGER, post_ids TEXT, truncated INTEGER, pages INTEGER,
  comparable_key TEXT, needs TEXT, excerpts TEXT, metrics TEXT,
  collected_at TEXT, status TEXT, detail TEXT, provenance TEXT
);

CREATE TABLE IF NOT EXISTS claim (
  id TEXT PRIMARY KEY, event_id TEXT, statement TEXT, classification TEXT,
  evidence_ids TEXT, conflicts TEXT, numeric_value REAL, numeric_kind TEXT,
  last_checked TEXT, review_state TEXT DEFAULT 'pending', superseded_by TEXT,
  created_at TEXT, created_by TEXT
);

CREATE TABLE IF NOT EXISTS opportunity (
  event_id TEXT PRIMARY KEY, business_id TEXT, factors TEXT, score REAL,
  coverage REAL, provisional INTEGER, eligible INTEGER, rank INTEGER,
  rationale TEXT, uncertainty TEXT, crowd TEXT, computed_at TEXT
);

CREATE TABLE IF NOT EXISTS plan (
  id TEXT PRIMARY KEY, event_id TEXT, business_id TEXT, version INTEGER,
  state TEXT, data TEXT, owner_edits TEXT, pending_revision TEXT,
  created_at TEXT, updated_at TEXT, created_by TEXT
);

CREATE TABLE IF NOT EXISTS plan_version (
  id INTEGER PRIMARY KEY AUTOINCREMENT, plan_id TEXT, version INTEGER,
  data TEXT, owner_edits TEXT, archived_at TEXT, reason TEXT, evidence_ids TEXT
);

CREATE TABLE IF NOT EXISTS task (
  plan_id TEXT, id TEXT, phase TEXT, title TEXT, due_local TEXT,
  owner TEXT, done INTEGER DEFAULT 0, PRIMARY KEY (plan_id, id)
);

CREATE TABLE IF NOT EXISTS projection (
  id TEXT PRIMARY KEY, event_id TEXT, plan_id TEXT, method TEXT,
  inputs TEXT, result TEXT, created_at TEXT
);

CREATE TABLE IF NOT EXISTS watch (
  id TEXT PRIMARY KEY, event_id TEXT, conditions TEXT, last_version INTEGER,
  created_at TEXT, active INTEGER DEFAULT 1, created_by TEXT
);

CREATE TABLE IF NOT EXISTS notification (
  id INTEGER PRIMARY KEY AUTOINCREMENT, change_key TEXT UNIQUE, event_id TEXT,
  kind TEXT, message TEXT, impact TEXT, created_at TEXT, read INTEGER DEFAULT 0,
  development_id TEXT, affected_plans TEXT, before_after TEXT, source_ids TEXT
);

CREATE TABLE IF NOT EXISTS outcome (
  id TEXT PRIMARY KEY, plan_id TEXT, event_id TEXT, redemptions INTEGER,
  sales REAL, actual_costs REAL, notes TEXT, attribution TEXT, created_at TEXT,
  provenance TEXT DEFAULT 'owner_entered'
);

CREATE TABLE IF NOT EXISTS investigation (
  id TEXT PRIMARY KEY, event_id TEXT, question TEXT, trigger TEXT, status TEXT,
  provider TEXT, model TEXT, started_at TEXT, finished_at TEXT, tool_log TEXT,
  answer TEXT, usage TEXT, errors TEXT, retrievals INTEGER
);

CREATE TABLE IF NOT EXISTS job_state (
  job TEXT PRIMARY KEY, last_attempt TEXT, last_success TEXT, next_run TEXT,
  lock_owner TEXT, lock_until TEXT, last_error TEXT, last_detail TEXT
);

CREATE TABLE IF NOT EXISTS job_run (
  id INTEGER PRIMARY KEY AUTOINCREMENT, job TEXT, started_at TEXT,
  finished_at TEXT, status TEXT, detail TEXT, trigger TEXT
);

CREATE TABLE IF NOT EXISTS request_log (
  id INTEGER PRIMARY KEY AUTOINCREMENT, provider TEXT, url TEXT, started_at TEXT,
  status INTEGER, ok INTEGER, ms INTEGER, error TEXT, cost_usd REAL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS source_status (
  provider TEXT PRIMARY KEY, category TEXT, status TEXT, detail TEXT,
  credential_status TEXT, last_attempt TEXT, last_success TEXT, next_step TEXT
);

CREATE TABLE IF NOT EXISTS model_usage (
  id INTEGER PRIMARY KEY AUTOINCREMENT, day TEXT, provider TEXT, model TEXT,
  input_tokens INTEGER, output_tokens INTEGER, cost_usd REAL, at TEXT
);

-- A Development is one evolving story or material change with its supporting evidence.
CREATE TABLE IF NOT EXISTS development (
  id TEXT PRIMARY KEY, headline TEXT, dev_type TEXT, summary TEXT,
  source_types TEXT, location_text TEXT, location_precision TEXT, lat REAL, lon REAL,
  published_at TEXT, effective_start TEXT, effective_basis TEXT,
  first_seen TEXT, last_seen TEXT, last_checked TEXT, version INTEGER DEFAULT 1,
  verification_state TEXT DEFAULT 'unverified', status TEXT DEFAULT 'open',
  followed INTEGER DEFAULT 0, cluster_key TEXT, provenance TEXT
);
CREATE TABLE IF NOT EXISTS development_source (
  development_id TEXT, source_kind TEXT, source_id TEXT, source_type TEXT, added_at TEXT,
  PRIMARY KEY (development_id, source_kind, source_id)
);
CREATE TABLE IF NOT EXISTS development_event (
  development_id TEXT, event_id TEXT, method TEXT, PRIMARY KEY (development_id, event_id)
);
CREATE TABLE IF NOT EXISTS development_version (
  id INTEGER PRIMARY KEY AUTOINCREMENT, development_id TEXT, version INTEGER, at TEXT,
  what_changed TEXT, source_ids TEXT
);
-- source change → affected business assumption → recommendation change (before/after)
CREATE TABLE IF NOT EXISTS decision (
  id TEXT PRIMARY KEY, development_id TEXT, event_id TEXT, plan_id TEXT, at TEXT,
  source_change TEXT, affected_assumption TEXT, before TEXT, after TEXT, decision TEXT,
  verification TEXT, evidence_ids TEXT, decided_by TEXT
);
CREATE TABLE IF NOT EXISTS briefing_item (
  id TEXT PRIMARY KEY, business_id TEXT, development_id TEXT UNIQUE, summary TEXT,
  implication TEXT, implication_basis TEXT, action TEXT, action_rationale TEXT,
  certainty TEXT, urgency REAL, priority_factors TEXT, review_state TEXT,
  seen INTEGER DEFAULT 0, generated_at TEXT, updated_at TEXT, agent_draft TEXT
);

CREATE INDEX IF NOT EXISTS idx_event_start ON event(start_utc);
CREATE INDEX IF NOT EXISTS idx_social_event ON social_observation(event_id, collected_at);
CREATE INDEX IF NOT EXISTS idx_req_provider ON request_log(provider, started_at);
"""

MIGRATIONS = {  # columns added after the first release, applied to existing databases
    "notification": ["development_id TEXT", "affected_plans TEXT", "before_after TEXT", "source_ids TEXT"],
}


def _migrate(c: sqlite3.Connection) -> None:
    for table, cols in MIGRATIONS.items():
        have = {r[1] for r in c.execute(f"PRAGMA table_info({table})")}
        for col in cols:
            if col.split()[0] not in have:
                c.execute(f"ALTER TABLE {table} ADD COLUMN {col}")


_local = threading.local()
_init_lock = threading.Lock()
_initialized: set[str] = set()


def _connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=30, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=30000")
    return conn


def conn() -> sqlite3.Connection:
    path = config.settings.db_path
    key = str(path)
    cache = getattr(_local, "conns", None)
    if cache is None:
        cache = _local.conns = {}
    c = cache.get(key)
    if c is None:
        c = _connect(path)
        cache[key] = c
    if key not in _initialized:
        with _init_lock:
            if key not in _initialized:
                c.executescript(SCHEMA)
                _migrate(c)
                c.commit()
                _initialized.add(key)
    return c


def reset_connections() -> None:
    cache = getattr(_local, "conns", {})
    for c in cache.values():
        c.close()
    _local.conns = {}
    _initialized.clear()


@contextmanager
def tx() -> Iterator[sqlite3.Connection]:
    c = conn()
    try:
        yield c
        c.commit()
    except Exception:
        c.rollback()
        raise


def q(sql: str, params: tuple | dict = ()) -> list[dict[str, Any]]:
    return [dict(r) for r in conn().execute(sql, params).fetchall()]


def q1(sql: str, params: tuple | dict = ()) -> dict[str, Any] | None:
    r = conn().execute(sql, params).fetchone()
    return dict(r) if r else None


def ex(sql: str, params: tuple | dict = ()) -> sqlite3.Cursor:
    c = conn()
    cur = c.execute(sql, params)
    c.commit()
    return cur


def j(v: Any) -> str:
    return json.dumps(v, default=str, ensure_ascii=False)


def uj(v: str | None, default: Any = None) -> Any:
    if v is None or v == "":
        return default
    try:
        return json.loads(v)
    except (TypeError, ValueError):
        return default


def get_meta(key: str) -> str | None:
    r = q1("SELECT value FROM meta WHERE key=?", (key,))
    return r["value"] if r else None


def set_meta(key: str, value: str) -> None:
    ex("INSERT INTO meta(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, value))

"""Environment-driven configuration. Secrets are read server-side only."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env", override=False)

# Vercel (hosted prototype): the deployment's filesystem is read-only and no worker runs, so each instance starts
# from the bundled demo snapshot copied to /tmp. Writes last for the life of that instance only.
if os.getenv("VERCEL") and not os.getenv("LOCALLIFT_DB"):
    import shutil
    _tmp = Path("/tmp/locallift.db")
    if not _tmp.exists() and (ROOT / "demo" / "locallift.db").exists():
        shutil.copy(ROOT / "demo" / "locallift.db", _tmp)
    os.environ["LOCALLIFT_DB"] = str(_tmp)


def _bool(name: str, default: bool = False) -> bool:
    return os.getenv(name, str(default)).strip().lower() in {"1", "true", "yes", "on"}


def _float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, default))
    except ValueError:
        return default


def _int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, default))
    except ValueError:
        return default


@dataclass
class Settings:
    env: str = field(default_factory=lambda: os.getenv("LOCALLIFT_ENV", "live"))
    live_db: str = field(default_factory=lambda: os.getenv("LOCALLIFT_DB", "data/locallift.db"))
    test_db: str = field(default_factory=lambda: os.getenv("LOCALLIFT_TEST_DB", "data/test.db"))
    contact: str = field(default_factory=lambda: os.getenv("LOCALLIFT_CONTACT", "LocalLift class project"))

    ticketmaster_key: str = field(default_factory=lambda: os.getenv("TICKETMASTER_API_KEY", "").strip())
    bsky_handle: str = field(default_factory=lambda: os.getenv("BSKY_HANDLE", ""))
    bsky_password: str = field(default_factory=lambda: os.getenv("BSKY_APP_PASSWORD", ""))
    x_bearer: str = field(default_factory=lambda: os.getenv("X_BEARER_TOKEN", "").strip())
    x_api_key: str = field(default_factory=lambda: os.getenv("X_API_KEY", "").strip())
    x_api_secret: str = field(default_factory=lambda: os.getenv("X_API_SECRET", "").strip())
    x_monthly_budget: float = field(default_factory=lambda: _float("X_MONTHLY_BUDGET_USD", 0))
    x_cost_per_read: float = field(default_factory=lambda: _float("X_COST_PER_POST_READ_USD", 0.005))
    x_max_posts: int = field(default_factory=lambda: _int("X_MAX_POSTS_PER_QUERY", 10))
    x_daily_events: int = field(default_factory=lambda: _int("X_DAILY_EVENTS", 2))
    x_min_hours_between: float = field(default_factory=lambda: _float("X_MIN_HOURS_BETWEEN", 6))
    x_include_counts: bool = field(default_factory=lambda: _bool("X_INCLUDE_COUNTS", False))
    x_counts_cost: float = field(default_factory=lambda: _float("X_COUNTS_COST_USD", 0.01))
    enable_paid: bool = field(default_factory=lambda: _bool("ENABLE_PAID_SOURCES", False))

    model_provider: str = field(default_factory=lambda: os.getenv("MODEL_PROVIDER", "none").strip().lower())
    anthropic_key: str = field(default_factory=lambda: os.getenv("ANTHROPIC_API_KEY", "").strip())
    anthropic_model: str = field(default_factory=lambda: os.getenv("ANTHROPIC_MODEL", "claude-opus-5-5"))
    anthropic_effort: str = field(default_factory=lambda: os.getenv("ANTHROPIC_EFFORT", "medium"))
    anthropic_base_url: str = field(default_factory=lambda: os.getenv("LOCALLIFT_ANTHROPIC_BASE_URL", "https://api.anthropic.com"))
    compat_base_url: str = field(default_factory=lambda: os.getenv("OPENAI_COMPAT_BASE_URL", ""))
    compat_key: str = field(default_factory=lambda: os.getenv("OPENAI_COMPAT_API_KEY", "").strip())
    compat_model: str = field(default_factory=lambda: os.getenv("OPENAI_COMPAT_MODEL", "").strip())
    groq_key: str = field(default_factory=lambda: os.getenv("GROQ_API_KEY", "").strip())
    groq_model: str = field(default_factory=lambda: os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile").strip())
    compat_fallbacks: list[str] = field(default_factory=lambda: [
        m.strip() for m in os.getenv("OPENAI_COMPAT_FALLBACK_MODELS", "").split(",") if m.strip()])
    max_daily_model_cost: float = field(default_factory=lambda: _float("MAX_DAILY_MODEL_COST", 2.0))
    input_cost_mtok: float = field(default_factory=lambda: _float("MODEL_INPUT_COST_PER_MTOK", 0))
    output_cost_mtok: float = field(default_factory=lambda: _float("MODEL_OUTPUT_COST_PER_MTOK", 0))
    agent_max_retrievals: int = field(default_factory=lambda: _int("AGENT_MAX_RETRIEVALS", 8))
    agent_max_seconds: int = field(default_factory=lambda: _int("AGENT_MAX_SECONDS", 60))
    agent_max_turns: int = field(default_factory=lambda: _int("AGENT_MAX_TURNS", 12))

    tile_url: str = field(default_factory=lambda: os.getenv("MAP_TILE_URL", "https://tile.openstreetmap.org/{z}/{x}/{y}.png"))
    tile_attribution: str = field(default_factory=lambda: os.getenv(
        "MAP_TILE_ATTRIBUTION",
        '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors'))
    approved_hosts: list[str] = field(default_factory=lambda: [
        h.strip().lower() for h in os.getenv(
            "APPROVED_FETCH_HOSTS",
            "www.ticketmaster.com,www.msg.com,www.nycgovparks.org,gothamist.com,www.nyc.gov,new.mta.info",
        ).split(",") if h.strip()])

    @property
    def db_path(self) -> Path:
        p = Path(self.test_db if self.env == "test" else self.live_db)
        return p if p.is_absolute() else ROOT / p

    @property
    def model_configured(self) -> bool:
        if self.model_provider == "anthropic":
            return bool(self.anthropic_key)
        if self.model_provider == "openai_compatible":
            return bool(self.compat_base_url and self.compat_model)
        return False

    @property
    def model_label(self) -> str:
        if self.model_provider == "anthropic":
            return f"anthropic:{self.anthropic_model}"
        if self.model_provider == "openai_compatible":
            return f"openai_compatible:{self.compat_model}"
        return "none"


settings = Settings()


def reload_settings() -> Settings:
    global settings
    settings = Settings()
    return settings

"""The X spend guard uses X's own billing-cycle read count, so the cap holds across servers."""
import pytest

from locallift import config
from locallift.sources import social
from locallift.sources.http import SourceError


@pytest.fixture
def x_on(monkeypatch):
    s = config.settings
    monkeypatch.setattr(s, "x_api_key", "k"); monkeypatch.setattr(s, "x_api_secret", "s")
    monkeypatch.setattr(s, "enable_paid", True); monkeypatch.setattr(s, "x_monthly_budget", 5.0)
    monkeypatch.setattr(social, "_x_usage", {})
    return s


def test_shared_read_count_blocks_search_at_cap(loaded, x_on, monkeypatch):
    monkeypatch.setattr(social, "x_cycle_reads", lambda: 1000)  # 1000 reads × $0.005 = $5 already spent
    with pytest.raises(SourceError, match="budget"):
        social.x_search("Harry Styles")


def test_hosted_fails_closed_when_usage_unreadable(loaded, x_on, monkeypatch):
    monkeypatch.setenv("VERCEL", "1")
    monkeypatch.setattr(social, "x_cycle_reads", lambda: None)
    assert social.x_spent() == float("inf")
    monkeypatch.delenv("VERCEL")
    assert social.x_spent() == social.x_month_spend()


def test_daily_call_ceiling(loaded, x_on, monkeypatch):
    from locallift import db
    monkeypatch.setattr(x_on, "x_daily_call_limit", 1)
    assert social.x_daily_calls_left()
    from locallift.timeutil import iso, now_utc
    db.ex("INSERT INTO request_log(provider,url,started_at,status,ok,ms,cost_usd) VALUES('x','https://api.x.com/2/tweets/search/recent',?,200,1,1,0)",
          (iso(now_utc()),))
    assert not social.x_daily_calls_left()

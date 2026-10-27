from locallift import db, fixtures, ingest, ranking, notify, plans
from locallift.sources import news as news_src, nycparks, social


def test_fixtures_refuse_live_mode(monkeypatch):
    from locallift import config
    monkeypatch.setenv("LOCALLIFT_ENV", "live")
    config.reload_settings()
    try:
        fixtures.load()
        assert False, "should refuse"
    except RuntimeError:
        pass


def test_duplicates_merged(loaded):
    assert loaded["duplicates"] == 1
    ev = db.q1("SELECT * FROM event WHERE id='fixture:arena-concert'")
    assert len(db.uj(ev["source_ids"])) == 2
    assert db.q1("SELECT 1 FROM event WHERE id='fixture:arena-concert-dup'") is None


def test_cancelled_and_past_excluded(loaded):
    opp = {o["event_id"]: o for o in db.q("SELECT * FROM opportunity")}
    assert opp["fixture:cancelled"]["eligible"] == 0
    assert opp["fixture:past"]["eligible"] == 0
    assert opp["fixture:arena-concert"]["eligible"] == 1


def test_missing_coordinates_unknown_not_guessed(loaded):
    o = db.q1("SELECT * FROM opportunity WHERE event_id='fixture:no-coords'")
    f = db.uj(o["factors"])
    assert f["factors"]["distance_access"] is None
    assert f["distance"] is None
    assert o["provisional"] == 1 and o["coverage"] < 1


def test_unknown_evidence_gets_no_free_credit(loaded):
    o = db.q1("SELECT * FROM opportunity WHERE event_id='fixture:morning-run'")
    assert o["score"] <= o["coverage"] * 100 + 1e-6
    crowd = db.uj(o["crowd"])
    assert crowd["basis"] == "Typical for this kind of event" and crowd["estimated"]
    assert crowd["low"] < crowd["expected"] < crowd["high"]  # always a ballpark number with a range, never a bare label


def test_crowd_bands():
    assert ranking.band_for(499) == "Low"
    assert ranking.band_for(500) == "Medium"
    assert ranking.band_for(4999) == "Medium"
    assert ranking.band_for(5000) == "High"


def test_news_syndication_and_retrospective(loaded):
    rows = db.q("""SELECT n.dedupe_key, en.retrospective FROM event_news en JOIN news n ON n.id=en.news_id
                   WHERE en.event_id='fixture:arena-concert'""")
    keys = [r["dedupe_key"] for r in rows if not r["retrospective"]]
    assert len(keys) == 2 and len(set(keys)) == 1  # syndicated copy counts once
    assert ranking.news_count("fixture:arena-concert") == 1
    assert any(r["retrospective"] for r in rows)  # review published after the event is historical context


def test_headline_dedupe_key():
    assert news_src.dedupe_key("The Band Adds A Show!") == news_src.dedupe_key("band adds a show")


def _cancel():
    ev = [e for e in fixtures.events() if e["provider_id"] == "arena-concert"][0]
    ev["status"] = "cancelled"
    return ev


def test_cancellation_alerts_once_and_preserves_owner_edits(loaded):
    p = plans.generate("fixture:arena-concert")
    plans.edit(p["id"], "copy.sign_copy", "OWNER SIGN")
    plans.edit(p["id"], "go_no_go", ["Owner's own checklist"])
    db.ex("UPDATE event SET followed=1 WHERE id='fixture:arena-concert'")
    original_start = db.q1("SELECT start_utc FROM event WHERE id='fixture:arena-concert'")["start_utc"]
    ev = _cancel()
    ev["start_utc"] = original_start
    s1 = ingest.upsert_events([ev])
    s2 = ingest.upsert_events([ev])  # same change observed again
    assert s1["changed"] == 1 and s2["changed"] == 0
    alerts = db.q("SELECT * FROM notification WHERE event_id='fixture:arena-concert' AND kind='cancellation'")
    assert len(alerts) == 1
    assert notify.add(alerts[0]["change_key"], "cancellation", "dup") is False
    ranking.rank_all()
    assert db.q1("SELECT eligible FROM opportunity WHERE event_id='fixture:arena-concert'")["eligible"] == 0
    g = plans.get(p["id"])
    assert g["pending_revision"]["kind"] == "cancellation"
    assert g["data"]["copy"]["sign_copy"] == "OWNER SIGN"
    g = plans.resolve_revision(p["id"], accept=True)
    assert g["data"]["copy"]["sign_copy"] == "OWNER SIGN"
    assert g["data"]["go_no_go"] == ["Owner's own checklist"]  # owner edit wins over the revision
    assert "go_no_go" in g["data"]["revision_note"]["owner_edits_kept"]
    assert g["version"] == 2 and len(g["versions"]) == 1
    assert db.q1("SELECT 1 FROM job_run WHERE job='reassess_request' AND status='queued'")


def test_reschedule_detected(loaded):
    ev = [e for e in fixtures.events() if e["provider_id"] == "morning-run"][0]
    ev["start_utc"] = "2026-11-10T13:00:00Z"
    st = ingest.upsert_events([ev])
    assert st["changes"][0]["kind"] == "rescheduling"
    assert db.q1("SELECT version FROM event WHERE id='fixture:morning-run'")["version"] == 2


def test_parks_coordinate_parsing_and_cross_midnight():
    assert nycparks._coords("40.7532997, -73.9840011; 40.1, -73.1") == (40.7532997, -73.9840011)
    assert nycparks._coords(None) == (None, None)


def test_social_trend_requires_comparable_windows():
    w = {"current": {"unique_posts": 30, "truncated": True}, "prior": {"unique_posts": 5, "truncated": False}}
    assert social.trend(w)["label"] == "Trend unavailable"
    w["current"]["truncated"] = False
    assert social.trend(w)["label"] == "Rising"
    assert social.trend({"current": w["current"]})["label"] == "Trend unavailable"
    assert social.trend({"current": {"unique_posts": 1, "truncated": False},
                         "prior": {"unique_posts": 1, "truncated": False}})["label"] == "Trend unavailable"


def test_social_dedupe_and_needs():
    posts = [{"id": "a", "text": "Grabbing food before the show at MSG!", "author": "u1", "handle": "h1"},
             {"id": "a", "text": "Grabbing food before the show at MSG!", "author": "u1", "handle": "h1"},
             {"id": "b", "text": "grabbing food before the show at msg", "author": "u2", "handle": "h2"},
             {"id": "c", "text": "Where to meet up near Penn Station? Need coffee", "author": "u3", "handle": "h3"}]
    s = social._summarize(posts, "bluesky")
    assert s["unique_posts"] == 2 and s["unique_authors"] == 2
    assert "quick food before the event" in s["needs"] and "meeting place" in s["needs"]
    assert s["geo_specific_posts"] == 2


def test_x_not_connected_without_credentials():
    st = social.x_status()
    assert st["status"] == "setup_required" and st["next_step"]


def test_capacity_estimate_drives_attendance_projection(loaded):
    import json
    db.ex("""INSERT INTO source_item(id,provider,kind,url,title,retrieved_at,content,access_status,provenance)
             VALUES('wikidata:QX:concert','wikidata','reference','https://example.test','Arena','2026-10-28T00:00:00Z',?,'ok','test_fixture')""",
          (json.dumps({"text": "Fixture Arena capacity: 20000"}),))
    db.ex("""INSERT INTO claim(id,event_id,statement,classification,evidence_ids,numeric_value,numeric_kind,created_at,created_by)
             VALUES('c1','fixture:arena-concert','Fixture Arena holds 20,000 per Wikidata','sourced_fact','["wikidata:QX:concert"]',20000,'venue_capacity','2026-10-28T00:00:00Z','t')""")
    crowd = ranking.crowd_for("fixture:arena-concert")
    assert crowd["basis"] == "Venue scale proxy" and crowd["estimated"]
    assert (crowd["low"], crowd["expected"], crowd["high"]) == (12000, 16000, 19000)  # 60/80/95% sell-through
    p = plans.generate("fixture:arena-concert")
    assert p["data"]["projection_method"] == "attendance"
    base = p["data"]["projection_summary"]["base"]
    assert base["achievable_incremental_orders"] > 20          # a 16k crowd next door is not "+10% of a normal hour"
    assert base["unmet_potential_demand"] > 0                  # capacity (48 orders/h) caps what the café can serve
    assert base["incremental_contribution"] > 0


def test_x_sampled_at_most_once_per_gap(loaded, monkeypatch):
    from locallift import jobs, services
    calls = []
    monkeypatch.setattr(social, "x_status", lambda: {"status": "connected"})

    def fake(q, include_counts=True):
        calls.append(include_counts)
        return {"provider": "x", "query": q, "counts": None, "search": {
            "start": "2026-10-27T14:00:00Z", "end": "2026-10-28T14:00:00Z", "sample_size": 3, "unique_posts": 3, "unique_authors": 3,
            "post_ids": ["1", "2", "3"], "truncated": False, "pages": 1, "excerpts": [], "needs": {}, "geo_specific_posts": 3,
            "metrics": {"likes": 0, "reposts": 0, "provider": "x"}}}
    monkeypatch.setattr(social, "x_search", fake)
    first = services.social_for_event("fixture:arena-concert", ("x",))["x"]
    second = services.social_for_event("fixture:arena-concert", ("x",))["x"]
    assert first["ok"] and not first.get("skipped")
    assert second.get("skipped")                      # repeat click within 6 h: no paid call
    assert calls == [False]                           # one search, counts endpoint not requested by default
    assert "x_daily" in jobs.SCHEDULE and jobs.SCHEDULE["x_daily"][0] == 1440


def test_every_event_gets_a_ballpark(loaded):
    for ev in db.q("SELECT id FROM event"):
        c = ranking.crowd_for(ev["id"])
        assert c["expected"] and c["low"] <= c["expected"] <= c["high"], (ev["id"], c)


def test_news_must_be_about_the_event(loaded):
    ev = db.q1("SELECT * FROM event WHERE id='fixture:arena-concert'")
    assert ingest.news_relevance(ev, "Fixture Band adds second night at Fixture Arena") == 1.0
    assert ingest.news_relevance(ev, "Fixture Band tour adds New York date") == 0.8
    assert ingest.news_relevance(ev, "$100 sneakers spotted on Fixture Band singer's friend") == 0.0   # gossip, no event context
    assert ingest.news_relevance(ev, "Every Marvel post-credits scene that went nowhere") == 0.0      # not about the event at all


def test_msg_alias_and_short_team_name():
    concert = {'title': 'Harry Styles: Together, Together', 'performers': '["Harry Styles"]', 'venue': 'Madison Square Garden'}
    game = {'title': 'New York Knicks vs. Detroit Pistons', 'performers': '["New York Knicks", "Detroit Pistons"]', 'venue': 'Madison Square Garden'}
    assert ingest.news_relevance(concert, 'Harry Styles announces MSG residency') == 1
    assert ingest.news_relevance(concert, 'Harry Styles spotted shopping in New York') == 0
    assert ingest.news_relevance(concert, 'Harry Styles concert in London announced') == 0
    assert ingest.news_relevance(game, 'Knicks return to MSG for season opener') == 1


def test_ticket_variants_are_not_separate_opportunities():
    assert not ranking.eligibility({'title':'NY Rangers vs Flyers Premium Seating'})[0]
    assert not ranking.eligibility({'title':'Artist VIP Add-on - Not A Concert Ticket'})[0]
    assert ranking.eligibility({'title':'New York Rangers vs. Philadelphia Flyers'})[0]


def test_truncated_samples_compare_posting_rates():
    w = {"current": {"unique_posts": 100, "truncated": True, "rate_per_hour": 40.0},
         "prior": {"unique_posts": 100, "truncated": True, "rate_per_hour": 10.0}}
    t = social.trend(w)
    assert t["label"] == "Rising" and t["change_pct"] == 300 and "rate" in t["method"]
    w["current"]["rate_per_hour"] = 11.0
    assert social.trend(w)["label"] == "Steady"

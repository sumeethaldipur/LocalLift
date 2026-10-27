from locallift import business, db, themes
from locallift.agent import providers


def _ev():
    return {"id": "fixture:arena-concert", "title": "Fixture Band Live", "performers": '["Fixture Band"]',
            "category": "Music / Pop", "venue": "Fixture Arena", "start_utc": "2026-11-01T00:00:00Z"}


def test_default_themed_offer_is_drink_and_treat(loaded):
    o = themes.default_offer(_ev(), business.get_profile())
    assert o["items"] == ["ov_cold_brew", "cookie"]
    assert o["price"] < o["regular_price"] and o["contribution_per_order"] > 0
    assert o["theme"]["unofficial"]


def test_ai_offer_validated_against_menu(loaded, monkeypatch):
    p = business.get_profile()
    monkeypatch.setattr(providers, "complete_json", lambda prompt, **k: ({"name": "Golden Hour Cold Brew", "drink": "ov_cold_brew",
                        "snack": "cookie", "addon": {"name": "honey drizzle", "cost": 2.0}, "story": "A nod to a fan-favourite song.",
                        "tagline": "Golden before the show"}, "test-model"))
    o = themes.ai_offer(_ev(), p)
    assert o["name"] == "Golden Hour Cold Brew"
    assert o["theme"]["addon"]["cost"] == themes.MAX_ADDON_COST          # add-on cost capped
    assert o["unit_cost"] == round(1.70 + 1.05 + themes.MAX_ADDON_COST, 2)  # costs from the real menu
    # invented menu item → rejected
    monkeypatch.setattr(providers, "complete_json", lambda prompt, **k: ({"name": "Lobster Roll", "drink": "lobster"}, "m"))
    assert themes.ai_offer(_ev(), p) is None
    # artist name in the product name (implied endorsement) → rejected
    monkeypatch.setattr(providers, "complete_json", lambda prompt, **k: ({"name": "Fixture Band Latte", "drink": "latte"}, "m"))
    assert themes.ai_offer(_ev(), p) is None

"""
Unit tests for the offline catalog builder's pure logic (no network, no model).
"""

import json

from tools import catalog_spec
from tools import build_catalog as bc
from tools import sources


# ------------------------------------------------------------------ spec
def test_spec_loads_and_is_well_formed():
    cols = catalog_spec.COLLECTIONS
    assert len(cols) >= 12
    ids = [c["id"] for c in cols]
    assert len(ids) == len(set(ids)), "collection ids must be unique"
    for c in cols:
        assert {"id", "title", "description", "sources", "license"} <= set(c)
        assert c["sources"], f"{c['id']} has no sources"
    assert catalog_spec.get_collection("cosmos")["sources"] == ["nasa"]


# ------------------------------------------------------------------ select
def _item(title, artist, url, thumb=None):
    return {"title": title, "agent_name": artist, "source_url": url,
            "thumbnail_url": thumb or url.replace("full", "thumb")}


def test_dedupe_drops_same_url_and_title_artist():
    items = [
        _item("Starry Night", "Van Gogh", "https://x/a/full.jpg"),
        _item("Starry Night", "Van Gogh", "https://x/a/full.jpg"),   # exact dup url
        _item("starry  night", "van gogh", "https://x/b/full.jpg"),  # same work, diff url
        _item("Sunflowers", "Van Gogh", "https://x/c/full.jpg"),
    ]
    out = bc.dedupe_and_select(items, target=10)
    titles = sorted(i["title"].lower().strip() for i in out)
    assert len(out) == 2 and "sunflowers" in titles


def test_dedupe_prefers_higher_res_url():
    items = [
        _item("Wave", "Hokusai", "https://x/a/400.jpg"),
        _item("Wave", "Hokusai", "https://x/a/full/max/0/default.jpg"),
    ]
    out = bc.dedupe_and_select(items, target=10)
    assert len(out) == 1 and "full/max" in out[0]["source_url"]


def test_dedupe_caps_to_target():
    items = [_item(f"Art {i}", "X", f"https://x/{i}/full.jpg") for i in range(20)]
    assert len(bc.dedupe_and_select(items, target=5)) == 5


def test_dedupe_requires_both_urls():
    items = [{"title": "No thumb", "agent_name": "X", "source_url": "https://x/a.jpg", "thumbnail_url": ""}]
    assert bc.dedupe_and_select(items, target=5) == []


# ------------------------------------------------------------------ placard
def test_template_placard_fills_text():
    it = sources._norm(title="The Kiss", agent_name="Gustav Klimt", date_display="1908",
                       medium="Oil and gold leaf", source="Belvedere")
    bc._template_placard(it)
    assert "The Kiss by Gustav Klimt (1908)." in it["description_narrative"]
    assert "Belvedere" in it["description_narrative"]
    assert it["tags"]  # non-empty


def test_enrich_falls_back_to_template_on_model_error(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("no model")
    monkeypatch.setattr(bc.ai_client, "chat", boom)
    it = sources._norm(title="Mona Lisa", agent_name="Leonardo da Vinci", source="Louvre")
    out = bc.enrich_item(it)
    assert out["description_narrative"].startswith("Mona Lisa by Leonardo da Vinci")


def test_enrich_uses_model_output(monkeypatch):
    monkeypatch.setattr(bc.ai_client, "chat", lambda *a, **k: json.dumps({
        "description_narrative": "Two factual sentences.", "tags": "a, b, c",
        "agent_role": "Painter", "cultural_context": "Renaissance", "date_display": "1503"}))
    it = sources._norm(title="Mona Lisa", agent_name="Leonardo da Vinci", source="Louvre")
    out = bc.enrich_item(it)
    assert out["description_narrative"] == "Two factual sentences."
    assert out["cultural_context"] == "Renaissance" and out["date_display"] == "1503"


# ------------------------------------------------------------------ scout mapping
def test_scout_result_maps_to_item():
    r = {"proposed_title": "Water Lilies", "proposed_artist": "Claude Monet",
         "source_url": "https://aic/iiif/x/full/max/0/default.jpg",
         "thumbnail_url": "https://aic/iiif/x/full/400,/0/default.jpg",
         "source_api": "Art Institute of Chicago",
         "context_hints": json.dumps({"date_display": "1906", "medium_display": "Oil on canvas"})}
    it = sources._from_scout_result(r)
    assert it["title"] == "Water Lilies" and it["agent_name"] == "Claude Monet"
    assert it["date_display"] == "1906" and it["medium"] == "Oil on canvas"
    assert it["license"] == "Public Domain"


def test_met_non_public_domain_dropped():
    r = {"proposed_title": "Restricted", "proposed_artist": "X",
         "source_url": "https://met/x.jpg", "thumbnail_url": "https://met/x_s.jpg",
         "source_api": "The Metropolitan Museum of Art",
         "context_hints": json.dumps({"isPublicDomain": False})}
    assert sources._from_scout_result(r) is None


def test_met_public_domain_kept():
    r = {"proposed_title": "Open", "proposed_artist": "X",
         "source_url": "https://met/x.jpg", "thumbnail_url": "https://met/x_s.jpg",
         "source_api": "The Metropolitan Museum of Art",
         "context_hints": json.dumps({"isPublicDomain": True})}
    assert sources._from_scout_result(r) is not None

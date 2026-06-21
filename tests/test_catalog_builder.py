"""
Unit tests for the offline catalog builder's pure logic (no network, no model).
"""

import asyncio
import io
import json

from PIL import Image

from tools import build_catalog as bc
from tools import catalog_spec, sources


def _png_bytes(size):
    buf = io.BytesIO()
    Image.new("RGB", size, (30, 60, 90)).save(buf, format="PNG")
    return buf.getvalue()


class _Resp:
    def __init__(self, status=200, ct="image/png", content=b""):
        self.status_code = status
        self.headers = {"content-type": ct}
        self.content = content


class _Client:
    """Minimal async httpx-like client returning a canned response."""
    def __init__(self, resp):
        self._resp = resp
    async def get(self, url, **kw):
        return self._resp


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


# ------------------------------------------------------------------ LoC junk filter
def test_loc_best_asset_requires_tile_storage_asset():
    # A real digitized item lives on tile.loc.gov/storage-services/service.
    full, thumb = sources._loc_best_asset([
        "https://tile.loc.gov/storage-services/service/pnp/cph/3g00000/x.jpg"])
    assert full and "tile.loc.gov/storage-services/service" in full
    # Static SVG icons / collection landing thumbnails are web pages, not the item → dropped.
    assert sources._loc_best_asset(["https://www.loc.gov/static/images/original-format/group-of-images.svg"]) == (None, None)


def test_loc_junk_titles_match():
    for t in ["Keep Mum | Articles and Essays | Posters", "Collection Highlights",
              "Interview with Tony Velonis", "Finding Images in the Prints Division"]:
        assert sources._LOC_JUNK_TITLE.search(t)
    assert not sources._LOC_JUNK_TITLE.search("Moulin Rouge: La Goulue")


# ------------------------------------------------------------------ Wikimedia PD gate
def test_wm_is_pd():
    assert sources._wm_is_pd({"LicenseShortName": {"value": "Public domain"}})
    assert sources._wm_is_pd({"LicenseShortName": {"value": "CC0"}})
    assert sources._wm_is_pd({"Copyrighted": {"value": "False"}})
    assert not sources._wm_is_pd({"LicenseShortName": {"value": "CC BY-SA 4.0"}})
    assert not sources._wm_is_pd({})


# ------------------------------------------------------------------ display-true gates
def test_thumb_gate_rejects_html_and_svg():
    assert asyncio.run(bc._thumb_is_real_image(_Client(_Resp(ct="text/html", content=b"<html>")), "u")) is False
    assert asyncio.run(bc._thumb_is_real_image(_Client(_Resp(ct="image/svg+xml", content=b"<svg/>")), "u")) is False


def test_thumb_gate_rejects_tiny_and_accepts_real():
    assert asyncio.run(bc._thumb_is_real_image(_Client(_Resp(content=_png_bytes((120, 120)))), "u")) is False
    assert asyncio.run(bc._thumb_is_real_image(_Client(_Resp(content=_png_bytes((500, 400)))), "u")) is True


def test_source_gate_enforces_4k_capable_resolution():
    # Non-Wikimedia source: must be ≥ MIN_DISPLAY_EDGE on the long edge.
    small = _Client(_Resp(content=_png_bytes((1000, 800))))
    big = _Client(_Resp(content=_png_bytes((3000, 2000))))
    assert asyncio.run(bc._source_ok(small, "https://artic.edu/x.jpg")) is False
    assert asyncio.run(bc._source_ok(big, "https://artic.edu/x.jpg")) is True


def test_source_gate_trusts_pregated_wikimedia():
    # Wikimedia FilePath is pre-gated on the original's size at resolve time → content-type only.
    cli = _Client(_Resp(status=206, ct="image/jpeg", content=b"\xff\xd8"))
    assert asyncio.run(bc._source_ok(cli, "https://commons.wikimedia.org/wiki/Special:FilePath/x.jpg?width=3840")) is True
    html = _Client(_Resp(status=200, ct="text/html", content=b"<html>"))
    assert asyncio.run(bc._source_ok(html, "https://commons.wikimedia.org/wiki/Special:FilePath/x.jpg?width=3840")) is False

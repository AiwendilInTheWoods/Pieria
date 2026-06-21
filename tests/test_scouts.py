"""
Unit tests for the live NASA + Wikimedia Scouts (no network).

These scouts gate in-path (the live discovery pipeline has no later gate), so the tests
assert the PD / resolution / mime filtering happens inside find_art().
"""

import asyncio
import json

import pytest

import scout


class _FakeResp:
    def __init__(self, status=200, data=None):
        self.status_code = status
        self._data = data if data is not None else {}

    def json(self):
        return self._data


class _RouterClient:
    """Async httpx-like client that routes GETs to canned responses by URL substring."""
    def __init__(self, routes):
        self.routes = routes  # list of (substring, _FakeResp)
        self.calls = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def get(self, url, **kw):
        self.calls.append((url, kw))
        for sub, resp in self.routes:
            if sub in url:
                return resp
        return _FakeResp(404, {})


def _patch_client(monkeypatch, router):
    monkeypatch.setattr(scout.httpx, "AsyncClient", lambda *a, **k: router)


@pytest.fixture(autouse=True)
def _no_throttle(monkeypatch):
    async def _noop():
        return None
    monkeypatch.setattr(scout, "_wm_throttle", _noop)


# ----------------------------------------------------------------- Wikimedia
def _wm_pages():
    return {"query": {"pages": {
        "1": {"title": "File:The Starry Night.jpg", "imageinfo": [{
            "mime": "image/jpeg", "width": 3000, "height": 2400,
            "extmetadata": {
                "LicenseShortName": {"value": "Public domain"},
                "Artist": {"value": "<a href='x'>Vincent van Gogh</a>"},
                "ObjectName": {"value": 'The Starry Nighttitle QS:P1476,en:"The Starry Night"'},
            }}]},
        "2": {"title": "File:Tiny.jpg", "imageinfo": [{  # too small -> dropped
            "mime": "image/jpeg", "width": 800, "height": 600,
            "extmetadata": {"LicenseShortName": {"value": "Public domain"}}}]},
        "3": {"title": "File:Modern.jpg", "imageinfo": [{  # not PD -> dropped
            "mime": "image/jpeg", "width": 4000, "height": 3000,
            "extmetadata": {"LicenseShortName": {"value": "CC BY-SA 4.0"},
                            "Copyrighted": {"value": "True"}}}]},
        "4": {"title": "File:Icon.svg", "imageinfo": [{  # svg -> dropped
            "mime": "image/svg+xml", "width": 4000, "height": 4000,
            "extmetadata": {"LicenseShortName": {"value": "Public domain"}}}]},
    }}}


def test_wikimedia_keeps_only_pd_large_raster(monkeypatch):
    router = _RouterClient([("commons.wikimedia.org", _FakeResp(200, _wm_pages()))])
    _patch_client(monkeypatch, router)
    out = asyncio.run(scout.WikimediaScout().find_art(query="Starry Night"))
    assert len(out) == 1, "only the PD, >=2000px, non-svg image should survive"
    item = out[0]
    assert item["proposed_title"] == "The Starry Night"
    assert item["proposed_artist"] == "Vincent van Gogh"  # html stripped
    assert item["source_api"] == "Wikimedia Commons"
    assert "Special:FilePath" in item["source_url"] and "width=3840" in item["source_url"]
    assert "width=600" in item["thumbnail_url"]
    json.loads(item["context_hints"])  # valid JSON


def test_wikimedia_passes_offset_and_limit(monkeypatch):
    router = _RouterClient([("commons.wikimedia.org", _FakeResp(200, _wm_pages()))])
    _patch_client(monkeypatch, router)
    asyncio.run(scout.WikimediaScout().find_art(query="x", offset=20, limit=7))
    _, kw = router.calls[0]
    params = kw["params"]
    assert params["gsroffset"] == "20"
    assert params["gsrlimit"] == "7"
    assert params["gsrnamespace"] == "6"


def test_wikimedia_empty_on_bad_status(monkeypatch):
    router = _RouterClient([("commons.wikimedia.org", _FakeResp(500, {}))])
    _patch_client(monkeypatch, router)
    assert asyncio.run(scout.WikimediaScout().find_art(query="x")) == []


# ----------------------------------------------------------------- NASA
def _nasa_search():
    return {"collection": {"items": [
        {"data": [{"title": "Pillars of Creation", "nasa_id": "PIA12345", "center": "STScI"}],
         "links": [{"href": "https://images-assets.nasa.gov/image/PIA12345/PIA12345~thumb.jpg"}]},
        {"data": [{"title": "No Links"}], "links": []},  # no thumb -> skipped
    ]}}


def test_nasa_resolves_full_res_asset(monkeypatch):
    manifest = ["https://images-assets.nasa.gov/image/PIA12345/PIA12345~thumb.jpg",
                "https://images-assets.nasa.gov/image/PIA12345/PIA12345~orig.jpg"]
    router = _RouterClient([
        ("images-api.nasa.gov/search", _FakeResp(200, _nasa_search())),
        ("collection.json", _FakeResp(200, manifest)),
    ])
    _patch_client(monkeypatch, router)
    out = asyncio.run(scout.NasaScout().find_art(query="nebula"))
    assert len(out) == 1, "the item with no link is skipped"
    item = out[0]
    assert item["source_url"].endswith("~orig.jpg")
    assert item["thumbnail_url"].endswith("~thumb.jpg")
    assert item["source_api"] == "NASA"
    assert item["proposed_title"] == "Pillars of Creation"


def test_nasa_falls_back_to_thumb_when_manifest_missing(monkeypatch):
    router = _RouterClient([
        ("images-api.nasa.gov/search", _FakeResp(200, _nasa_search())),
        ("collection.json", _FakeResp(404, {})),
    ])
    _patch_client(monkeypatch, router)
    out = asyncio.run(scout.NasaScout().find_art(query="nebula"))
    assert len(out) == 1
    assert out[0]["source_url"].endswith("~thumb.jpg")

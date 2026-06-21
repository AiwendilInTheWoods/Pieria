"""
Unit tests for the Samsung Frame TV push adapter.

Covers everything except actual TV acceptance (no hardware — see memory no-frame-tv-for-testing):
the full-colour render, the push orchestration (dedupe / delete-old / persist / errors) via a fake
client, and that SamsungFrameClient drives the samsungtvws API correctly via an injected fake TV.
"""

import asyncio
import io

import pytest
from PIL import Image

import frame_push as fp
from epaper import render_fullcolor


def _make_image(tmp_path, size=(1000, 800), color=(120, 40, 30)):
    p = tmp_path / "art.jpg"
    Image.new("RGB", size, color).save(p, format="JPEG")
    return p


def _cfg(**over):
    base = {
        "playlist": "Masterpieces", "width": 3840, "height": 2160, "matte": "none",
        "last_content_id": None, "last_artwork_id": None,
    }
    base.update(over)
    return base


# ------------------------------------------------------------------ render
def test_render_fullcolor_is_rgb_jpeg_exact_size(tmp_path):
    data = render_fullcolor(_make_image(tmp_path), 1920, 1080, "cover", 90)
    im = Image.open(io.BytesIO(data))
    assert im.size == (1920, 1080)
    assert im.format == "JPEG"
    assert im.mode == "RGB"  # NOT paletted/dithered like the e-ink path


# ------------------------------------------------------------------ push_once
def test_push_once_uploads_shows_and_sets_artmode(tmp_path):
    path = _make_image(tmp_path)
    captured = []
    client = fp.FakeFrameClient()

    async def select(_pl):
        return (path, 42)

    res = asyncio.run(fp.push_once(_cfg(), select, client, persist=lambda c, a: captured.append((c, a))))
    assert res["status"] == "pushed"
    assert res["artwork_id"] == 42
    # order: push -> show -> artmode
    kinds = [c[0] for c in client.calls]
    assert kinds == ["push", "show", "artmode"]
    assert client.uploaded == [res["content_id"]]
    assert client.artmode is True
    assert captured == [(res["content_id"], 42)]


def test_push_once_dedupes_unchanged_artwork(tmp_path):
    path = _make_image(tmp_path)
    client = fp.FakeFrameClient()

    async def select(_pl):
        return (path, 7)

    cfg = _cfg(last_artwork_id=7, last_content_id="MY-PREV")
    res = asyncio.run(fp.push_once(cfg, select, client, persist=lambda *a: None))
    assert res["status"] == "unchanged"
    assert client.calls == []  # nothing uploaded


def test_push_once_force_overrides_dedupe(tmp_path):
    path = _make_image(tmp_path)
    client = fp.FakeFrameClient()

    async def select(_pl):
        return (path, 7)

    cfg = _cfg(last_artwork_id=7, last_content_id="MY-PREV")
    res = asyncio.run(fp.push_once(cfg, select, client, force=True, persist=lambda *a: None))
    assert res["status"] == "pushed"
    # prior upload deleted on change of content
    assert client.deleted == ["MY-PREV"]


def test_push_once_deletes_prior_upload_on_change(tmp_path):
    path = _make_image(tmp_path)
    client = fp.FakeFrameClient()

    async def select(_pl):
        return (path, 99)

    cfg = _cfg(last_artwork_id=1, last_content_id="OLD-CID")
    res = asyncio.run(fp.push_once(cfg, select, client, persist=lambda *a: None))
    assert res["status"] == "pushed"
    assert client.deleted == ["OLD-CID"]


def test_push_once_skips_when_no_artwork(tmp_path):
    client = fp.FakeFrameClient()

    async def select(_pl):
        return None

    res = asyncio.run(fp.push_once(_cfg(), select, client, persist=lambda *a: None))
    assert res["status"] == "skipped"
    assert client.calls == []


def test_push_once_error_does_not_persist(tmp_path):
    path = _make_image(tmp_path)
    client = fp.FakeFrameClient(fail_on="push")
    captured = []

    async def select(_pl):
        return (path, 5)

    with pytest.raises(RuntimeError):
        asyncio.run(fp.push_once(_cfg(), select, client, persist=lambda c, a: captured.append((c, a))))
    assert captured == []  # state not corrupted on failure


# ------------------------------------------------------------------ run_test_push
def test_run_test_push_errors_without_host(monkeypatch):
    monkeypatch.setattr(fp, "get_frame_config", lambda force=False: _cfg(host=""))

    async def select(_pl):
        return None

    res = asyncio.run(fp.run_test_push(select))
    assert res["status"] == "error"
    assert "host" in res["reason"].lower()


def test_run_test_push_pushes_with_fake_client(tmp_path, monkeypatch):
    path = _make_image(tmp_path)
    monkeypatch.setattr(fp, "get_frame_config",
                        lambda force=False: _cfg(host="192.168.1.50"))
    monkeypatch.setattr(fp, "_persist_state", lambda c, a: None)  # don't touch the real DB

    async def select(_pl):
        return (path, 3)

    res = asyncio.run(fp.run_test_push(select, client_factory=lambda cfg: fp.FakeFrameClient()))
    assert res["status"] == "pushed"
    assert res["artwork_id"] == 3


# ------------------------------------------------------------------ SamsungFrameClient mapping
class _FakeArt:
    def __init__(self, rec):
        self.rec = rec

    def upload(self, data, file_type=None, matte=None):
        self.rec.append(("upload", file_type, matte, len(data)))
        return "CID-123"

    def select_image(self, content_id, show=None):
        self.rec.append(("select_image", content_id, show))

    def set_artmode(self, mode):
        self.rec.append(("set_artmode", mode))

    def delete(self, content_id):
        self.rec.append(("delete", content_id))

    def get_artmode(self):
        return "on"


class _FakeTV:
    def __init__(self, rec):
        self._art = _FakeArt(rec)

    def art(self):
        return self._art


def test_samsung_client_maps_to_library_api():
    rec = []
    client = fp.SamsungFrameClient("10.0.0.5", tv_factory=lambda: _FakeTV(rec))

    cid = asyncio.run(client.push(b"\xff\xd8imagebytes", "jpg", "none"))
    asyncio.run(client.show(cid))
    asyncio.run(client.ensure_artmode())
    asyncio.run(client.delete("OLD"))
    info = asyncio.run(client.test())

    assert cid == "CID-123"
    assert ("upload", "jpg", "none", len(b"\xff\xd8imagebytes")) in rec
    assert ("select_image", "CID-123", True) in rec
    assert ("set_artmode", True) in rec
    assert ("delete", "OLD") in rec
    assert info == {"artmode": "on"}

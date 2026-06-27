"""Tests for the Studio Personal-mode upload path (increment ③)."""

import io

import pytest
from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app as app_module
from app import PERSONAL_PLAYLIST_NAME, app
from database import Base, get_db
from models import ArtworkModel, PlaylistModel, playlist_artwork


@pytest.fixture
def client(monkeypatch, tmp_path):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(bind=engine)
    db = sessionmaker(bind=engine, autocommit=False, autoflush=False)()

    def _override_db():
        yield db
    app.dependency_overrides[get_db] = _override_db
    monkeypatch.setattr(app_module, "LIBRARY_DIR", tmp_path)

    with TestClient(app) as c:
        yield c, db
    app.dependency_overrides.clear()
    db.close()


def _png_bytes(size=(60, 40), color=(10, 120, 200)):
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, format="PNG")
    return buf.getvalue()


def _upload(c, **data):
    filename = data.pop("filename", "snap.png")
    size = data.pop("size", (60, 40))
    return c.post("/upload/personal",
                  files={"file": (filename, _png_bytes(size), "image/png")},
                  data=data)


def test_personal_upload_is_approved_personal_no_review(client):
    c, db = client
    r = _upload(c, caption="Beach Day", date="2026")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["is_personal"] is True
    assert body["status"] == "approved"          # not pending_review — bypasses the queue
    assert body["title"] == "Beach Day"
    assert body["date_display"] == "2026"
    assert body["focal_x"] == 0.5 and body["focal_y"] == 0.5
    art = db.get(ArtworkModel, body["id"])
    assert art.filename.startswith("personal_") and art.filename.endswith(".png")


def test_personal_upload_auto_creates_my_photos_playlist(client):
    c, db = client
    body = _upload(c, caption="Pet").json()
    pl = db.query(PlaylistModel).filter(PlaylistModel.name == PERSONAL_PLAYLIST_NAME).first()
    assert pl is not None
    links = db.execute(select(playlist_artwork).where(
        playlist_artwork.c.playlist_id == pl.id,
        playlist_artwork.c.artwork_id == body["id"])).all()
    assert len(links) == 1


def test_personal_upload_respects_given_playlist(client):
    c, db = client
    pl = PlaylistModel(name="Vacation"); db.add(pl); db.commit(); db.refresh(pl)
    body = _upload(c, caption="Sunset", playlist_id=pl.id).json()
    ids = [r[0] for r in db.execute(select(playlist_artwork.c.artwork_id).where(
        playlist_artwork.c.playlist_id == pl.id)).all()]
    assert body["id"] in ids
    # did NOT also create the default "My Photos" playlist
    assert db.query(PlaylistModel).filter(PlaylistModel.name == PERSONAL_PLAYLIST_NAME).first() is None


def test_personal_upload_collision_safe_filenames(client):
    c, db = client
    a = _upload(c, caption="Dog").json()
    b = _upload(c, caption="Dog").json()
    assert db.get(ArtworkModel, a["id"]).filename != db.get(ArtworkModel, b["id"]).filename


def test_personal_upload_rejects_non_image(client):
    c, _ = client
    r = c.post("/upload/personal", files={"file": ("notimg.txt", b"hello world", "text/plain")})
    assert r.status_code == 400


# --- AI auto-caption (increment ④) ---

@pytest.mark.parametrize("url,local", [
    ("http://localhost:11434/v1", True),
    ("http://127.0.0.1:1234/v1", True),
    ("http://host.docker.internal:11434/v1", True),
    ("http://192.168.1.50:11434/v1", True),
    ("http://10.0.0.5/v1", True),
    ("https://api.openai.com/v1", False),
    ("https://generativelanguage.googleapis.com/v1beta/openai", False),
    ("https://openrouter.ai/api/v1", False),
    ("", False),
])
def test_is_local_base_url(url, local):
    import ai_client
    assert ai_client.is_local_base_url(url) is local


def test_caption_suggests_and_reports_local(client, monkeypatch):
    c, _ = client
    body = _upload(c, caption="").json()   # creates a personal artwork + its file on disk
    monkeypatch.setattr(app_module.ai_client, "get_ai_config",
                        lambda force=False: {"configured": True, "base_url": "http://localhost:11434/v1"})
    monkeypatch.setattr(app_module.ai_client, "chat",
                        lambda *a, **k: '{"caption": "A Sunny Day at Bondi Beach"}')
    r = c.post(f"/api/studio/caption/{body['id']}", json={"hint": "Bondi"})
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["caption"] == "A Sunny Day at Bondi Beach"
    assert d["model_is_local"] is True   # localhost model ⇒ photo stays on-device


def test_caption_reports_cloud_model(client, monkeypatch):
    c, _ = client
    body = _upload(c).json()
    monkeypatch.setattr(app_module.ai_client, "get_ai_config",
                        lambda force=False: {"configured": True, "base_url": "https://api.openai.com/v1"})
    monkeypatch.setattr(app_module.ai_client, "chat", lambda *a, **k: '{"caption": "Golden Hour"}')
    d = c.post(f"/api/studio/caption/{body['id']}", json={}).json()
    assert d["caption"] == "Golden Hour" and d["model_is_local"] is False


def test_caption_requires_configured_model(client, monkeypatch):
    c, _ = client
    body = _upload(c).json()
    monkeypatch.setattr(app_module.ai_client, "get_ai_config",
                        lambda force=False: {"configured": False, "base_url": ""})
    assert c.post(f"/api/studio/caption/{body['id']}", json={}).status_code == 400


def test_caption_unknown_artwork_404(client):
    c, _ = client
    assert c.post("/api/studio/caption/99999", json={}).status_code == 404


# --- Jargon-free placard / detail page (increment ⑤) ---

def test_art_detail_page_personal_strips_jargon(client):
    c, _ = client
    body = _upload(c, caption="Beach Day", date="2024").json()
    page = c.get(f"/art/{body['id']}")
    assert page.status_code == 200
    html = page.text
    assert "Beach Day" in html            # the caption is the title
    assert "Unknown artist" not in html   # no museum artist line
    assert "View original source" not in html


def test_next_image_metadata_carries_is_personal(client):
    c, _ = client
    # _upload auto-creates + links a "My Photos" playlist, so /next-image has something to select.
    _upload(c, caption="Pet Nap")
    info = c.get("/next-image", params={"playlist_name": PERSONAL_PLAYLIST_NAME})
    assert info.status_code == 200, info.text
    assert info.json()["metadata"]["is_personal"] is True

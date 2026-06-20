"""
Catalog endpoint tests — browse the bundled manifest and lazily "add" an item to the library.

The remote image download is mocked (a real small PNG so PIL can read dimensions); library/
playlist writes are redirected to a tmp dir. No network is touched.
"""

import io

import pytest
from PIL import Image
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from fastapi.testclient import TestClient

import app as app_module
from app import app
from database import Base, get_db
from models import ArtworkModel, PlaylistModel, playlist_artwork


@pytest.fixture
def client(monkeypatch, tmp_path):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(bind=engine)
    TestingSessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    db = TestingSessionLocal()

    def _override_db():
        yield db
    app.dependency_overrides[get_db] = _override_db

    # Redirect all filesystem writes into the tmp dir.
    monkeypatch.setattr(app_module, "LIBRARY_DIR", tmp_path)
    monkeypatch.setattr(app_module, "ARTWORK_ROOT", tmp_path)

    # Mock the high-res download with a real (tiny) PNG so Image.open() works.
    buf = io.BytesIO()
    Image.new("RGB", (40, 30), (20, 40, 60)).save(buf, format="PNG")
    png_bytes = buf.getvalue()

    class _FakeResp:
        status_code = 200
        content = png_bytes

    class _FakeAsyncClient:
        def __init__(self, *a, **k): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def get(self, url, **k): return _FakeResp()

    monkeypatch.setattr(app_module.httpx, "AsyncClient", _FakeAsyncClient)

    with TestClient(app) as c:
        yield c, db
    app.dependency_overrides.clear()
    db.close()


def test_get_catalog_returns_collections(client):
    c, _ = client
    r = c.get("/api/catalog")
    assert r.status_code == 200
    data = r.json()
    assert data["collections"], "expected at least one collection"
    first_item = data["collections"][0]["items"][0]
    assert "source_url" in first_item and "thumbnail_url" in first_item
    assert first_item["added"] is False  # nothing added yet


def test_add_item_creates_approved_artwork(client):
    c, db = client
    cat = c.get("/api/catalog").json()
    item = cat["collections"][0]["items"][0]

    r = c.post("/api/catalog/add", json={"collection_id": cat["collections"][0]["id"], "item_index": 0})
    assert r.status_code == 200, r.text
    art = db.query(ArtworkModel).filter(ArtworkModel.source_url == item["source_url"]).first()
    assert art is not None
    assert art.status == "approved"
    assert art.title == item["title"]
    assert art.is_seed is False
    assert art.thumbnail_url == item["thumbnail_url"]
    # Image dimensions came from the (mocked) downloaded file.
    assert art.original_width == 40 and art.original_height == 30


def test_add_is_idempotent_and_flips_added_flag(client):
    c, db = client
    cid = c.get("/api/catalog").json()["collections"][0]["id"]
    c.post("/api/catalog/add", json={"collection_id": cid, "item_index": 0})
    c.post("/api/catalog/add", json={"collection_id": cid, "item_index": 0})  # dup
    assert db.query(ArtworkModel).count() == 1  # deduped on source_url

    cat = c.get("/api/catalog").json()
    assert cat["collections"][0]["items"][0]["added"] is True


def test_add_with_playlist_links_it(client):
    c, db = client
    pl = PlaylistModel(name="My Wall")
    db.add(pl); db.commit(); db.refresh(pl)

    cid = c.get("/api/catalog").json()["collections"][0]["id"]
    r = c.post("/api/catalog/add", json={"collection_id": cid, "item_index": 1, "playlist_id": pl.id})
    assert r.status_code == 200, r.text
    links = db.execute(playlist_artwork.select().where(playlist_artwork.c.playlist_id == pl.id)).all()
    assert len(links) == 1


def test_unknown_collection_and_index_404(client):
    c, _ = client
    assert c.post("/api/catalog/add", json={"collection_id": "nope", "item_index": 0}).status_code == 404
    cid = c.get("/api/catalog").json()["collections"][0]["id"]
    assert c.post("/api/catalog/add", json={"collection_id": cid, "item_index": 999}).status_code == 404

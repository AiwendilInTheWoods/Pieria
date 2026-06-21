"""
Catalog endpoint tests — split manifest (index + per-collection) + lazy add.

A temp catalog dir with controlled fixtures is wired in via app.CATALOG_DIR; the remote image
download is mocked; library/playlist writes go to a tmp dir. No network is touched.
"""

import io
import json

import pytest
from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app as app_module
from app import app
from database import Base, get_db
from models import ArtworkModel, PlaylistModel, playlist_artwork

ITEM_A = {
    "title": "Test Sunrise", "agent_name": "A. Painter", "agent_role": "Painter",
    "creation_date": "1872", "date_display": "1872", "medium": "Oil on canvas",
    "cultural_context": "Impressionism", "description_narrative": "A test placard.", "tags": "sea, dawn",
    "source": "Test Museum", "license": "Public Domain",
    "source_url": "https://example.test/a/full.jpg", "thumbnail_url": "https://example.test/a/thumb.jpg",
}
ITEM_B = dict(ITEM_A, title="Test Dusk", source_url="https://example.test/b/full.jpg",
              thumbnail_url="https://example.test/b/thumb.jpg")


@pytest.fixture
def client(monkeypatch, tmp_path):
    # In-memory DB
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(bind=engine)
    db = sessionmaker(bind=engine, autocommit=False, autoflush=False)()

    def _override_db():
        yield db
    app.dependency_overrides[get_db] = _override_db

    # Temp split catalog fixtures
    cat = tmp_path / "catalog"
    cat.mkdir()
    (cat / "index.json").write_text(json.dumps({"version": 1, "collections": [
        {"id": "demo", "title": "Demo", "description": "d", "source": "Test Museum",
         "license": "Public Domain", "count": 2, "cover_thumbnail": ITEM_A["thumbnail_url"]},
    ]}))
    (cat / "demo.json").write_text(json.dumps({
        "id": "demo", "title": "Demo", "description": "d", "source": "Test Museum",
        "license": "Public Domain", "items": [ITEM_A, ITEM_B],
    }))
    monkeypatch.setattr(app_module, "CATALOG_DIR", cat)

    # Redirect library/playlist writes
    monkeypatch.setattr(app_module, "LIBRARY_DIR", tmp_path)
    monkeypatch.setattr(app_module, "ARTWORK_ROOT", tmp_path)

    # Mock the high-res download with a real tiny PNG
    buf = io.BytesIO()
    Image.new("RGB", (40, 30), (10, 20, 30)).save(buf, format="PNG")
    png = buf.getvalue()

    class _Resp:
        status_code = 200
        content = png

    class _Client:
        def __init__(self, *a, **k): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def get(self, url, **k): return _Resp()

    monkeypatch.setattr(app_module.httpx, "AsyncClient", _Client)

    with TestClient(app) as c:
        yield c, db
    app.dependency_overrides.clear()
    db.close()


def test_index_returns_collection_summaries(client):
    c, _ = client
    r = c.get("/api/catalog")
    assert r.status_code == 200
    data = r.json()
    assert [col["id"] for col in data["collections"]] == ["demo"]
    assert data["collections"][0]["count"] == 2
    assert "cover_thumbnail" in data["collections"][0]
    # Index must NOT inline items (that's the per-collection endpoint).
    assert "items" not in data["collections"][0]


def test_collection_returns_items_with_added_flag(client):
    c, _ = client
    r = c.get("/api/catalog/demo")
    assert r.status_code == 200
    items = r.json()["items"]
    assert len(items) == 2
    assert all(it["added"] is False for it in items)


def test_unknown_collection_404(client):
    c, _ = client
    assert c.get("/api/catalog/nope").status_code == 404


def test_add_creates_artwork_and_flips_added(client):
    c, db = client
    r = c.post("/api/catalog/add", json={"collection_id": "demo", "item_index": 0})
    assert r.status_code == 200, r.text
    art = db.query(ArtworkModel).filter(ArtworkModel.source_url == ITEM_A["source_url"]).first()
    assert art is not None and art.status == "approved" and art.title == "Test Sunrise"
    assert art.original_width == 40 and art.original_height == 30
    # added flag now true for item 0
    items = c.get("/api/catalog/demo").json()["items"]
    assert items[0]["added"] is True and items[1]["added"] is False


def test_add_is_idempotent(client):
    c, db = client
    c.post("/api/catalog/add", json={"collection_id": "demo", "item_index": 0})
    c.post("/api/catalog/add", json={"collection_id": "demo", "item_index": 0})
    assert db.query(ArtworkModel).count() == 1


def test_add_with_playlist_links_it(client):
    c, db = client
    pl = PlaylistModel(name="Wall")
    db.add(pl); db.commit(); db.refresh(pl)
    r = c.post("/api/catalog/add", json={"collection_id": "demo", "item_index": 1, "playlist_id": pl.id})
    assert r.status_code == 200, r.text
    links = db.execute(playlist_artwork.select().where(playlist_artwork.c.playlist_id == pl.id)).all()
    assert len(links) == 1


def test_add_unknown_index_404(client):
    c, _ = client
    assert c.post("/api/catalog/add", json={"collection_id": "demo", "item_index": 99}).status_code == 404

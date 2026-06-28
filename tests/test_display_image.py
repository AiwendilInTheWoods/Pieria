"""The resolution-capped Canvas display image (/artworks/{id}/display.jpg).

Museum originals can be 150+ MP / 100 MB — too big for a Pi-class browser to paint.
The Canvas loads a capped derivative instead; the full-res original stays on disk.
"""

import io

import pytest
from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app as app_module
from app import DISPLAY_MAX_EDGE, app
from config import LIBRARY_DIR
from database import Base, get_db
from models import ArtworkModel


@pytest.fixture
def client():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(bind=engine)
    db = sessionmaker(bind=engine, autocommit=False, autoflush=False)()

    def _override_db():
        yield db
    app.dependency_overrides[get_db] = _override_db
    with TestClient(app) as c:
        yield c, db
    app.dependency_overrides.clear()
    db.close()


@pytest.fixture
def make_artwork(client):
    """Write a real image into LIBRARY_DIR, register it, and clean up both the
    original and any generated derivatives afterwards."""
    c, db = client
    created = []

    def _make(name, size, color=(120, 80, 40)):
        LIBRARY_DIR.mkdir(parents=True, exist_ok=True)
        path = LIBRARY_DIR / name
        Image.new("RGB", size, color).save(path, format="JPEG", quality=85)
        art = ArtworkModel(filename=name, title="t", status="approved")
        db.add(art); db.commit(); db.refresh(art)
        created.append((path, art.id))
        return c, art

    yield _make

    for path, art_id in created:
        path.unlink(missing_ok=True)
        for d in app_module.DERIVATIVES_DIR.glob(f"{art_id}-*"):
            d.unlink(missing_ok=True)


def test_oversized_image_is_capped(make_artwork):
    # A landscape original well beyond the cap (and beyond the 8192 GPU texture ceiling).
    c, art = make_artwork("_test_big_landscape.jpg", (12000, 8000))
    r = c.get(f"/artworks/{art.id}/display.jpg")
    assert r.status_code == 200
    assert r.headers["content-type"] == "image/jpeg"
    w, h = Image.open(io.BytesIO(r.content)).size
    assert max(w, h) == DISPLAY_MAX_EDGE          # long edge pinned to the cap
    assert (w, h) == (DISPLAY_MAX_EDGE, 5120)     # aspect ratio preserved (12000:8000 = 3:2)


def test_portrait_image_is_capped_on_long_edge(make_artwork):
    c, art = make_artwork("_test_big_portrait.jpg", (8000, 12000))
    r = c.get(f"/artworks/{art.id}/display.jpg")
    w, h = Image.open(io.BytesIO(r.content)).size
    assert max(w, h) == DISPLAY_MAX_EDGE
    assert h > w                                  # still portrait


def test_small_image_passes_through(make_artwork):
    # Already under the cap → not upscaled.
    c, art = make_artwork("_test_small.jpg", (2400, 1600))
    r = c.get(f"/artworks/{art.id}/display.jpg")
    assert r.status_code == 200
    w, h = Image.open(io.BytesIO(r.content)).size
    assert (w, h) == (2400, 1600)


def test_derivative_is_cached_on_disk(make_artwork):
    c, art = make_artwork("_test_cache.jpg", (9000, 6000))
    for d in app_module.DERIVATIVES_DIR.glob(f"{art.id}-*"):   # ignore leftovers from other tests (shared dir, id reused)
        d.unlink(missing_ok=True)
    c.get(f"/artworks/{art.id}/display.jpg")
    assert list(app_module.DERIVATIVES_DIR.glob(f"{art.id}-*"))   # written through to disk


def test_display_404_for_missing_artwork(client):
    c, _ = client
    assert c.get("/artworks/999999/display.jpg").status_code == 404

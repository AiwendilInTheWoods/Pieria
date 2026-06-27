"""Bulk multi-select endpoints (Inc 4): bulk link/unlink to a playlist + bulk delete from library."""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app as app_module
from app import app
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
        yield c, db, tmp_path
    app.dependency_overrides.clear()
    db.close()


def _art(db, tmp_path, name):
    """Create an artwork row plus a real file on disk (so delete can unlink it)."""
    (tmp_path / name).write_bytes(b"img")
    a = ArtworkModel(filename=name, original_width=10, original_height=10, status="approved")
    db.add(a); db.commit(); db.refresh(a)
    return a


def _members(db, pid):
    return sorted(r[0] for r in db.execute(
        select(playlist_artwork.c.artwork_id).where(playlist_artwork.c.playlist_id == pid)).all())


def test_bulk_link_adds_many_and_is_idempotent(client):
    c, db, tmp = client
    pl = PlaylistModel(name="Wall"); db.add(pl); db.commit(); db.refresh(pl)
    a, b, d = _art(db, tmp, "a.jpg"), _art(db, tmp, "b.jpg"), _art(db, tmp, "d.jpg")

    r = c.post(f"/playlists/{pl.id}/artworks", json={"artwork_ids": [a.id, b.id, d.id]})
    assert r.status_code == 200 and r.json()["count"] == 3
    assert _members(db, pl.id) == sorted([a.id, b.id, d.id])

    # re-linking the same ids must not duplicate rows (idempotent helper)
    c.post(f"/playlists/{pl.id}/artworks", json={"artwork_ids": [a.id, b.id]})
    assert _members(db, pl.id) == sorted([a.id, b.id, d.id])


def test_bulk_unlink_removes_only_listed(client):
    c, db, tmp = client
    pl = PlaylistModel(name="Wall"); db.add(pl); db.commit(); db.refresh(pl)
    a, b, d = _art(db, tmp, "a.jpg"), _art(db, tmp, "b.jpg"), _art(db, tmp, "d.jpg")
    c.post(f"/playlists/{pl.id}/artworks", json={"artwork_ids": [a.id, b.id, d.id]})

    r = c.request("DELETE", f"/playlists/{pl.id}/artworks", json={"artwork_ids": [b.id, d.id]})
    assert r.status_code == 200 and r.json()["count"] == 2
    assert _members(db, pl.id) == [a.id]
    # the artworks themselves still exist in the library
    assert db.get(ArtworkModel, b.id) is not None


def test_bulk_delete_wipes_files_and_rows(client):
    c, db, tmp = client
    a, b = _art(db, tmp, "a.jpg"), _art(db, tmp, "b.jpg")
    assert (tmp / "a.jpg").exists() and (tmp / "b.jpg").exists()

    r = c.post("/artworks/delete", json={"artwork_ids": [a.id, b.id, 99999]})  # unknown id ignored
    assert r.status_code == 200 and r.json()["count"] == 2
    assert db.get(ArtworkModel, a.id) is None and db.get(ArtworkModel, b.id) is None
    assert not (tmp / "a.jpg").exists() and not (tmp / "b.jpg").exists()


def test_bulk_delete_also_clears_playlist_membership(client):
    c, db, tmp = client
    pl = PlaylistModel(name="Wall"); db.add(pl); db.commit(); db.refresh(pl)
    a = _art(db, tmp, "a.jpg")
    c.post(f"/playlists/{pl.id}/artworks", json={"artwork_ids": [a.id]})
    assert _members(db, pl.id) == [a.id]

    c.post("/artworks/delete", json={"artwork_ids": [a.id]})
    assert _members(db, pl.id) == []   # association gone with the artwork


def test_bulk_endpoints_tolerate_empty_lists(client):
    c, db, tmp = client
    pl = PlaylistModel(name="Wall"); db.add(pl); db.commit(); db.refresh(pl)
    assert c.post(f"/playlists/{pl.id}/artworks", json={"artwork_ids": []}).json()["count"] == 0
    assert c.request("DELETE", f"/playlists/{pl.id}/artworks", json={"artwork_ids": []}).json()["count"] == 0
    assert c.post("/artworks/delete", json={"artwork_ids": []}).json()["count"] == 0

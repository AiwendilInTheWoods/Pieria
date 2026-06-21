"""The server-hosted /art/{id} 'Learn More' page the placard QR points at."""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import app
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


def test_detail_page_renders(client):
    c, db = client
    art = ArtworkModel(
        filename="x.jpg", title="The Starry Night", agent_name="Vincent van Gogh",
        date_display="1889", cultural_context="Post-Impressionism", medium="Oil on canvas",
        description_narrative="A night sky over a village.", tags="night, sky",
        source_url="https://museum.test/starry", status="approved")
    db.add(art); db.commit(); db.refresh(art)

    r = c.get(f"/art/{art.id}")
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]
    body = r.text
    assert "The Starry Night" in body
    assert "Vincent van Gogh" in body
    assert f"/artworks/{art.id}/preview" in body   # hero image points at our server, not a CDN
    assert "https://museum.test/starry" in body    # source link present


def test_detail_page_escapes_html(client):
    c, db = client
    art = ArtworkModel(filename="y.jpg", title="<script>alert(1)</script>", status="approved")
    db.add(art); db.commit(); db.refresh(art)
    r = c.get(f"/art/{art.id}")
    assert "<script>alert(1)</script>" not in r.text   # escaped, not injected
    assert "&lt;script&gt;" in r.text


def test_detail_page_404(client):
    c, _ = client
    assert c.get("/art/999999").status_code == 404

#!/usr/bin/env python3
"""
FastAPI Backend for the Artwork Display Engine.
Phase 4: Targeted WebSocket Routing for Multiple Displays.
"""

import asyncio
import fcntl
import html
import io
import json
import logging
import os
import random
import shutil
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from functools import lru_cache
from pathlib import Path
from typing import Dict, List, Optional
from urllib.parse import quote

from dotenv import load_dotenv
from fastapi import (
    BackgroundTasks,
    Depends,
    FastAPI,
    File,
    Form,
    HTTPException,
    Query,
    Request,
    UploadFile,
    WebSocket,
    WebSocketDisconnect,
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, Response
from fastapi.staticfiles import StaticFiles
from PIL import Image, ImageOps
from pydantic import BaseModel
from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session

# Load environment variables
load_dotenv()

# -----------------------------------------------------------------------------
# 1. Configuration, Logging & Targeted WebSocket Manager
# -----------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger("artwork-display-api")

class ConnectionManager:
    """Manages targeted WebSocket connections grouped by display_id."""
    def __init__(self):
        # Maps display_id -> list of active WebSocket connections
        self.active_connections: Dict[str, List[WebSocket]] = {}

    async def connect(self, websocket: WebSocket, display_id: str):
        await websocket.accept()
        if display_id not in self.active_connections:
            self.active_connections[display_id] = []
        self.active_connections[display_id].append(websocket)
        logger.info(f"New connection to display '{display_id}'. Total for ID: {len(self.active_connections[display_id])}")

    def disconnect(self, websocket: WebSocket, display_id: str):
        if display_id in self.active_connections:
            if websocket in self.active_connections[display_id]:
                self.active_connections[display_id].remove(websocket)
                if not self.active_connections[display_id]:
                    del self.active_connections[display_id]
            logger.info(f"Disconnected from display '{display_id}'.")

    async def send_personal_message(self, message: dict, display_id: str):
        """Sends a JSON message only to sockets registered under a specific display_id."""
        if display_id in self.active_connections:
            for connection in self.active_connections[display_id]:
                try:
                    await connection.send_json(message)
                except Exception:
                    pass

    async def broadcast(self, message: dict):
        """Sends a JSON message to absolutely all connected clients."""
        for display_id in self.active_connections:
            for connection in self.active_connections[display_id]:
                try:
                    await connection.send_json(message)
                except Exception:
                    pass

manager = ConnectionManager()

# Local imports
import httpx

import curator
import scout
from agents import process_artwork
from database import SessionLocal, get_db, init_db
from models import (
    ActiveDisplayModel,
    ArtworkModel,
    DiscoveryQueueModel,
    DisplayPlaybackSessionModel,
    PlaylistModel,
    RemoteCommandModel,
    SettingsModel,
    SubscriptionModel,
    playlist_artwork,
)
from query_classifier import QueryClassifier
from result_ranker import ResultRanker, clean_title
from scout import create_search_session, get_search_session

# Shared instances for smart search
_query_classifier = QueryClassifier()
_result_ranker = ResultRanker()

import ai_client
import federation
import frame_push
from config import ARTWORK_ROOT, LIBRARY_DIR, SD_USER_AGENT
from epaper import PALETTES, VALID_FORMATS, media_type_for, render_for_epaper


@lru_cache(maxsize=256)
def get_optimized_image(image_path: Path, size: tuple, quality: int = 85) -> bytes:
    """Resizes and compresses an image for web delivery."""
    logger.info(f"[Image Processor] Optimizing: {image_path.name}")
    with Image.open(image_path) as img:
        if img.mode in ("RGBA", "P"):
            img = img.convert("RGB")
        img.thumbnail(size, Image.Resampling.LANCZOS)
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=quality)
        return buf.getvalue()

async def run_ai_pipeline(artwork_id: int):
    db = SessionLocal()
    try:
        await process_artwork(artwork_id, db)
    finally:
        db.close()

async def run_rag_pipeline(artwork_id: int, context_hints: str = None):
    db = SessionLocal()
    try:
        await curator.enrich_artwork(artwork_id, db, context_hints=context_hints)
    finally:
        db.close()

async def run_scouts_bg(query: str = None, sources: List[str] = None,
                       session_id: str = None, limit: int = 10):
    """Background task: classifies query, runs scouts, ranks results, inserts into DB."""
    db = SessionLocal()
    try:
        # Retrieve or create search session
        session = get_search_session(session_id) if session_id else None
        if session:
            intent = session.intent
            offset = session.offset
        else:
            intent = _query_classifier.classify(query) if query else None
            offset = 0

        logger.info(f"[Scout BG] Starting scouts: query='{query}', sources={sources}, "
                    f"intent={intent.query_type if intent else 'none'}, "
                    f"canonical='{intent.canonical_name if intent else 'n/a'}', "
                    f"offset={offset}, limit={limit}")

        # Run scouts with classified intent
        raw_results = await scout.run_scouts(
            db, query=query, sources=sources,
            intent=intent, offset=offset, limit=limit
        )
        logger.info(f"[Scout BG] Scouts returned {len(raw_results)} raw results")

        # Rank and deduplicate
        ranked_results = _result_ranker.rank_and_deduplicate(raw_results, intent)
        logger.info(f"[Scout BG] After ranking: {len(ranked_results)} results")

        # Insert into DiscoveryQueue, skipping duplicates
        total_new = 0
        for item in ranked_results:
            existing = db.query(DiscoveryQueueModel).filter(
                DiscoveryQueueModel.source_url == item['source_url']
            ).first()
            if not existing:
                item['proposed_title'] = clean_title(item.get('proposed_title'))
                new_entry = DiscoveryQueueModel(
                    **item,
                    search_session_id=session_id
                )
                db.add(new_entry)
                total_new += 1
        db.commit()
        logger.info(f"[Scout BG] DiscoveryQueue updated with {total_new} new items.")
    except Exception as e:
        logger.error(f"[Scout BG] BACKGROUND TASK FAILED: {e}", exc_info=True)
    finally:
        db.close()

async def run_batch_enrich_bg():
    db = SessionLocal()
    try:
        await curator.batch_enrich_all(db)
    finally:
        db.close()

def sync_db_with_filesystem(db: Session) -> None:
    if not ARTWORK_ROOT.exists():
        ARTWORK_ROOT.mkdir(parents=True, exist_ok=True)
    if not LIBRARY_DIR.exists():
        LIBRARY_DIR.mkdir(parents=True, exist_ok=True)

    valid_extensions = {".jpg", ".jpeg", ".png", ".webp"}
    for item in ARTWORK_ROOT.iterdir():
        if item.is_dir() and item.name != "_Library":
            playlist = db.query(PlaylistModel).filter(PlaylistModel.name == item.name).first()
            if not playlist:
                playlist = PlaylistModel(name=item.name)
                db.add(playlist); db.commit(); db.refresh(playlist)

            for file_path in item.iterdir():
                if file_path.suffix.lower() in valid_extensions:
                    dest_path = LIBRARY_DIR / file_path.name
                    if not dest_path.exists():
                        shutil.move(file_path, dest_path)

                    artwork = db.query(ArtworkModel).filter(ArtworkModel.filename == file_path.name).first()
                    if not artwork:
                        with Image.open(dest_path) as img:
                            w, h = img.size
                        artwork = ArtworkModel(
                            filename=file_path.name,
                            original_width=w, original_height=h,
                            status='approved'
                        )
                        db.add(artwork); db.commit(); db.refresh(artwork)

                    existing_link = db.execute(
                        select(playlist_artwork).where(
                            playlist_artwork.c.playlist_id == playlist.id,
                            playlist_artwork.c.artwork_id == artwork.id
                        )
                    ).first()

                    if not existing_link:
                        db.execute(playlist_artwork.insert().values(
                            playlist_id=playlist.id,
                            artwork_id=artwork.id,
                            display_order=0
                        ))
            db.commit()

async def run_factory_seed(db: Session):
    """Parses factory_seed.json and injects masterpieces if library is empty."""
    seed_file = Path("static/factory_seed.json")
    if not seed_file.exists(): return

    existing = db.query(ArtworkModel).filter(ArtworkModel.is_seed == True).first()
    if existing: return

    try:
        import json
        with open(seed_file) as f:
            seeds = json.load(f)

        logger.info(f"[Bootstrapper] Injecting {len(seeds)} Masterpieces from Factory Seed...")

        async def perform_downloads(seed_items: list):
            db_local = SessionLocal()
            try:
                await asyncio.sleep(2)
                for idx, item in enumerate(seed_items):
                    await asyncio.sleep(2.0)
                    try:
                        pl_name = item.get("playlist", "The Masterpieces")
                        playlist = db_local.query(PlaylistModel).filter(PlaylistModel.name == pl_name).first()
                        if not playlist:
                            playlist = PlaylistModel(name=pl_name)
                            db_local.add(playlist); db_local.commit(); db_local.refresh(playlist)
                            (ARTWORK_ROOT / pl_name).mkdir(parents=True, exist_ok=True)

                        filename = f"seed_{idx}_{item.get('title', 'art').replace(' ','_').lower()[:15]}"
                        logger.info(f"[Bootstrapper] Downloading '{filename}'...")

                        # Shared robust downloader (UA + 429 retry + validation).
                        try:
                            dest_path, safe_name, w, h = await _download_image_to_library(
                                item.get("source_url"), filename=filename)
                        except HTTPException as e:
                            logger.error(f"[Bootstrapper] Failed download {filename}: {e.detail}")
                            continue

                        pl_path = ARTWORK_ROOT / pl_name / safe_name
                        # Remove stale symlink before creating new one
                        if pl_path.is_symlink() or pl_path.exists():
                            pl_path.unlink()
                        try: os.symlink(dest_path.resolve(), pl_path)
                        except OSError: shutil.copy(dest_path, pl_path)

                        sfx, sfy = _focal_xy(item)
                        artwork = ArtworkModel(
                            filename=safe_name, original_width=w, original_height=h,
                            crop_width=float(w), crop_height=float(h),
                            status='approved',
                            title=item.get("title"), agent_name=item.get("agent_name"),
                            agent_role=item.get("agent_role"), creation_date=item.get("creation_date"),
                            cultural_context=item.get("cultural_context"), medium=item.get("medium"),
                            date_display=item.get("date_display"), description_narrative=item.get("description_narrative"),
                            tags=item.get("tags"), is_seed=True,
                            focal_x=sfx, focal_y=sfy,
                        )
                        db_local.add(artwork); db_local.commit(); db_local.refresh(artwork)

                        try:
                            db_local.execute(playlist_artwork.insert().values(
                                playlist_id=playlist.id, artwork_id=artwork.id, display_order=idx
                            ))
                            db_local.commit()
                        except Exception:
                            db_local.rollback()  # playlist_artwork may already exist

                        logger.info(f"[Bootstrapper] ✓ Seeded '{item.get('title')}' → {pl_name}")

                    except Exception as inner_e: logger.error(f"[Bootstrapper] Item error: {inner_e}")
            finally: db_local.close()

        asyncio.create_task(perform_downloads(seeds))

    except Exception as e:
        logger.error(f"[Bootstrapper] Failed to parse factory_seed.json: {e}")

from alembic import command
from alembic.config import Config


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Lifecycle events for FastAPI application with multi-worker concurrency locks."""

    lock_file = None
    try:
        # Leader Election using fcntl
        # The first worker grabs the exclusive non-blocking lock.
        # The other 3 workers will throw a BlockingIOError and skip initialization.
        lock_file = open("/tmp/screen_docent_startup.lock", "w")
        fcntl.flock(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)

        logger.info("[Startup] Leader elected. Running exclusive boot tasks (migrations, filesystem sync)...")

        # Run Alembic migrations on startup
        alembic_cfg = Config("alembic.ini")
        logger.info("Running Alembic migrations...")
        try:
            command.upgrade(alembic_cfg, "head")
            logger.info("Alembic migrations complete.")
        except Exception as e:
            logger.error(f"Failed to run Alembic migrations: {e}")

        init_db()
        db = SessionLocal()
        try:
            sync_db_with_filesystem(db)
            await run_factory_seed(db)
        finally:
            db.close()

        # Leader-only: the Samsung Frame TV pusher. Running it solely in the leader avoids
        # firing it once per uvicorn worker. No-op until enabled in Settings → Frame TV.
        asyncio.create_task(frame_push.frame_push_loop(_frame_select))
        logger.info("[Startup] Frame TV push loop scheduled (leader).")

    except BlockingIOError:
        logger.info("[Startup] Follower worker initialized. Skipping exclusive boot tasks.")
    except Exception as e:
        logger.error(f"[Startup] Unexpected error during initialization: {e}")
    finally:
        # We deliberately do NOT unlock the file here.
        # If we unlock it while the leader is still finishing startup tasks,
        # a slightly delayed follower worker could grab the lock and run migrations concurrently.
        # The OS will naturally release the lock when the Uvicorn worker process terminates on server shutdown.
        yield

app = FastAPI(title="Artwork Display Engine API", version="0.4.5", lifespan=lifespan)

@app.middleware("http")
async def inject_aggressive_cache_headers(request: Request, call_next):
    response = await call_next(request)
    # Target Pillow rendering routes, media library, and static assets
    path = request.url.path
    is_media_cacheable = (
        (path.startswith("/artworks/") and ("thumbnail" in path or "preview" in path))
        or path.startswith("/media/")
        or path.endswith((".svg", ".png", ".jpg", ".webp"))
    )
    is_code_asset = path.endswith((".css", ".js", ".json"))
    is_html_asset = path.endswith(".html") or path == "/admin" or path == "/remote" or path == "/"

    if path.startswith("/api/") or is_html_asset or path.startswith("/display/"):
        # API/HTML and the per-display e-ink endpoint must never be cached.
        # (/display/*.png must beat the is_media_cacheable .png rule below.)
        response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate"
    elif is_media_cacheable:
        # Images/media rarely change — cache aggressively
        response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
    elif is_code_asset:
        # JS/CSS/JSON change during development — short cache + revalidate
        response.headers["Cache-Control"] = "public, max-age=60, must-revalidate"
    return response

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)



# -----------------------------------------------------------------------------
# 2. Data Models
# -----------------------------------------------------------------------------
class ArtworkSchema(BaseModel):
    id: int
    filename: str
    original_width: int
    original_height: int
    title: Optional[str] = None
    agent_name: Optional[str] = None
    agent_role: Optional[str] = None
    creation_date: Optional[str] = None; cultural_context: Optional[str] = None
    medium: Optional[str] = None; date_display: Optional[str] = None
    description_narrative: Optional[str] = None; tags: Optional[str] = None
    status: str
    crop_x: float
    crop_y: float
    crop_width: float
    crop_height: float
    focal_x: float = 0.5
    focal_y: float = 0.5
    is_personal: bool = False
    model_config = {"from_attributes": True}

class PlaylistSchema(BaseModel):
    id: int
    name: str
    display_time: int
    default_mode: str
    shuffle: bool
    placard_initial_wait_sec: int
    placard_initial_show_sec: int
    placard_interaction_show_sec: int
    artworks: List[ArtworkSchema] = []
    @property
    def image_count(self) -> int:
        return len(self.artworks)
    model_config = {"from_attributes": True}

class ArtworkApproval(BaseModel):
    title: str; agent_name: str; agent_role: str; creation_date: str; cultural_context: str; medium: str; date_display: str; description_narrative: str; tags: str

class CropMetadataUpdate(BaseModel):
    crop_x: float; crop_y: float; crop_width: float; crop_height: float

class PlaylistUpdate(BaseModel):
    display_time: Optional[int] = None
    default_mode: Optional[str] = None
    shuffle: Optional[bool] = None
    placard_initial_wait_sec: Optional[int] = None
    placard_initial_show_sec: Optional[int] = None
    placard_interaction_show_sec: Optional[int] = None

class ReorderRequest(BaseModel):
    artwork_ids: List[int]

class RemoteChangeRequest(BaseModel):
    target_display: str
    action: str
    playlist: Optional[str] = None
    mode: Optional[str] = None

class RegenerationRequest(BaseModel):
    hint: Optional[str] = None

class DispatchRequest(BaseModel):
    sources: List[str]
    search: Optional[str] = None
    limit: int = 10

class LoadMoreRequest(BaseModel):
    session_id: str

class DiscoveryQueueSchema(BaseModel):
    id: int
    source_url: str
    thumbnail_url: str
    proposed_title: Optional[str] = None
    proposed_artist: Optional[str] = None
    source_api: str
    status: str
    relevance_score: Optional[float] = 0.0
    search_session_id: Optional[str] = None
    model_config = {"from_attributes": True}

# -----------------------------------------------------------------------------
# 3. API Endpoints
# -----------------------------------------------------------------------------

@app.get("/artworks", response_model=List[ArtworkSchema])
async def get_full_library(db: Session = Depends(get_db)):
    return db.query(ArtworkModel).all()

@app.get("/playlists", response_model=List[PlaylistSchema])
async def list_playlists(db: Session = Depends(get_db)):
    return db.query(PlaylistModel).all()

@app.post("/playlists", response_model=PlaylistSchema)
async def create_playlist(name: str = Form(...), db: Session = Depends(get_db)):
    existing = db.query(PlaylistModel).filter(PlaylistModel.name == name).first()
    if existing: raise HTTPException(status_code=400, detail="Exists")
    new_p = PlaylistModel(name=name); db.add(new_p); db.commit(); db.refresh(new_p)
    return new_p

@app.patch("/playlists/{playlist_id}", response_model=PlaylistSchema)
async def update_playlist(playlist_id: int, data: PlaylistUpdate, db: Session = Depends(get_db)):
    p = db.query(PlaylistModel).filter(PlaylistModel.id == playlist_id).first()
    if not p: raise HTTPException(status_code=404)
    if data.display_time is not None: p.display_time = data.display_time
    if data.default_mode is not None: p.default_mode = data.default_mode
    if data.shuffle is not None: p.shuffle = data.shuffle
    if data.placard_initial_wait_sec is not None: p.placard_initial_wait_sec = data.placard_initial_wait_sec
    if data.placard_initial_show_sec is not None: p.placard_initial_show_sec = data.placard_initial_show_sec
    if data.placard_interaction_show_sec is not None: p.placard_interaction_show_sec = data.placard_interaction_show_sec
    db.commit(); db.refresh(p); return p

@app.delete("/playlists/{playlist_id}")
async def delete_playlist(playlist_id: int, db: Session = Depends(get_db)):
    p = db.query(PlaylistModel).filter(PlaylistModel.id == playlist_id).first()
    if not p: raise HTTPException(404)
    db.delete(p); db.commit(); return {"status": "ok"}

@app.post("/playlists/{playlist_id}/artworks/{artwork_id}")
async def link_artwork_to_playlist(playlist_id: int, artwork_id: int, db: Session = Depends(get_db)):
    db.execute(playlist_artwork.insert().values(playlist_id=playlist_id, artwork_id=artwork_id))
    db.commit(); return {"status": "linked"}

@app.delete("/playlists/{playlist_id}/artworks/{artwork_id}")
async def unlink_artwork_from_playlist(playlist_id: int, artwork_id: int, db: Session = Depends(get_db)):
    db.execute(delete(playlist_artwork).where(
        playlist_artwork.c.playlist_id == playlist_id,
        playlist_artwork.c.artwork_id == artwork_id
    ))
    db.commit(); return {"status": "unlinked"}

@app.post("/playlists/{playlist_id}/reorder")
async def reorder_playlist(playlist_id: int, request: ReorderRequest, db: Session = Depends(get_db)):
    for index, art_id in enumerate(request.artwork_ids):
        db.execute(update(playlist_artwork).where(
            playlist_artwork.c.playlist_id == playlist_id,
            playlist_artwork.c.artwork_id == art_id
        ).values(display_order=index))
    db.commit(); return {"status": "success"}

@app.post("/upload", response_model=ArtworkSchema)
async def upload_artwork(background_tasks: BackgroundTasks, file: UploadFile = File(...), playlist_id: Optional[int] = Form(None), db: Session = Depends(get_db)):
    if not LIBRARY_DIR.exists(): LIBRARY_DIR.mkdir(parents=True)
    f_path = LIBRARY_DIR / file.filename
    with open(f_path, "wb") as b: shutil.copyfileobj(file.file, b)
    with Image.open(f_path) as img: w, h = img.size
    new_a = ArtworkModel(filename=file.filename, original_width=w, original_height=h, status='pending_review')
    db.add(new_a); db.commit(); db.refresh(new_a)
    if playlist_id:
        db.execute(playlist_artwork.insert().values(playlist_id=playlist_id, artwork_id=new_a.id))
        db.commit()
    background_tasks.add_task(run_ai_pipeline, new_a.id)
    return new_a


PERSONAL_PLAYLIST_NAME = "My Photos"


def _get_or_create_playlist(db: Session, name: str) -> PlaylistModel:
    pl = db.query(PlaylistModel).filter(PlaylistModel.name == name).first()
    if not pl:
        pl = PlaylistModel(name=name)
        db.add(pl); db.commit(); db.refresh(pl)
    return pl


def _link_artwork_to_playlist(db: Session, playlist_id: int, artwork_id: int) -> None:
    """Append an artwork to a playlist (idempotent), preserving append order."""
    exists = db.execute(select(playlist_artwork).where(
        playlist_artwork.c.playlist_id == playlist_id,
        playlist_artwork.c.artwork_id == artwork_id)).first()
    if exists:
        return
    order = len(db.execute(select(playlist_artwork.c.artwork_id).where(
        playlist_artwork.c.playlist_id == playlist_id)).all())
    db.execute(playlist_artwork.insert().values(
        playlist_id=playlist_id, artwork_id=artwork_id, display_order=order))
    db.commit()


@app.post("/upload/personal", response_model=ArtworkSchema)
async def upload_personal_photo(
    file: UploadFile = File(...),
    caption: Optional[str] = Form(None),
    date: Optional[str] = Form(None),
    playlist_id: Optional[int] = Form(None),
    db: Session = Depends(get_db),
):
    """Studio → My Photos: add one of the user's OWN photos to the local library. Unlike /upload, this
    deliberately skips the museum AI pipeline (the photo is never sent to a model — the privacy
    headline) and the review queue: it lands `approved` with `is_personal=True`. EXIF orientation is
    baked in so phone photos display upright; caption/date are optional (shown on a stripped placard).
    Links into the given playlist, or an auto-created "My Photos" playlist."""
    if not LIBRARY_DIR.exists():
        LIBRARY_DIR.mkdir(parents=True)
    raw = await file.read()
    try:
        with Image.open(io.BytesIO(raw)) as src:
            fmt = (src.format or "JPEG").upper()
            img = ImageOps.exif_transpose(src)   # bake phone orientation; drops the EXIF tag
    except Exception:
        raise HTTPException(400, detail="That file isn't a readable image.")

    ext = {"PNG": ".png", "WEBP": ".webp"}.get(fmt, ".jpg")
    stem = Path(file.filename or "").stem
    base = "".join(c for c in (caption or stem or "photo") if c.isalnum() or c in " _-").strip()
    base = (base.replace(" ", "_")[:24] or "photo").lower()
    safe = f"personal_{base}{ext}"
    dest = LIBRARY_DIR / safe
    n = 1
    while dest.exists():
        safe = f"personal_{base}_{n}{ext}"; dest = LIBRARY_DIR / safe; n += 1

    if ext == ".jpg":
        if img.mode not in ("RGB", "L"):
            img = img.convert("RGB")
        img.save(dest, quality=92)
    else:
        img.save(dest)
    w, h = img.size

    art = ArtworkModel(
        filename=safe, original_width=w, original_height=h,
        crop_width=float(w), crop_height=float(h),
        title=(caption or None), date_display=(date or None),
        is_personal=True, status="approved",
    )
    db.add(art); db.commit(); db.refresh(art)

    pl = db.query(PlaylistModel).filter(PlaylistModel.id == playlist_id).first() if playlist_id else None
    if pl is None:
        pl = _get_or_create_playlist(db, PERSONAL_PLAYLIST_NAME)
    _link_artwork_to_playlist(db, pl.id, art.id)
    return art


STUDIO_CAPTION_PROMPT = (
    "You are writing a short, warm caption for someone's personal photo — like the title in a family "
    "photo album or on the back of a postcard. Look at the image and write an evocative title of 3 to "
    "8 words that captures the PLACE, OCCASION, and MOOD. Name the location or event if it is provided "
    "below or clearly recognizable. Do NOT list objects or describe the scene clinically. "
    "Good: \"A Sunny Day at Bondi Beach\". Bad: \"A child holding a bucket and shovel on sand\". "
    "Return ONLY a JSON object: {\"caption\": \"<your title>\"}."
)


class CaptionRequest(BaseModel):
    hint: Optional[str] = None


@app.post("/api/studio/caption/{artwork_id}")
async def suggest_caption(artwork_id: int, payload: CaptionRequest, db: Session = Depends(get_db)):
    """Studio → My Photos: suggest an evocative, album-style caption for a personal photo via the
    configured vision model (opt-in). Returns the suggestion only — the user edits/accepts it in the
    UI. `model_is_local` tells the UI whether the photo stays on-device (a local Ollama/LM Studio
    model) or is sent to a cloud model, so the privacy note can be honest instead of a blanket warning."""
    art = db.query(ArtworkModel).filter(ArtworkModel.id == artwork_id).first()
    if not art:
        raise HTTPException(404, detail="Artwork not found")
    img_path = LIBRARY_DIR / art.filename
    if not img_path.exists():
        raise HTTPException(404, detail="Image file missing")
    cfg = ai_client.get_ai_config(force=True)
    if not cfg["configured"]:
        raise HTTPException(400, detail="No AI model is configured. Add one in Settings → AI Engine, "
                                        "or type a caption yourself.")
    prompt = STUDIO_CAPTION_PROMPT
    hint = (payload.hint or "").strip()
    if hint:
        prompt += f" The user notes about this photo: \"{hint}\" — weave it in naturally."
    parts = [ai_client.text_part(prompt), ai_client.image_part(str(img_path))]
    try:
        resp = await asyncio.to_thread(
            ai_client.chat, "vision", [{"role": "user", "content": parts}], json_mode=True)
        caption = (ai_client.parse_json(resp).get("caption") or "").strip()
    except Exception as e:
        logger.warning(f"[Studio] caption generation failed: {e}")
        raise HTTPException(502, detail="Caption generation failed. Try again, or type one yourself.")
    if not caption:
        raise HTTPException(502, detail="The model didn't return a caption. Try again.")
    return {"caption": caption, "model_is_local": ai_client.is_local_base_url(cfg["base_url"])}


class PersonalPhotoUpdate(BaseModel):
    caption: Optional[str] = None
    date: Optional[str] = None


@app.patch("/api/studio/photo/{artwork_id}", response_model=ArtworkSchema)
async def update_personal_photo(artwork_id: int, payload: PersonalPhotoUpdate, db: Session = Depends(get_db)):
    """Studio → My Photos: save a personal photo's caption (title) and/or date. Restricted to personal
    photos so it can't be used to edit museum/catalog metadata."""
    art = db.query(ArtworkModel).filter(ArtworkModel.id == artwork_id).first()
    if not art or not art.is_personal:
        raise HTTPException(404, detail="Personal photo not found")
    if payload.caption is not None:
        art.title = payload.caption.strip() or None
    if payload.date is not None:
        art.date_display = payload.date.strip() or None
    db.commit(); db.refresh(art)
    return art

@app.get("/artworks/pending", response_model=List[ArtworkSchema])
async def get_pending_artworks(db: Session = Depends(get_db)):
    return db.query(ArtworkModel).filter(ArtworkModel.status == 'pending_review').all()

@app.patch("/artworks/{artwork_id}/approve", response_model=ArtworkSchema)
async def approve_artwork(artwork_id: int, data: ArtworkApproval, db: Session = Depends(get_db)):
    art = db.query(ArtworkModel).filter(ArtworkModel.id == artwork_id).first()
    if not art: raise HTTPException(404)
    art.title, art.agent_name, art.agent_role, art.creation_date, art.cultural_context, art.medium, art.date_display, art.description_narrative, art.tags, art.status = data.title, data.agent_name, data.agent_role, data.creation_date, data.cultural_context, data.medium, data.date_display, data.description_narrative, data.tags, 'approved'
    db.commit(); db.refresh(art); return art

@app.post("/api/curate/regenerate/{artwork_id}", response_model=ArtworkSchema)
async def regenerate_artwork_metadata(artwork_id: int, request: RegenerationRequest, db: Session = Depends(get_db)):
    """Manually triggers the AI pipeline with an optional human-in-the-loop hint."""
    updated_art = await process_artwork(artwork_id, db, user_hint=request.hint)
    if not updated_art:
        raise HTTPException(status_code=500, detail="AI Regeneration failed")
    return updated_art

@app.post("/api/curate/reenrich/{artwork_id}", response_model=ArtworkSchema)
async def reenrich_artwork(artwork_id: int, request: RegenerationRequest, db: Session = Depends(get_db)):
    """Sets artwork status back to pending and triggers AI re-enrichment."""
    art = db.query(ArtworkModel).filter(ArtworkModel.id == artwork_id).first()
    if not art: raise HTTPException(404)

    art.status = 'pending_review'
    db.commit()

    updated_art = await process_artwork(artwork_id, db, user_hint=request.hint)
    return updated_art

@app.post("/api/curate/batch-enrich")
async def batch_enrich(background_tasks: BackgroundTasks, db: Session = Depends(get_db)):
    """Triggers RAG enrichment for all approved artworks."""
    background_tasks.add_task(run_batch_enrich_bg)
    return {"status": "Batch enrichment started in background"}

@app.get("/api/discover/queue", response_model=List[DiscoveryQueueSchema])
async def get_discovery_queue(
    session_id: Optional[str] = Query(None),
    db: Session = Depends(get_db)
):
    """Returns the list of pending art discoveries, optionally filtered by session."""
    query = db.query(DiscoveryQueueModel).filter(DiscoveryQueueModel.status == 'pending')
    if session_id:
        query = query.filter(DiscoveryQueueModel.search_session_id == session_id)
    return query.order_by(DiscoveryQueueModel.relevance_score.desc()).all()

@app.post("/api/discover/dispatch")
async def dispatch_discovery(request: DispatchRequest, background_tasks: BackgroundTasks, db: Session = Depends(get_db)):
    """Smart multi-source art discovery dispatch with query classification."""
    # Classify the query upfront to create a session with the right intent
    intent = _query_classifier.classify(request.search) if request.search else None
    limit = max(1, min(request.limit, 10))  # Clamp to 1–10

    # Create a search session for Load More support
    session = create_search_session(
        query=request.search or "",
        intent=intent,
        sources=request.sources,
        limit=limit
    )

    background_tasks.add_task(
        run_scouts_bg,
        query=request.search,
        sources=request.sources,
        session_id=session.session_id,
        limit=limit
    )
    return {
        "status": "Art scouts dispatched",
        "sources": request.sources,
        "search": request.search,
        "session_id": session.session_id,
        "intent": {
            "type": intent.query_type if intent else "freetext",
            "canonical": intent.canonical_name if intent else request.search,
        }
    }

@app.post("/api/discover/more")
async def load_more_discoveries(request: LoadMoreRequest, background_tasks: BackgroundTasks, db: Session = Depends(get_db)):
    """Fetches the next batch of results from an existing search session."""
    session = get_search_session(request.session_id)
    if not session:
        raise HTTPException(404, detail="Search session expired or not found. Please start a new search.")

    # Advance the offset
    session.offset += session.limit

    background_tasks.add_task(
        run_scouts_bg,
        query=session.query,
        sources=session.sources,
        session_id=session.session_id,
        limit=session.limit
    )
    return {
        "status": "Loading more results",
        "session_id": session.session_id,
        "offset": session.offset
    }

@app.post("/api/discover/approve/{item_id}")
async def approve_discovery(item_id: int, background_tasks: BackgroundTasks, db: Session = Depends(get_db)):
    """Downloads approved discovery and adds to library."""
    item = db.query(DiscoveryQueueModel).filter(DiscoveryQueueModel.id == item_id).first()
    if not item: raise HTTPException(404)

    # 1. Download full-res image via the shared robust downloader (descriptive UA — Wikimedia/NASA
    #    reject the default httpx UA — plus 429 retry, redirects, and image validation).
    filename = f"scouted_{item_id}_{item.proposed_title.replace(' ', '_')[:50]}"
    _, filename, w, h = await _download_image_to_library(item.source_url, filename=filename)

    # 2. Add to database
    new_art = ArtworkModel(
        filename=filename,
        original_width=w, original_height=h,
        title=item.proposed_title,
        agent_name=item.proposed_artist,
        source_url=item.source_url,
        status='processing'
    )
    db.add(new_art)
    item.status = 'approved'
    db.commit()
    db.refresh(new_art)

    # 3. Enrich with RAG Curator
    background_tasks.add_task(run_rag_pipeline, new_art.id, item.context_hints)

    return {"status": "Art added and fully enriched", "artwork_id": new_art.id}

@app.post("/api/discover/reject/{item_id}")
async def reject_discovery(item_id: int, db: Session = Depends(get_db)):
    """Removes a discovery from the queue."""
    item = db.query(DiscoveryQueueModel).filter(DiscoveryQueueModel.id == item_id).first()
    if not item: raise HTTPException(404)
    item.status = 'rejected'
    db.commit()
    return {"status": "Rejected"}

@app.delete("/api/discover/history")
async def clear_rejected_history(db: Session = Depends(get_db)):
    """Deletes all rejected items from the discovery queue to free up the cache."""
    db.execute(delete(DiscoveryQueueModel).where(DiscoveryQueueModel.status == 'rejected'))
    db.commit()
    return {"status": "History cleared"}

@app.delete("/api/discover/orphans")
async def clear_orphaned_approvals(db: Session = Depends(get_db)):
    """Deletes discovery queue items that were 'approved' but have no active artwork entry."""
    approved_items = db.query(DiscoveryQueueModel).filter(DiscoveryQueueModel.status == 'approved').all()
    artworks = db.query(ArtworkModel.filename).filter(ArtworkModel.filename.like('scouted_%')).all()

    active_scout_ids = set()
    for (fname,) in artworks:
        parts = fname.split('_')
        if len(parts) >= 2 and parts[1].isdigit():
            active_scout_ids.add(int(parts[1]))

    orphans_deleted = 0
    for item in approved_items:
        if item.id not in active_scout_ids:
            db.delete(item)
            orphans_deleted += 1

    db.commit()
    return {"status": f"Successfully cleared {orphans_deleted} orphaned approvals"}

@app.delete("/api/discover/clear-pending")
async def clear_pending_discoveries(db: Session = Depends(get_db)):
    """Deletes all pending items from the discovery queue. Useful for fresh test runs."""
    result = db.execute(delete(DiscoveryQueueModel).where(DiscoveryQueueModel.status == 'pending'))
    db.commit()
    # Clear any active search sessions
    from scout import _search_sessions
    _search_sessions.clear()
    return {"status": f"Cleared {result.rowcount} pending discoveries"}

@app.post("/api/admin/factory-reset")
async def factory_reset(db: Session = Depends(get_db)):
    """
    Resets the app to factory state:
    - Keeps only seed artworks (is_seed=True)
    - Removes all non-seed artworks from DB and disk
    - Clears entire discovery queue (all statuses)
    - Clears playlist-artwork associations for deleted art
    - Clears search sessions
    """

    # 1. Delete all discovery queue items (all statuses)
    discover_count = db.execute(delete(DiscoveryQueueModel)).rowcount

    # 2. Get non-seed artworks to delete their files
    non_seed_art = db.query(ArtworkModel).filter(ArtworkModel.is_seed != True).all()
    files_deleted = 0
    for art in non_seed_art:
        filepath = LIBRARY_DIR / art.filename
        # Handle both real files and symlinks
        if filepath.is_symlink() or filepath.exists():
            try:
                filepath.unlink()
                files_deleted += 1
            except Exception as e:
                logger.warning(f"[Factory Reset] Could not delete {filepath}: {e}")
        # Also clean up any playlist symlinks pointing to this file
        for pl_dir in ARTWORK_ROOT.iterdir():
            if pl_dir.is_dir() and pl_dir.name != '_Library':
                pl_link = pl_dir / art.filename
                if pl_link.is_symlink() or pl_link.exists():
                    try: pl_link.unlink()
                    except Exception: pass

    # 3. Remove ALL artwork-playlist associations (both seed and non-seed)
    db.execute(playlist_artwork.delete())

    # 4. Delete non-seed artwork records from DB
    art_count = db.query(ArtworkModel).filter(ArtworkModel.is_seed != True).delete(synchronize_session='fetch')

    # 5. Delete seed artworks too so bootstrapper re-downloads on next start
    seed_art = db.query(ArtworkModel).filter(ArtworkModel.is_seed == True).all()
    for art in seed_art:
        filepath = LIBRARY_DIR / art.filename
        if filepath.is_symlink() or filepath.exists():
            try: filepath.unlink()
            except Exception: pass
        # Clean playlist symlinks for seed art too
        for pl_dir in ARTWORK_ROOT.iterdir():
            if pl_dir.is_dir() and pl_dir.name != '_Library':
                pl_link = pl_dir / art.filename
                if pl_link.is_symlink() or pl_link.exists():
                    try: pl_link.unlink()
                    except Exception: pass
    seed_count = db.query(ArtworkModel).filter(ArtworkModel.is_seed == True).delete(synchronize_session='fetch')

    # 6. Clear search sessions
    from scout import _search_sessions
    _search_sessions.clear()

    db.commit()

    logger.info(f"[Factory Reset] Removed {art_count} + {seed_count} seed artworks, {files_deleted} files, {discover_count} queue items. Seeds will re-download on restart.")
    return {
        "status": "Factory reset complete. Restart the server to re-seed masterpieces.",
        "artworks_removed": art_count,
        "seed_artworks_removed": seed_count,
        "files_deleted": files_deleted,
        "queue_items_cleared": discover_count,
    }

@app.get("/artworks/{artwork_id}/thumbnail")
async def get_artwork_thumbnail(artwork_id: int, db: Session = Depends(get_db)):
    art = db.query(ArtworkModel).filter(ArtworkModel.id == artwork_id).first()
    if not art: raise HTTPException(404)
    path = LIBRARY_DIR / art.filename
    return Response(content=get_optimized_image(path, (400, 400), quality=70), media_type="image/jpeg")

@app.get("/artworks/{artwork_id}/preview")
async def get_artwork_preview(artwork_id: int, db: Session = Depends(get_db)):
    art = db.query(ArtworkModel).filter(ArtworkModel.id == artwork_id).first()
    if not art: raise HTTPException(404)
    path = LIBRARY_DIR / art.filename
    return Response(content=get_optimized_image(path, (1920, 1080), quality=85), media_type="image/jpeg")

@app.get("/art/{artwork_id}", response_class=HTMLResponse)
async def artwork_detail_page(artwork_id: int, db: Session = Depends(get_db)):
    """Server-hosted 'Learn More' page the placard QR points at — works offline (no Google hand-off)."""
    art = db.query(ArtworkModel).filter(ArtworkModel.id == artwork_id).first()
    if not art:
        return HTMLResponse("<body style='font-family:sans-serif;background:#0b1020;color:#e2e8f0;padding:40px'>"
                            "<h1>Artwork not found</h1></body>", status_code=404)
    e = html.escape
    if art.is_personal:
        # Personal photo: caption + optional date only — no artist/medium/culture/tags/source jargon.
        title = e(art.title or "My Photo")
        artist_line = e(art.date_display or art.creation_date or "")
        meta_bits = desc = tag_html = source = ""
    else:
        title = e(art.title or "Untitled")
        role = e(art.agent_role) if art.agent_role and art.agent_role != "Artist" else ""
        date = e(art.date_display or art.creation_date or "")
        artist_line = e(art.agent_name or "Unknown artist") + (f" · {role}" if role else "") + (f" · {date}" if date else "")
        meta_bits = " · ".join(b for b in [e(art.cultural_context or ""), e(art.medium or "")] if b)
        desc = e(art.description_narrative or "")
        tag_html = "".join(f"<span class=tag>{e(t.strip())}</span>" for t in (art.tags or "").split(",") if t.strip())
        source = (f"<a class=source href='{e(art.source_url)}' target=_blank rel=noopener>View original source ↗</a>"
                  if art.source_url else "")
    return HTMLResponse(f"""<!DOCTYPE html><html lang=en><head>
<meta charset=UTF-8><meta name=viewport content="width=device-width, initial-scale=1">
<title>{title} — Screen Docent</title><style>
 :root {{ color-scheme: dark; }}
 body {{ margin:0; background:#0b1020; color:#e2e8f0; font-family:'Inter',system-ui,-apple-system,sans-serif; line-height:1.6; }}
 .wrap {{ max-width:760px; margin:0 auto; padding:24px 20px 60px; }}
 img.hero {{ width:100%; border-radius:14px; background:#0f172a; display:block; margin-bottom:24px; box-shadow:0 10px 40px rgba(0,0,0,.5); }}
 h1 {{ font-size:1.8rem; margin:0 0 6px; }}
 .artist {{ font-size:1.1rem; color:#cbd5e1; margin:0 0 4px; }}
 .meta {{ color:#94a3b8; font-size:.92rem; margin:0 0 20px; }}
 .desc {{ font-size:1.02rem; }}
 .tags {{ margin-top:22px; display:flex; flex-wrap:wrap; gap:8px; }}
 .tag {{ background:#1e293b; border:1px solid #334155; color:#cbd5e1; padding:4px 12px; border-radius:20px; font-size:.8rem; }}
 .source {{ display:inline-block; margin-top:24px; color:#3b82f6; text-decoration:none; }}
 .brand {{ margin-top:40px; color:#475569; font-size:.78rem; display:flex; align-items:center; gap:8px; }}
 .brand img {{ height:20px; opacity:.7; }}
</style></head><body><div class=wrap>
 <img class=hero src="/artworks/{art.id}/preview" alt="{title}">
 <h1>{title}</h1>
 <p class=artist>{artist_line}</p>
 {f'<p class=meta>{meta_bits}</p>' if meta_bits else ''}
 {f'<p class=desc>{desc}</p>' if desc else ''}
 {f'<div class=tags>{tag_html}</div>' if tag_html else ''}
 {source}
 <div class=brand><img src="/logo.svg" alt=""> Presented by Screen Docent</div>
</div></body></html>""")

class CropPayload(BaseModel):
    crop_x: float = 0.0
    crop_y: float = 0.0
    crop_width: float = 0.0
    crop_height: float = 0.0
    focal_x: Optional[float] = None
    focal_y: Optional[float] = None

@app.patch("/artworks/{artwork_id}/crop", response_model=ArtworkSchema)
async def update_artwork_crop(artwork_id: int, payload: CropPayload, db: Session = Depends(get_db)):
    """Persist a manual crop rectangle (original pixels) from the admin Cropper, plus optionally the
    normalized focal point (the Ken Burns / e-ink framing anchor). The admin crop modal calls this;
    it was previously missing, so manual crop-saves silently failed."""
    art = db.query(ArtworkModel).filter(ArtworkModel.id == artwork_id).first()
    if not art:
        raise HTTPException(404)
    art.crop_x = payload.crop_x
    art.crop_y = payload.crop_y
    art.crop_width = payload.crop_width
    art.crop_height = payload.crop_height
    if payload.focal_x is not None:
        art.focal_x = min(1.0, max(0.0, payload.focal_x))
    if payload.focal_y is not None:
        art.focal_y = min(1.0, max(0.0, payload.focal_y))
    db.commit(); db.refresh(art)
    return art

@app.delete("/artworks/{artwork_id}")
async def permanent_delete_artwork(artwork_id: int, db: Session = Depends(get_db)):
    art = db.query(ArtworkModel).filter(ArtworkModel.id == artwork_id).first()
    if not art: raise HTTPException(404)
    f_path = LIBRARY_DIR / art.filename
    if f_path.exists(): f_path.unlink()
    db.delete(art); db.commit(); return {"status": "wiped"}

@app.get("/next-image")
async def get_next_image(
    playlist_name: str,
    shuffle: Optional[bool] = Query(None),
    display_id: str = Query("default"),
    direction: int = Query(1),
    db: Session = Depends(get_db)
):
    """
    Phase 6: Stateful next-image selection.
    Uses 'bag shuffle' for variety and persists state per display.
    """
    p = db.query(PlaylistModel).filter(PlaylistModel.name == playlist_name).first()
    if not p: raise HTTPException(404)

    # Resolve Shuffle Hierarchy (URL override > Playlist setting)
    resolved_shuffle = shuffle if shuffle is not None else p.shuffle

    # Fetch all approved artworks in this playlist
    artworks = db.query(ArtworkModel).join(playlist_artwork).filter(
        playlist_artwork.c.playlist_id == p.id,
        ArtworkModel.status == 'approved'
    ).order_by(playlist_artwork.c.display_order).all()

    if not artworks: raise HTTPException(404, detail="No approved images")
    count = len(artworks)

    # Get or create playback session
    session = db.query(DisplayPlaybackSessionModel).filter(
        DisplayPlaybackSessionModel.display_id == display_id,
        DisplayPlaybackSessionModel.playlist_id == p.id
    ).first()

    if not session:
        session = DisplayPlaybackSessionModel(display_id=display_id, playlist_id=p.id)
        db.add(session)
        db.commit()

    selected_art = None
    selected_idx = -1

    if resolved_shuffle:
        # Bag Shuffle Logic
        unplayed_ids = json.loads(session.unplayed_artworks_json)

        # Valid approved IDs in this playlist
        valid_ids = [a.id for a in artworks]

        # Filter unplayed to only include currently valid/approved IDs
        bag = [aid for aid in unplayed_ids if aid in valid_ids]

        # If bag is empty, refill it
        if not bag:
            bag = valid_ids
            logger.info(f"[Director] Refilling bag for display '{display_id}' / playlist '{playlist_name}'")

        # Phase 6 Bonus: Weighted random draw based on affinity_score
        # Get actual artwork objects for the IDs in the bag to access affinity scores
        bag_artworks = [a for a in artworks if a.id in bag]

        if bag_artworks:
            # random.choices uses weights. affinity_score defaults to 1.0.
            weights = [max(0.1, a.affinity_score) for a in bag_artworks]
            selected_art = random.choices(bag_artworks, weights=weights, k=1)[0]

            # Remove from bag
            bag.remove(selected_art.id)
            session.unplayed_artworks_json = json.dumps(bag)

            # Find its index in the ordered list for the frontend (optional but helpful)
            for i, a in enumerate(artworks):
                if a.id == selected_art.id:
                    selected_idx = i
                    break
    else:
        # Stateful Sequential Logic
        base_idx = session.last_sequential_index
        selected_idx = (base_idx + direction) % count
        selected_art = artworks[selected_idx]
        session.last_sequential_index = selected_idx

    db.commit()

    return {
        "index": selected_idx,
        "image_url": f"/media/_Library/{quote(selected_art.filename)}",
        "playlist": playlist_name,
        "display_time": p.display_time,
        "default_mode": p.default_mode,
        "shuffle": resolved_shuffle,
        "placard_wait": p.placard_initial_wait_sec,
        "placard_show": p.placard_initial_show_sec,
        "placard_manual": p.placard_interaction_show_sec,
        "crop": {"x": selected_art.crop_x, "y": selected_art.crop_y, "width": selected_art.crop_width, "height": selected_art.crop_height},
        "focal_point": {"x": selected_art.focal_x, "y": selected_art.focal_y},
        "metadata": {
            "id": selected_art.id,
            "is_personal": selected_art.is_personal,
            "title": selected_art.title, "agent_name": selected_art.agent_name, "agent_role": selected_art.agent_role,
            "creation_date": selected_art.creation_date, "cultural_context": selected_art.cultural_context,
            "medium": selected_art.medium, "date_display": selected_art.date_display,
            "description": selected_art.description_narrative, "tags": selected_art.tags
        }
    }


def touch_active_display(db: Session, display_id: str):
    """Upsert last_seen_at so pull-on-wake e-ink frames show up in the remote/
    admin just like WebSocket-connected Canvas displays."""
    try:
        d = db.query(ActiveDisplayModel).filter(ActiveDisplayModel.display_id == display_id).first()
        if d:
            d.last_seen_at = datetime.now(UTC)
        else:
            db.add(ActiveDisplayModel(display_id=display_id))
        db.commit()
    except Exception as e:
        logger.error(f"touch_active_display error for {display_id}: {e}")
        db.rollback()


@app.get("/display/{display_id}/current.{ext}")
async def get_display_image(
    display_id: str,
    ext: str,
    playlist: Optional[str] = Query(None),
    w: int = Query(1600, ge=16, le=4096),
    h: int = Query(1200, ge=16, le=4096),
    palette: str = Query("spectra6"),
    fit: str = Query("cover"),
    shuffle: Optional[bool] = Query(None),
    db: Session = Depends(get_db),
):
    """
    Track B: stateless pull-on-wake image for e-ink / BYOS frames.

    Reuses /next-image's selection (advancing the same bag-shuffle), then renders
    the chosen artwork cropped to w x h and Floyd–Steinberg-dithered to the device
    palette. Returns the bytes plus an `X-Refresh-After` header (the playlist's
    display_time) so the frame knows how long to deep-sleep. No WebSocket, no JS.
    """
    ext = ext.lower()
    if ext not in VALID_FORMATS:
        raise HTTPException(404, detail="Use .png or .bmp")
    if palette not in PALETTES:
        raise HTTPException(400, detail=f"Unknown palette. Options: {', '.join(PALETTES)}")

    # Playlist binding is stateless (v1): explicit ?playlist=, else the first one.
    if not playlist:
        first = db.query(PlaylistModel).order_by(PlaylistModel.id).first()
        if not first:
            raise HTTPException(404, detail="No playlists exist")
        playlist = first.name

    # Reuse the canonical selection brain (advances state once per fetch).
    info = await get_next_image(
        playlist_name=playlist, shuffle=shuffle, display_id=display_id, direction=1, db=db
    )

    art = db.query(ArtworkModel).filter(ArtworkModel.id == info["metadata"]["id"]).first()
    if not art:
        raise HTTPException(404, detail="Selected artwork not found")
    path = LIBRARY_DIR / art.filename
    if not path.exists():
        raise HTTPException(404, detail="Artwork file missing")

    try:
        data = render_for_epaper(path, w, h, palette=palette, fit=fit,
                                 focal=(art.focal_x, art.focal_y), fmt=ext)
    except Exception as e:
        logger.error(f"[epaper] render failed for {path.name}: {e}", exc_info=True)
        raise HTTPException(500, detail="Render failed")

    touch_active_display(db, display_id)

    return Response(
        content=data,
        media_type=media_type_for(ext),
        headers={
            "X-Refresh-After": str(info["display_time"]),
            "Cache-Control": "no-store, no-cache, must-revalidate",
        },
    )

# -----------------------------------------------------------------------------
# 4. WebSocket & Remote Control
# -----------------------------------------------------------------------------
@app.get("/remote")
async def get_remote_page():
    return FileResponse(STATIC_DIR / "remote.html")

@app.get("/api/remote/displays")
async def get_active_displays(db: Session = Depends(get_db)):
    """Returns a list of all display IDs currently connected via WebSocket across all workers."""
    # Consider a display active if seen in the last 15 seconds
    cutoff = datetime.now(UTC) - timedelta(seconds=15)
    displays = db.query(ActiveDisplayModel).filter(ActiveDisplayModel.last_seen_at > cutoff).all()
    return [d.display_id for d in displays]

@app.post("/api/remote/change")
async def remote_change_playlist(request: RemoteChangeRequest, db: Session = Depends(get_db)):
    """Targeted command to change a playlist, mode, or trigger navigation on a specific display."""
    logger.info(f"Targeted Remote Command: {request.target_display} -> {request.action}")

    payload = {"action": request.action}
    if request.playlist:
        payload["playlist"] = request.playlist
    if request.mode:
        payload["mode"] = request.mode

    # Phase 5: Persist command to DB to bridge across worker processes
    cmd = RemoteCommandModel(
        target_display=request.target_display,
        action=request.action,
        payload=json.dumps(payload)
    )
    db.add(cmd)
    db.commit()

    return {"status": "command_queued"}

@app.websocket("/ws/{display_id}")
async def websocket_endpoint(websocket: WebSocket, display_id: str):
    """Handles targeted display connections with multi-worker synchronization."""
    await manager.connect(websocket, display_id)

    async def heartbeat():
        """Updates the active_displays table to signify this display is alive on this worker."""
        while True:
            try:
                with SessionLocal() as db:
                    display = db.query(ActiveDisplayModel).filter(ActiveDisplayModel.display_id == display_id).first()
                    if display:
                        display.last_seen_at = datetime.now(UTC)
                    else:
                        display = ActiveDisplayModel(display_id=display_id)
                        db.add(display)
                    db.commit()
            except Exception as e:
                logger.error(f"Heartbeat error for {display_id}: {e}")
            await asyncio.sleep(5)

    async def command_poller():
        """Polls the remote_commands table for actions targeting this specific display."""
        while True:
            try:
                with SessionLocal() as db:
                    cmds = db.query(RemoteCommandModel).filter(RemoteCommandModel.target_display == display_id).all()
                    for cmd in cmds:
                        logger.info(f"Relaying remote command to {display_id}: {cmd.action}")
                        await manager.send_personal_message(json.loads(cmd.payload), display_id)
                        db.delete(cmd)
                    db.commit()
            except Exception as e:
                logger.error(f"Command poller error for {display_id}: {e}")
            await asyncio.sleep(1)

    # Start sync workers
    heartbeat_task = asyncio.create_task(heartbeat())
    poller_task = asyncio.create_task(command_poller())

    try:
        while True:
            # We mostly broadcast from the API, but remotes can still talk directly here if needed
            data = await websocket.receive_json()
            await manager.broadcast(data)
    except WebSocketDisconnect:
        manager.disconnect(websocket, display_id)
    except Exception as e:
        logger.error(f"WebSocket error on '{display_id}': {e}")
        manager.disconnect(websocket, display_id)
    finally:
        heartbeat_task.cancel()
        poller_task.cancel()
        # Clean up heartbeat from DB immediately on clean disconnect
        with SessionLocal() as db:
            db.query(ActiveDisplayModel).filter(ActiveDisplayModel.display_id == display_id).delete()
            db.commit()

# -----------------------------------------------------------------------------
# 4.5 Settings (API Keys)
# -----------------------------------------------------------------------------
@app.get("/api/settings/keys")
async def get_api_keys(db: Session = Depends(get_db)):
    """Returns a map of which API keys are unlocked."""
    settings = db.query(SettingsModel).all()
    # Check for presence of keys
    return {
        "harvard": any(s.setting_key == "harvard_api_key" for s in settings),
        "smithsonian": any(s.setting_key == "smithsonian_api_key" for s in settings),
        "europeana": any(s.setting_key == "europeana_api_key" for s in settings)
    }

class TelemetryHeartbeat(BaseModel):
    artwork_id: int
    display_time_sec: int
    skipped: bool

@app.post("/api/telemetry/heartbeat")
def record_telemetry(payload: TelemetryHeartbeat, db: Session = Depends(get_db)):
    """
    Phase 6: Ingests display metrics from Canvas clients.
    """
    artwork = db.query(ArtworkModel).filter(ArtworkModel.id == payload.artwork_id).first()
    if not artwork:
        raise HTTPException(status_code=404, detail="Artwork not found")

    # Update raw telemetry
    artwork.total_display_time += payload.display_time_sec
    if payload.skipped:
        artwork.skip_count += 1

    # Phase 6 Director Affinity Calculation (v1)
    # This is a naive calculation that will be evolved.
    # Base is 1.0.
    # Skipping heavily penalizes (-0.1).
    # Natural display slightly rewards (+0.05 per 30s).
    if payload.skipped:
        artwork.affinity_score = max(0.1, artwork.affinity_score - 0.1)
    else:
        intervals = payload.display_time_sec / 30.0
        artwork.affinity_score = min(5.0, artwork.affinity_score + (0.05 * intervals))

    db.commit()
    return {"status": "ok", "affinity": artwork.affinity_score}

@app.post("/api/settings/keys/{source}")
async def verify_and_save_api_key(source: str, payload: dict, db: Session = Depends(get_db)):
    """Validates an API key against the source museum backend and persists it."""
    key = payload.get("api_key")
    if not key: raise HTTPException(400, "api_key payload is required.")

    try:
        async with httpx.AsyncClient() as client:
            if source == "harvard":
                resp = await client.get(f"https://api.harvardartmuseums.org/object?apikey={key}&size=1", timeout=15)
                if resp.status_code != 200: raise Exception("Harvard API rejected the key.")
                db_key = "harvard_api_key"
            elif source == "smithsonian":
                resp = await client.get(f"https://api.si.edu/openaccess/api/v1.0/search?q=art&api_key={key}&rows=1", timeout=15)
                if resp.status_code != 200: raise Exception("Smithsonian API rejected the key.")
                db_key = "smithsonian_api_key"
            elif source == "europeana":
                resp = await client.get(f"https://api.europeana.eu/record/v2/search.json?wskey={key}&query=*&rows=1", timeout=15)
                if resp.status_code != 200: raise Exception("Europeana API rejected the key.")
                db_key = "europeana_api_key"
            else:
                raise HTTPException(400, f"Unsupported museum target: {source}")
    except Exception as e:
        raise HTTPException(401, detail=f"Validation Failed: {str(e)}")

    setting = db.query(SettingsModel).filter(SettingsModel.setting_key == db_key).first()
    if setting:
        setting.setting_value = key
    else:
        setting = SettingsModel(setting_key=db_key, setting_value=key)
        db.add(setting)
    db.commit()
    return {"status": "success", "source": source}

# -----------------------------------------------------------------------------
# 4.6 AI Engine (model provider configuration)
# -----------------------------------------------------------------------------
def _upsert_setting(db: Session, key: str, value: str):
    row = db.query(SettingsModel).filter(SettingsModel.setting_key == key).first()
    if row:
        row.setting_value = value
    else:
        db.add(SettingsModel(setting_key=key, setting_value=value))

@app.get("/api/settings/ai")
async def get_ai_settings(db: Session = Depends(get_db)):
    """Returns the current AI engine config (never the raw key) + provider presets for the UI."""
    rows = {
        s.setting_key: s.setting_value
        for s in db.query(SettingsModel)
        .filter(SettingsModel.setting_key.in_(ai_client.AI_SETTING_KEYS))
        .all()
    }
    cfg = ai_client.get_ai_config(force=True)
    return {
        "provider": rows.get("ai_provider", ai_client.DEFAULT_PROVIDER),
        "base_url": rows.get("ai_base_url", ""),
        "model": rows.get("ai_model", ""),
        "model_fast": rows.get("ai_model_fast", ""),
        "temperature": rows.get("ai_temperature", ""),
        "has_key": cfg["configured"],
        "key_source": "db" if rows.get("ai_api_key") else ("env" if cfg["configured"] else "none"),
        "model_is_local": ai_client.is_local_base_url(cfg["base_url"]),
        "presets": {
            k: {
                "label": v["label"],
                "base_url": v["base_url"],
                "models": v["models"],
                "oauth": v.get("oauth", False),
                "key_optional": v.get("key_optional", False),
                "key_url": v.get("key_url", ""),
            }
            for k, v in ai_client.PRESETS.items()
        },
    }

class AISettingsPayload(BaseModel):
    model_config = {"protected_namespaces": ()}  # allow "model"/"model_fast" field names
    provider: str
    base_url: Optional[str] = ""
    api_key: Optional[str] = None  # blank/omitted ⇒ keep the existing stored key
    model: str
    model_fast: Optional[str] = ""
    temperature: Optional[str] = ""

@app.post("/api/settings/ai")
async def save_ai_settings(payload: AISettingsPayload, db: Session = Depends(get_db)):
    """Validates a candidate AI config against the live endpoint, then persists it."""
    provider = payload.provider
    if provider not in ai_client.PRESETS:
        raise HTTPException(400, f"Unknown provider: {provider}")
    if not payload.model:
        raise HTTPException(400, "A model name is required.")

    base_url = (payload.base_url or ai_client.PRESETS[provider]["base_url"]).rstrip("/")
    existing = db.query(SettingsModel).filter(SettingsModel.setting_key == "ai_api_key").first()
    api_key = (
        (payload.api_key or "").strip()
        or (existing.setting_value if existing else "")
        or os.getenv("GEMINI_API_KEY", "")
    )
    key_optional = ai_client.PRESETS[provider].get("key_optional", False)
    if not api_key and not key_optional:
        raise HTTPException(400, "An API key is required for this provider.")

    # Validate against the live endpoint before persisting (mirrors the museum-key flow).
    try:
        await asyncio.to_thread(ai_client.validate_config, provider, base_url, api_key, payload.model)
    except Exception as e:
        raise HTTPException(401, detail=f"Validation failed: {str(e)}")

    _upsert_setting(db, "ai_provider", provider)
    _upsert_setting(db, "ai_base_url", base_url)
    if api_key:
        _upsert_setting(db, "ai_api_key", api_key)
    _upsert_setting(db, "ai_model", payload.model)
    _upsert_setting(db, "ai_model_fast", (payload.model_fast or "").strip())
    _upsert_setting(db, "ai_temperature", (payload.temperature or "").strip())
    db.commit()
    ai_client.invalidate_config_cache()
    return {"status": "success", "provider": provider, "model": payload.model}

@app.get("/api/settings/ai/oauth/start")
async def ai_oauth_start(callback_url: str, challenge: str):
    """Assembles the OpenRouter authorization URL (PKCE). The client holds the code_verifier."""
    from urllib.parse import urlencode
    params = urlencode({
        "callback_url": callback_url,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    })
    return {"auth_url": f"https://openrouter.ai/auth?{params}"}

class OAuthExchangePayload(BaseModel):
    code: str
    verifier: str

@app.post("/api/settings/ai/oauth/exchange")
async def ai_oauth_exchange(payload: OAuthExchangePayload, db: Session = Depends(get_db)):
    """Exchanges an OpenRouter auth code (+ PKCE verifier) for an API key and saves it."""
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                "https://openrouter.ai/api/v1/auth/keys",
                json={
                    "code": payload.code,
                    "code_verifier": payload.verifier,
                    "code_challenge_method": "S256",
                },
                timeout=20,
            )
        if resp.status_code != 200:
            raise Exception(resp.text[:200])
        key = resp.json().get("key")
        if not key:
            raise Exception("No key returned by OpenRouter.")
    except Exception as e:
        raise HTTPException(401, detail=f"OAuth exchange failed: {str(e)}")

    provider = "openrouter"
    _upsert_setting(db, "ai_provider", provider)
    _upsert_setting(db, "ai_base_url", ai_client.PRESETS[provider]["base_url"])
    _upsert_setting(db, "ai_api_key", key)
    if not db.query(SettingsModel).filter(SettingsModel.setting_key == "ai_model").first():
        _upsert_setting(db, "ai_model", ai_client.PRESETS[provider]["models"][0])
    db.commit()
    ai_client.invalidate_config_cache()
    return {"status": "success", "provider": provider}

# -----------------------------------------------------------------------------
# 4.7 Catalog (browseable curated public-domain art; lazy high-res on add)
# -----------------------------------------------------------------------------
# Split manifest produced by tools/build_catalog.py: an index.json (collection summaries) plus one
# <collection_id>.json per collection (the items). Lets the catalog scale to thousands and the UI
# load a collection's thumbnails only when opened.
CATALOG_DIR = Path("static/catalog")
# SD_USER_AGENT (the descriptive UA Wikimedia/museums require) now lives in config.py so the
# offline tools/ scripts can reuse it without importing this app.

def _read_local_json(path: Path):
    if not path.exists():
        return None
    with open(path) as f:
        return json.load(f)

async def _catalog_remote_base(db: Session) -> Optional[str]:
    """Optional remote override: a static base URL hosting index.json + <id>.json (no server needed)."""
    setting = db.query(SettingsModel).filter(SettingsModel.setting_key == "catalog_url").first()
    return setting.setting_value.rstrip("/") if setting and setting.setting_value else None

async def _fetch_remote_json(base: str, name: str):
    async with httpx.AsyncClient(headers={"User-Agent": SD_USER_AGENT}) as client:
        r = await client.get(f"{base}/{name}", timeout=15.0, follow_redirects=True)
        if r.status_code == 200:
            return r.json()
    raise RuntimeError(f"HTTP {r.status_code}")

# Subscribed (federated) collections share the catalog browse surface, but their ids are namespaced
# so they can never collide with — or masquerade as — a bundled/official collection.
SUB_PREFIX = "sub_"


def _subscribed_summaries(db: Session) -> list:
    """Index summaries for enabled subscriptions, each stamped with its origin/trust/publisher."""
    out = []
    for sub in db.query(SubscriptionModel).filter(SubscriptionModel.enabled.is_(True)).all():
        if not sub.cached_manifest:
            continue
        try:
            m = json.loads(sub.cached_manifest)
        except ValueError:
            continue
        items = m.get("items", [])
        cover = ""
        if items:
            img = items[0].get("image") or {}
            cover = img.get("thumbnail_url") or img.get("full_url") or ""
        out.append({
            "id": f"{SUB_PREFIX}{sub.id}",
            "title": m.get("title") or sub.title or "Untitled",
            "description": m.get("description", ""),
            "source": sub.publisher_name or "",
            "license": "",  # per-item; mixed
            "count": len(items),
            "cover_thumbnail": cover,
            "origin": "subscription",
            "trust": sub.trust,
            "publisher": {"id": sub.publisher_id, "name": sub.publisher_name, "url": sub.publisher_url},
        })
    return out


def _subscribed_collection(db: Session, collection_id: str):
    """Resolve a `sub_<id>` collection to its cached manifest's items (mapped to the catalog shape)."""
    try:
        sub_id = int(collection_id[len(SUB_PREFIX):])
    except ValueError:
        return None
    sub = db.query(SubscriptionModel).filter(
        SubscriptionModel.id == sub_id, SubscriptionModel.enabled.is_(True)).first()
    if not sub or not sub.cached_manifest:
        return None
    m = json.loads(sub.cached_manifest)
    return {
        "id": collection_id,
        "title": m.get("title"),
        "description": m.get("description", ""),
        "source": sub.publisher_name or "",
        "license": "",
        "origin": "subscription",
        "trust": sub.trust,
        "items": [federation.manifest_item_to_catalog(it) for it in m.get("items", [])],
    }


async def _catalog_index(db: Session) -> dict:
    """Collection summaries: optional remote override → bundled split files, then federated
    subscriptions appended. Bundled/remote collections are stamped origin='bundled' so the UI can
    distinguish official from subscribed."""
    index = None
    base = await _catalog_remote_base(db)
    if base:
        try:
            index = await _fetch_remote_json(base, "index.json")
        except Exception as e:
            logger.warning(f"[Catalog] remote index fetch failed ({e}); using bundled.")
    if index is None:
        index = _read_local_json(CATALOG_DIR / "index.json") or {"version": 1, "collections": []}
    for c in index.get("collections", []):
        c.setdefault("origin", "bundled")
    index.setdefault("collections", []).extend(_subscribed_summaries(db))
    return index

async def _catalog_collection(db: Session, collection_id: str):
    """One collection's full items file, or None if the id isn't present in the index."""
    if collection_id.startswith(SUB_PREFIX):
        return _subscribed_collection(db, collection_id)
    index = await _catalog_index(db)
    if not any(c.get("id") == collection_id for c in index.get("collections", [])):
        return None
    col = None
    base = await _catalog_remote_base(db)
    if base:
        try:
            col = await _fetch_remote_json(base, f"{collection_id}.json")
        except Exception as e:
            logger.warning(f"[Catalog] remote collection fetch failed ({e}); using bundled.")
    if col is None:
        col = _read_local_json(CATALOG_DIR / f"{collection_id}.json")
    if isinstance(col, dict):
        col.setdefault("origin", "bundled")
    return col

async def _download_image_to_library(source_url: str, *, filename: str,
                                     retries: int = 3) -> tuple[Path, str, int, int]:
    """Robustly download a remote image into LIBRARY_DIR — the one downloader the seed, discovery,
    and catalog paths share. Sends a descriptive User-Agent (the default httpx UA is rejected by
    Wikimedia and others), follows redirects, retries 429s with escalating backoff, writes to a
    collision-safe unique filename, and validates the bytes are a real image (a bad download is
    deleted, never left in the library). Returns (dest_path, safe_filename, width, height); raises
    HTTPException on download or validation failure."""
    safe_name = "".join(x for x in filename if x.isalnum() or x in "_-.")
    if not safe_name.lower().endswith(".jpg"):
        safe_name += ".jpg"
    stem = safe_name[:-4]
    dest_path = LIBRARY_DIR / safe_name
    n = 1
    while dest_path.exists():
        safe_name = f"{stem}_{n}.jpg"; dest_path = LIBRARY_DIR / safe_name; n += 1

    resp = None
    async with httpx.AsyncClient(headers={"User-Agent": SD_USER_AGENT}) as client:
        for attempt in range(retries):
            resp = await client.get(source_url, timeout=45.0, follow_redirects=True)
            if resp.status_code == 429:
                await asyncio.sleep(3 * (attempt + 1))
                continue
            break
    if not resp or resp.status_code != 200:
        raise HTTPException(502, detail=f"Could not download image (HTTP {resp.status_code if resp else 'none'}).")

    with open(dest_path, "wb") as f:
        f.write(resp.content)
    try:
        with Image.open(dest_path) as img:
            w, h = img.size
    except Exception:
        dest_path.unlink(missing_ok=True)
        raise HTTPException(502, detail="Downloaded file was not a valid image.")
    return dest_path, safe_name, w, h


def _focal_xy(item: dict, default: tuple = (0.5, 0.5)) -> tuple:
    """Parse a flat 'focal_point': [x, y] (normalized 0..1) from a catalog/seed item — the Ken Burns
    / crop framing anchor baked by tools/backfill_focal_*. Absent or malformed ⇒ centered default."""
    fp = item.get("focal_point")
    if isinstance(fp, (list, tuple)) and len(fp) == 2:
        try:
            return min(1.0, max(0.0, float(fp[0]))), min(1.0, max(0.0, float(fp[1])))
        except (TypeError, ValueError):
            pass
    return default


async def _download_and_create_artwork(db: Session, *, source_url: str, thumbnail_url: str,
                                       metadata: dict, playlist_id: Optional[int] = None,
                                       filename_prefix: str = "catalog") -> ArtworkModel:
    """Download a remote image once and create an *approved* ArtworkModel with prefilled metadata.
    Uses the shared robust downloader (UA + 429 backoff + validation), then optionally links the
    artwork into a playlist. Dedups on source_url — returns the existing row if already added."""
    existing = db.query(ArtworkModel).filter(ArtworkModel.source_url == source_url).first()
    if existing:
        return existing

    title = (metadata.get("title") or "art")
    filename = f"{filename_prefix}_{title.replace(' ', '_').lower()[:18]}"
    dest_path, safe_name, w, h = await _download_image_to_library(source_url, filename=filename)

    fx, fy = _focal_xy(metadata)   # baked catalog/manifest focal_point [x, y] (normalized); else centered

    artwork = ArtworkModel(
        filename=safe_name, original_width=w, original_height=h,
        crop_width=float(w), crop_height=float(h),
        focal_x=fx, focal_y=fy,
        status='approved',
        title=metadata.get("title"), agent_name=metadata.get("agent_name"),
        agent_role=metadata.get("agent_role", "Artist"), creation_date=metadata.get("creation_date"),
        cultural_context=metadata.get("cultural_context"), medium=metadata.get("medium"),
        date_display=metadata.get("date_display"), description_narrative=metadata.get("description_narrative"),
        tags=metadata.get("tags"), source_url=source_url, thumbnail_url=thumbnail_url, is_seed=False,
    )
    db.add(artwork); db.commit(); db.refresh(artwork)

    if playlist_id:
        playlist = db.query(PlaylistModel).filter(PlaylistModel.id == playlist_id).first()
        if playlist:
            try:
                (ARTWORK_ROOT / playlist.name).mkdir(parents=True, exist_ok=True)
                pl_path = ARTWORK_ROOT / playlist.name / safe_name
                if pl_path.is_symlink() or pl_path.exists():
                    pl_path.unlink()
                try: os.symlink(dest_path.resolve(), pl_path)
                except OSError: shutil.copy(dest_path, pl_path)
            except Exception as e:
                logger.warning(f"[Catalog] playlist symlink failed: {e}")
            order = len(db.execute(select(playlist_artwork.c.artwork_id).where(
                playlist_artwork.c.playlist_id == playlist.id)).all())
            try:
                db.execute(playlist_artwork.insert().values(
                    playlist_id=playlist.id, artwork_id=artwork.id, display_order=order))
                db.commit()
            except Exception:
                db.rollback()
    return artwork

# ---------------------------------------------------------------------------
# §4.8 Samsung Frame TV push (Integrations)
# ---------------------------------------------------------------------------

async def _frame_select(playlist: str):
    """Selector injected into the Frame pusher: pick the current artwork for a playlist (reusing the
    bag-shuffle/affinity in get_next_image, on a dedicated display_id) and return (file_path, id)."""
    db = SessionLocal()
    try:
        pl = playlist
        if not pl:
            first = db.query(PlaylistModel).order_by(PlaylistModel.id).first()
            if not first:
                return None
            pl = first.name
        cfg = frame_push.get_frame_config()
        info = await get_next_image(
            playlist_name=pl, shuffle=None, display_id=cfg["display_id"], direction=1, db=db
        )
        art_id = (info.get("metadata") or {}).get("id")
        if not art_id:
            return None
        art = db.query(ArtworkModel).filter(ArtworkModel.id == art_id).first()
        if not art:
            return None
        return (LIBRARY_DIR / art.filename, art_id, (art.focal_x, art.focal_y))
    except Exception as e:
        logger.warning(f"[Frame] selection failed: {e}")
        return None
    finally:
        db.close()


@app.get("/api/settings/frame")
async def get_frame_settings(db: Session = Depends(get_db)):
    """Current Frame TV config (+ last-push status) for the Settings panel."""
    cfg = frame_push.get_frame_config(force=True)
    return {
        "enabled": cfg["enabled"],
        "host": cfg["host"],
        "port": cfg["port"],
        "playlist": cfg["playlist"],
        "interval_sec": cfg["interval_sec"],
        "width": cfg["width"],
        "height": cfg["height"],
        "matte": cfg["matte"],
        "last_artwork_id": cfg["last_artwork_id"],
        "last_push_at": cfg["last_push_at"],
    }


class FrameSettingsPayload(BaseModel):
    enabled: bool = False
    host: Optional[str] = ""
    port: Optional[int] = 8001
    playlist: Optional[str] = ""
    interval_sec: Optional[int] = 900
    width: Optional[int] = 3840
    height: Optional[int] = 2160
    matte: Optional[str] = "none"


@app.post("/api/settings/frame")
async def save_frame_settings(payload: FrameSettingsPayload, db: Session = Depends(get_db)):
    """Persist Frame TV settings. Takes effect on the next push cycle (config cache invalidated)."""
    if payload.enabled and not (payload.host or "").strip():
        raise HTTPException(400, "A Frame TV host/IP is required to enable pushing.")
    _upsert_setting(db, "frame_enabled", "true" if payload.enabled else "false")
    _upsert_setting(db, "frame_host", (payload.host or "").strip())
    _upsert_setting(db, "frame_port", str(payload.port or 8001))
    _upsert_setting(db, "frame_playlist", (payload.playlist or "").strip())
    _upsert_setting(db, "frame_interval_sec", str(max(60, payload.interval_sec or 900)))
    _upsert_setting(db, "frame_width", str(payload.width or 3840))
    _upsert_setting(db, "frame_height", str(payload.height or 2160))
    _upsert_setting(db, "frame_matte", (payload.matte or "none").strip())
    db.commit()
    frame_push.invalidate_frame_cache()
    return {"status": "success"}


@app.post("/api/settings/frame/test")
async def test_frame_push(db: Session = Depends(get_db)):
    """One-shot 'Test / Push now'. Returns a structured result (never 500s) so the GUI can show a
    clean message with or without a TV present."""
    return await frame_push.run_test_push(_frame_select)


class CatalogSourcePayload(BaseModel):
    catalog_url: Optional[str] = ""


@app.get("/api/settings/catalog")
async def get_catalog_source(db: Session = Depends(get_db)):
    """Current remote catalog base URL (empty ⇒ serving the bundled catalog)."""
    base = await _catalog_remote_base(db)
    return {"catalog_url": base or "", "using_remote": bool(base)}


@app.post("/api/settings/catalog")
async def save_catalog_source(payload: CatalogSourcePayload, db: Session = Depends(get_db)):
    """Set or clear the remote catalog base URL — a static host serving `index.json` + per-collection
    files (no server required). Validation is advisory: we test-fetch `index.json` and report the
    collection count, but still persist a currently-unreachable URL (the runtime fetch falls back to
    bundled on any failure) so the GUI can warn rather than block. An empty value reverts to bundled."""
    url = (payload.catalog_url or "").strip().rstrip("/")
    if not url:
        row = db.query(SettingsModel).filter(SettingsModel.setting_key == "catalog_url").first()
        if row:
            db.delete(row); db.commit()
        return {"status": "success", "catalog_url": "", "using_remote": False,
                "message": "Reverted to the bundled catalog."}
    if not url.startswith(("http://", "https://")):
        raise HTTPException(400, "Catalog URL must start with http:// or https://")

    warning = None
    collections = 0
    try:
        index = await _fetch_remote_json(url, "index.json")
        if not isinstance(index, dict) or "collections" not in index:
            raise HTTPException(400, "Reached the URL, but it doesn't look like a catalog index "
                                     "(no 'collections' key). Point at the base path that serves "
                                     "index.json.")
        collections = len(index.get("collections") or [])
    except HTTPException:
        raise
    except Exception as e:
        warning = (f"Saved, but couldn't reach {url}/index.json right now ({e}). The app will keep "
                   f"using the bundled catalog until it becomes reachable.")

    _upsert_setting(db, "catalog_url", url)
    db.commit()
    result = {"status": "success", "catalog_url": url, "using_remote": True}
    if warning:
        result["warning"] = warning
    else:
        result["message"] = f"Connected — {collections} collection(s) found."
    return result


@app.get("/api/catalog")
async def get_catalog(db: Session = Depends(get_db)):
    """Collection summaries (cover + count) for the Browse Catalog grid. Items load per-collection."""
    return await _catalog_index(db)

@app.get("/api/catalog/{collection_id}")
async def get_catalog_collection(collection_id: str, db: Session = Depends(get_db)):
    """One collection's items — prefilled placard metadata + hotlinked thumbnail_url + an `added`
    flag (matched by source_url). High-res is fetched only on add."""
    col = await _catalog_collection(db, collection_id)
    if not col:
        raise HTTPException(404, detail=f"Unknown collection: {collection_id}")
    added = {row[0] for row in db.query(ArtworkModel.source_url).filter(ArtworkModel.source_url.isnot(None)).all()}
    for it in col.get("items", []):
        it["added"] = it.get("source_url") in added
    return col

class CatalogAddPayload(BaseModel):
    collection_id: str
    item_index: int
    playlist_id: Optional[int] = None

@app.post("/api/catalog/add")
async def add_catalog_item(payload: CatalogAddPayload, db: Session = Depends(get_db)):
    """Lazily download one catalog item's high-res image and add it to the library (approved,
    metadata prefilled — no AI needed). Optionally links it to a playlist. Idempotent per source_url."""
    col = await _catalog_collection(db, payload.collection_id)
    if not col:
        raise HTTPException(404, detail=f"Unknown collection: {payload.collection_id}")
    items = col.get("items", [])
    if payload.item_index < 0 or payload.item_index >= len(items):
        raise HTTPException(404, detail="Unknown catalog item")
    item = items[payload.item_index]
    # Federated items come from a third party — SSRF-guard the image URL before the server fetches it
    # (a malicious manifest could point image.full_url at an internal/loopback address).
    if payload.collection_id.startswith(SUB_PREFIX):
        try:
            federation._assert_public_url(item["source_url"])
        except federation.FederationError as e:
            raise HTTPException(400, detail=f"Refused to fetch image: {e}") from e
    art = await _download_and_create_artwork(
        db, source_url=item["source_url"], thumbnail_url=item.get("thumbnail_url"),
        metadata=item, playlist_id=payload.playlist_id)
    return {"status": "added", "artwork_id": art.id, "title": art.title}

class CatalogAddCollectionPayload(BaseModel):
    collection_id: str
    playlist_id: Optional[int] = None

@app.post("/api/catalog/add-collection")
async def add_catalog_collection(payload: CatalogAddCollectionPayload, db: Session = Depends(get_db)):
    """Best-effort add of every item in a collection (continues past individual failures)."""
    col = await _catalog_collection(db, payload.collection_id)
    if not col:
        raise HTTPException(404, detail=f"Unknown collection: {payload.collection_id}")
    added, failed = 0, 0
    for item in col.get("items", []):
        try:
            await _download_and_create_artwork(
                db, source_url=item["source_url"], thumbnail_url=item.get("thumbnail_url"),
                metadata=item, playlist_id=payload.playlist_id)
            added += 1
        except Exception as e:
            failed += 1
            logger.warning(f"[Catalog] add-collection item failed: {e}")
    return {"status": "done", "added": added, "failed": failed}

# -----------------------------------------------------------------------------
# §4.9 Federation — subscribe to third-party Manifest v2 collections by URL
# -----------------------------------------------------------------------------

def _sub_summary(s: SubscriptionModel) -> dict:
    return {
        "id": s.id,
        "url": s.url,
        "collection_id": f"{SUB_PREFIX}{s.id}",
        "title": s.title,
        "publisher": {"id": s.publisher_id, "name": s.publisher_name, "url": s.publisher_url},
        "trust": s.trust,
        "enabled": s.enabled,
        "item_count": s.item_count,
        "last_synced": s.last_synced.isoformat() if s.last_synced else None,
        "last_status": s.last_status,
    }

class SubscriptionPayload(BaseModel):
    url: str

@app.get("/api/subscriptions")
async def list_subscriptions(db: Session = Depends(get_db)):
    return [_sub_summary(s) for s in db.query(SubscriptionModel).order_by(SubscriptionModel.id).all()]

@app.post("/api/subscriptions")
async def add_subscription(payload: SubscriptionPayload, db: Session = Depends(get_db)):
    """Subscribe to a publisher's Manifest v2 URL. Fetched + safety-checked + validated BEFORE a row
    is created, so a bad/unsafe URL never persists. Trust starts at 'community' (URL-added)."""
    url = payload.url.strip()
    if db.query(SubscriptionModel).filter(SubscriptionModel.url == url).first():
        raise HTTPException(409, detail="Already subscribed to this URL")
    try:
        manifest = await federation.fetch_manifest(url)
    except federation.FederationError as e:
        raise HTTPException(400, detail=str(e)) from e
    pub = manifest.get("publisher") or {}
    sub = SubscriptionModel(
        url=url, collection_id=manifest.get("id"), title=manifest.get("title"),
        publisher_id=pub.get("id"), publisher_name=pub.get("name"), publisher_url=pub.get("url"),
        trust=federation.assess_trust(manifest), enabled=True, cached_manifest=json.dumps(manifest),
        item_count=len(manifest.get("items", [])), last_status="ok", last_synced=datetime.now(UTC))
    db.add(sub); db.commit(); db.refresh(sub)
    return _sub_summary(sub)

@app.post("/api/subscriptions/{sub_id}/sync")
async def sync_subscription_endpoint(sub_id: int, db: Session = Depends(get_db)):
    sub = db.query(SubscriptionModel).filter(SubscriptionModel.id == sub_id).first()
    if not sub:
        raise HTTPException(404)
    await federation.sync_subscription(db, sub)
    return _sub_summary(sub)

@app.delete("/api/subscriptions/{sub_id}")
async def delete_subscription(sub_id: int, db: Session = Depends(get_db)):
    sub = db.query(SubscriptionModel).filter(SubscriptionModel.id == sub_id).first()
    if not sub:
        raise HTTPException(404)
    db.delete(sub); db.commit()
    return {"status": "removed"}

# -----------------------------------------------------------------------------
# 5. Static File Serving
# -----------------------------------------------------------------------------
if ARTWORK_ROOT.exists():
    app.mount("/media", StaticFiles(directory=str(ARTWORK_ROOT)), name="media")
STATIC_DIR = Path("static")
@app.get("/admin")
async def get_admin_page(): return FileResponse(STATIC_DIR / "admin.html")

@app.get("/help")
async def get_help_page(): return FileResponse(STATIC_DIR / "help.html")

@app.get("/studio")
async def get_studio_page(): return FileResponse(STATIC_DIR / "studio.html")

if STATIC_DIR.exists():
    app.mount("/", StaticFiles(directory=str(STATIC_DIR), html=True), name="static")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app:app", host="0.0.0.0", port=8000, reload=True)

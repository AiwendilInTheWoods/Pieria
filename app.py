#!/usr/bin/env python3
"""
FastAPI Backend for the Artwork Display Engine.
Phase 4: Targeted WebSocket Routing for Multiple Displays.
"""

import os
import logging
import random
import shutil
import io
from contextlib import asynccontextmanager
from typing import List, Dict, Any, Optional
from pathlib import Path
from urllib.parse import quote

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query, Depends, UploadFile, File, Form, BackgroundTasks, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session
from sqlalchemy import select, delete, update
from PIL import Image
from pydantic import BaseModel

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
from database import init_db, get_db, SessionLocal
from models import PlaylistModel, ArtworkModel, playlist_artwork, DiscoveryQueueModel
from agents import process_artwork
import curator
import scout
import httpx

ARTWORK_ROOT = Path(os.getenv("ARTWORK_ROOT", "Artwork"))
LIBRARY_DIR = ARTWORK_ROOT / "_Library"

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

@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    db = SessionLocal()
    try:
        sync_db_with_filesystem(db)
    finally:
        db.close()
    yield

app = FastAPI(title="Artwork Display Engine API", version="0.4.5", lifespan=lifespan)

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
    artist: Optional[str] = None
    year: Optional[str] = None
    description: Optional[str] = None
    tags: Optional[str] = None
    status: str
    crop_x: float
    crop_y: float
    crop_width: float
    crop_height: float
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
    title: str; artist: str; year: str; description: str; tags: str

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

class DiscoveryQueueSchema(BaseModel):
    id: int
    source_url: str
    thumbnail_url: str
    proposed_title: str
    proposed_artist: str
    source_api: str
    status: str
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

@app.get("/artworks/pending", response_model=List[ArtworkSchema])
async def get_pending_artworks(db: Session = Depends(get_db)):
    return db.query(ArtworkModel).filter(ArtworkModel.status == 'pending_review').all()

@app.patch("/artworks/{artwork_id}/approve", response_model=ArtworkSchema)
async def approve_artwork(artwork_id: int, data: ArtworkApproval, db: Session = Depends(get_db)):
    art = db.query(ArtworkModel).filter(ArtworkModel.id == artwork_id).first()
    if not art: raise HTTPException(404)
    art.title, art.artist, art.year, art.description, art.tags, art.status = data.title, data.artist, data.year, data.description, data.tags, 'approved'
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
    background_tasks.add_task(curator.batch_enrich_all, db)
    return {"status": "Batch enrichment started in background"}

@app.get("/api/discover/queue", response_model=List[DiscoveryQueueSchema])
async def get_discovery_queue(db: Session = Depends(get_db)):
    """Returns the list of pending art discoveries."""
    return db.query(DiscoveryQueueModel).filter(DiscoveryQueueModel.status == 'pending').all()

@app.post("/api/discover/refresh")
async def trigger_discovery(search: Optional[str] = Query(None), background_tasks: BackgroundTasks = None, db: Session = Depends(get_db)):
    """Triggers scouts to find new art, optionally guided by a search term."""
    background_tasks.add_task(scout.run_scouts, db, query=search)
    return {"status": "Art scouts dispatched", "search": search}

@app.post("/api/discover/dispatch")
async def dispatch_discovery(request: DispatchRequest, background_tasks: BackgroundTasks, db: Session = Depends(get_db)):
    """Multi-source art discovery dispatch."""
    background_tasks.add_task(scout.run_scouts, db, query=request.search, sources=request.sources)
    return {"status": "Art scouts dispatched", "sources": request.sources, "search": request.search}

@app.post("/api/discover/approve/{item_id}")
async def approve_discovery(item_id: int, background_tasks: BackgroundTasks, db: Session = Depends(get_db)):
    """Downloads approved discovery and adds to library."""
    item = db.query(DiscoveryQueueModel).filter(DiscoveryQueueModel.id == item_id).first()
    if not item: raise HTTPException(404)
    
    # 1. Download full-res image
    filename = f"scouted_{item_id}_{item.proposed_title.replace(' ', '_')[:50]}.jpg"
    filepath = LIBRARY_DIR / filename
    
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(item.source_url, timeout=30.0)
            if resp.status_code == 200:
                with open(filepath, "wb") as f:
                    f.write(resp.content)
            else:
                raise Exception(f"Download failed: {resp.status_code}")
    except Exception as e:
        logger.error(f"[Discovery] Download failed: {e}")
        raise HTTPException(500, detail=str(e))

    # 2. Add to database
    with Image.open(filepath) as img: w, h = img.size
    new_art = ArtworkModel(
        filename=filename, 
        original_width=w, original_height=h,
        title=item.proposed_title,
        artist=item.proposed_artist,
        status='pending_review'
    )
    db.add(new_art)
    item.status = 'approved'
    db.commit()
    db.refresh(new_art)

    # 3. Enrich with RAG Curator
    background_tasks.add_task(curator.enrich_artwork, new_art.id, db)
    
    return {"status": "Art added to library and enrichment started", "artwork_id": new_art.id}

@app.post("/api/discover/reject/{item_id}")
async def reject_discovery(item_id: int, db: Session = Depends(get_db)):
    """Removes a discovery from the queue."""
    item = db.query(DiscoveryQueueModel).filter(DiscoveryQueueModel.id == item_id).first()
    if not item: raise HTTPException(404)
    item.status = 'rejected'
    db.commit()
    return {"status": "Rejected"}

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

@app.delete("/artworks/{artwork_id}")
async def permanent_delete_artwork(artwork_id: int, db: Session = Depends(get_db)):
    art = db.query(ArtworkModel).filter(ArtworkModel.id == artwork_id).first()
    if not art: raise HTTPException(404)
    f_path = LIBRARY_DIR / art.filename
    if f_path.exists(): f_path.unlink()
    db.delete(art); db.commit(); return {"status": "wiped"}

@app.get("/next-image")
async def get_next_image(playlist_name: str, shuffle: bool = Query(True), current_index: Optional[int] = Query(None), direction: int = Query(1), db: Session = Depends(get_db)):
    p = db.query(PlaylistModel).filter(PlaylistModel.name == playlist_name).first()
    if not p: raise HTTPException(404)
    artworks = db.query(ArtworkModel).join(playlist_artwork).filter(playlist_artwork.c.playlist_id == p.id, ArtworkModel.status == 'approved').order_by(playlist_artwork.c.display_order).all()
    if not artworks: raise HTTPException(404, detail="No approved images")
    count = len(artworks)
    if shuffle:
        idx = random.randint(0, count - 1)
        if count > 1 and current_index == idx:
            while idx == current_index: idx = random.randint(0, count - 1)
    else:
        base_idx = current_index if current_index is not None else -1
        idx = (base_idx + direction) % count
    art = artworks[idx]
    image_path = f"{p.name}/{art.filename}"
    return {
        "index": idx, "image_url": f"/media/_Library/{quote(art.filename)}",
        "playlist": playlist_name, "display_time": p.display_time,
        "default_mode": p.default_mode, "shuffle": p.shuffle,
        "placard_wait": p.placard_initial_wait_sec,
        "placard_show": p.placard_initial_show_sec,
        "placard_manual": p.placard_interaction_show_sec,
        "crop": {"x": art.crop_x, "y": art.crop_y, "width": art.crop_width, "height": art.crop_height},
        "metadata": {"title": art.title, "artist": art.artist, "year": art.year, "description": art.description, "tags": art.tags}
    }

# -----------------------------------------------------------------------------
# 4. WebSocket & Remote Control
# -----------------------------------------------------------------------------
@app.get("/remote")
async def get_remote_page(): 
    return FileResponse(STATIC_DIR / "remote.html")

@app.get("/api/remote/displays")
async def get_active_displays():
    """Returns a list of all display IDs currently connected via WebSocket."""
    return list(manager.active_connections.keys())

@app.post("/api/remote/change")
async def remote_change_playlist(request: RemoteChangeRequest):
    """Targeted command to change a playlist, mode, or trigger navigation on a specific display."""
    logger.info(f"Targeted Remote Command: {request.target_display} -> {request.action}")
    
    payload = {"action": request.action}
    if request.playlist:
        payload["playlist"] = request.playlist
    if request.mode:
        payload["mode"] = request.mode
        
    await manager.send_personal_message(payload, request.target_display)
    return {"status": "command_sent"}

@app.websocket("/ws/{display_id}")
async def websocket_endpoint(websocket: WebSocket, display_id: str):
    """Handles targeted display connections."""
    await manager.connect(websocket, display_id)
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

if STATIC_DIR.exists():
    app.mount("/", StaticFiles(directory=str(STATIC_DIR), html=True), name="static")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app:app", host="0.0.0.0", port=8000, reload=True)

#!/usr/bin/env python3
"""
FastAPI Backend for the Artwork Display Engine.
Phase 3: Many-to-Many Playlists, AI Pipeline, and Centralized Library.
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
from fastapi import FastAPI, HTTPException, Query, Depends, UploadFile, File, Form, BackgroundTasks
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session
from sqlalchemy import select, delete, update
from PIL import Image

# Load environment variables
load_dotenv()

# Local imports
from database import init_db, get_db, SessionLocal
from models import PlaylistModel, ArtworkModel, playlist_artwork
from agents import process_artwork
from pydantic import BaseModel

# -----------------------------------------------------------------------------
# 1. Configuration & Logging
# -----------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger("artwork-display-api")

ARTWORK_ROOT = Path(os.getenv("ARTWORK_ROOT", "Artwork"))
LIBRARY_DIR = ARTWORK_ROOT / "_Library"

async def run_ai_pipeline(artwork_id: int):
    db = SessionLocal()
    try:
        await process_artwork(artwork_id, db)
    finally:
        db.close()

def sync_db_with_filesystem(db: Session) -> None:
    """
    Migration Logic: Scans the existing filesystem structure and populates the DB.
    Legacy folders become Playlists, files move to _Library.
    """
    if not ARTWORK_ROOT.exists():
        ARTWORK_ROOT.mkdir(parents=True, exist_ok=True)
    
    if not LIBRARY_DIR.exists():
        LIBRARY_DIR.mkdir(parents=True, exist_ok=True)

    valid_extensions = {".jpg", ".jpeg", ".png", ".webp"}
    
    # Process the root Artwork directory for legacy folders
    for item in ARTWORK_ROOT.iterdir():
        if item.is_dir() and item.name != "_Library":
            playlist = db.query(PlaylistModel).filter(PlaylistModel.name == item.name).first()
            if not playlist:
                playlist = PlaylistModel(name=item.name)
                db.add(playlist); db.commit(); db.refresh(playlist)

            # Move files to library and link to this playlist
            for file_path in item.iterdir():
                if file_path.suffix.lower() in valid_extensions:
                    dest_path = LIBRARY_DIR / file_path.name
                    if not dest_path.exists():
                        shutil.move(file_path, dest_path)
                    
                    # Create or find artwork record
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
                    
                    # Link to playlist if not already linked
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

app = FastAPI(title="Artwork Display Engine API", version="0.3.5", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# -----------------------------------------------------------------------------
# 2. Data Models (Schemas)
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

class ReorderRequest(BaseModel):
    artwork_ids: List[int]

# -----------------------------------------------------------------------------
# 3. Optimization Logic
# -----------------------------------------------------------------------------
def get_optimized_image(file_path: Path, max_size: tuple[int, int], quality: int = 80) -> bytes:
    from PIL import Image
    with Image.open(file_path) as img:
        if img.mode in ("RGBA", "P"): img = img.convert("RGB")
        img.thumbnail(max_size, Image.Resampling.LANCZOS)
        img_byte_arr = io.BytesIO()
        img.save(img_byte_arr, format='JPEG', quality=quality, optimize=True)
        return img_byte_arr.getvalue()

# -----------------------------------------------------------------------------
# 4. API Endpoints
# -----------------------------------------------------------------------------

@app.get("/artworks", response_model=List[ArtworkSchema])
async def get_full_library(db: Session = Depends(get_db)):
    """Retrieves all artworks in the centralized library."""
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

@app.delete("/playlists/{playlist_id}")
async def delete_playlist(playlist_id: int, db: Session = Depends(get_db)):
    p = db.query(PlaylistModel).filter(PlaylistModel.id == playlist_id).first()
    if not p: raise HTTPException(404)
    db.delete(p); db.commit(); return {"status": "ok"}

@app.post("/playlists/{playlist_id}/artworks/{artwork_id}")
async def link_artwork_to_playlist(playlist_id: int, artwork_id: int, db: Session = Depends(get_db)):
    """Links an existing library artwork to a playlist."""
    db.execute(playlist_artwork.insert().values(playlist_id=playlist_id, artwork_id=artwork_id))
    db.commit(); return {"status": "linked"}

@app.delete("/playlists/{playlist_id}/artworks/{artwork_id}")
async def unlink_artwork_from_playlist(playlist_id: int, artwork_id: int, db: Session = Depends(get_db)):
    """Removes an artwork from a playlist without deleting the file."""
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
async def upload_artwork(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...), 
    playlist_id: Optional[int] = Form(None),
    db: Session = Depends(get_db)
):
    """Centralized upload to library, with optional playlist linking."""
    if not LIBRARY_DIR.exists(): LIBRARY_DIR.mkdir(parents=True)
    f_path = LIBRARY_DIR / file.filename
    with open(f_path, "wb") as b: shutil.copyfileobj(file.file, b)
    
    from PIL import Image
    with Image.open(f_path) as img: w, h = img.size
    
    new_a = ArtworkModel(
        filename=file.filename, original_width=w, original_height=h, status='pending_review'
    )
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
    """Wipes artwork from DB and filesystem."""
    art = db.query(ArtworkModel).filter(ArtworkModel.id == artwork_id).first()
    if not art: raise HTTPException(404)
    f_path = LIBRARY_DIR / art.filename
    if f_path.exists(): f_path.unlink()
    db.delete(art); db.commit(); return {"status": "wiped"}

@app.get("/next-image")
async def get_next_image(
    playlist_name: str, 
    shuffle: bool = Query(True), 
    current_index: Optional[int] = Query(None), 
    direction: int = Query(1), 
    db: Session = Depends(get_db)
):
    p = db.query(PlaylistModel).filter(PlaylistModel.name == playlist_name).first()
    if not p: raise HTTPException(404)
    
    # Query using the association table to honor per-playlist ordering
    artworks = db.query(ArtworkModel).join(playlist_artwork).filter(
        playlist_artwork.c.playlist_id == p.id,
        ArtworkModel.status == 'approved'
    ).order_by(playlist_artwork.c.display_order).all()
    
    if not artworks: raise HTTPException(404, detail="No approved images in playlist")
    count = len(artworks)
    
    # Logic fix: Handle 'shuffle' correctly and calculate index based on count
    if shuffle:
        idx = random.randint(0, count - 1)
        if count > 1 and current_index == idx:
            while idx == current_index:
                idx = random.randint(0, count - 1)
    else:
        # Step through in the given direction
        base_idx = current_index if current_index is not None else -1
        idx = (base_idx + direction) % count
    
    art = artworks[idx]
    return {
        "index": idx, "image_url": f"/media/_Library/{quote(art.filename)}",
        "playlist": playlist_name, "display_time": p.display_time,
        "crop": {"x": art.crop_x, "y": art.crop_y, "width": art.crop_width, "height": art.crop_height},
        "metadata": {"title": art.title, "artist": art.artist, "year": art.year, "description": art.description, "tags": art.tags}
    }

# -----------------------------------------------------------------------------
# 5. Static File Serving
# -----------------------------------------------------------------------------
if ARTWORK_ROOT.exists():
    app.mount("/media", StaticFiles(directory=str(ARTWORK_ROOT)), name="media")
STATIC_DIR = Path("static")
@app.get("/admin")
async def get_admin_page(): return FileResponse(STATIC_DIR / "admin.html")
if STATIC_DIR.exists():
    app.mount("/", StaticFiles(directory=str(STATIC_DIR), html=True), name="static")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app:app", host="0.0.0.0", port=8000, reload=True)

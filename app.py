#!/usr/bin/env python3
"""
FastAPI Backend for the Artwork Display Engine.
Phase 2: DB-backed management with Image Metadata and Precision Cropping.
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

from fastapi import FastAPI, HTTPException, Query, Depends, UploadFile, File, Form
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session
from pydantic import BaseModel, Field
from PIL import Image

# Local imports for Database and Models
from database import init_db, get_db, SessionLocal
from models import PlaylistModel, ArtworkModel

# -----------------------------------------------------------------------------
# 1. Configuration & Logging
# -----------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger("artwork-display-api")

ARTWORK_ROOT = Path(os.getenv("ARTWORK_ROOT", "Artwork"))

def sync_db_with_filesystem(db: Session) -> None:
    if not ARTWORK_ROOT.exists():
        ARTWORK_ROOT.mkdir(parents=True, exist_ok=True)
        return

    valid_extensions = {".jpg", ".jpeg", ".png", ".webp"}
    for item in ARTWORK_ROOT.iterdir():
        if item.is_dir():
            playlist = db.query(PlaylistModel).filter(PlaylistModel.name == item.name).first()
            if not playlist:
                playlist = PlaylistModel(name=item.name); db.add(playlist); db.commit(); db.refresh(playlist)

            existing_filenames = {a.filename for a in playlist.artworks}
            next_order = len(playlist.artworks)

            for file_path in item.iterdir():
                if file_path.suffix.lower() in valid_extensions:
                    filename = file_path.name
                    if filename not in existing_filenames:
                        # Extract real dimensions for precision scaling
                        with Image.open(file_path) as img:
                            w, h = img.size
                        
                        new_art = ArtworkModel(
                            filename=filename, 
                            playlist_id=playlist.id,
                            display_order=next_order,
                            original_width=w, original_height=h,
                            crop_x=0, crop_y=0, crop_width=0, crop_height=0
                        )
                        db.add(new_art)
                        next_order += 1
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

app = FastAPI(title="Artwork Display Engine API", version="0.2.4", lifespan=lifespan)

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
    playlist_id: int
    display_order: int
    original_width: int
    original_height: int
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

class CropMetadataUpdate(BaseModel):
    crop_x: float
    crop_y: float
    crop_width: float
    crop_height: float

class PlaylistUpdate(BaseModel):
    display_time: Optional[int] = None

class ReorderRequest(BaseModel):
    artwork_ids: List[int]

# -----------------------------------------------------------------------------
# 3. Optimization Logic
# -----------------------------------------------------------------------------
def get_optimized_image(file_path: Path, max_size: tuple[int, int], quality: int = 80) -> bytes:
    with Image.open(file_path) as img:
        if img.mode in ("RGBA", "P"): img = img.convert("RGB")
        img.thumbnail(max_size, Image.Resampling.LANCZOS)
        img_byte_arr = io.BytesIO()
        img.save(img_byte_arr, format='JPEG', quality=quality, optimize=True)
        return img_byte_arr.getvalue()

# -----------------------------------------------------------------------------
# 4. API Endpoints
# -----------------------------------------------------------------------------

@app.get("/playlists", response_model=List[PlaylistSchema])
async def list_playlists(db: Session = Depends(get_db)):
    return db.query(PlaylistModel).all()

@app.post("/playlists", response_model=PlaylistSchema)
async def create_playlist(name: str = Form(...), db: Session = Depends(get_db)):
    existing = db.query(PlaylistModel).filter(PlaylistModel.name == name).first()
    if existing: raise HTTPException(status_code=400, detail="Exists")
    ARTWORK_ROOT.joinpath(name).mkdir(parents=True, exist_ok=True)
    new_p = PlaylistModel(name=name); db.add(new_p); db.commit(); db.refresh(new_p)
    return new_p

@app.patch("/playlists/{playlist_id}", response_model=PlaylistSchema)
async def update_playlist(playlist_id: int, data: PlaylistUpdate, db: Session = Depends(get_db)):
    p = db.query(PlaylistModel).filter(PlaylistModel.id == playlist_id).first()
    if not p: raise HTTPException(404)
    if data.display_time is not None: p.display_time = data.display_time
    db.commit(); db.refresh(p); return p

@app.post("/playlists/{playlist_id}/reorder")
async def reorder_playlist(playlist_id: int, request: ReorderRequest, db: Session = Depends(get_db)):
    for index, art_id in enumerate(request.artwork_ids):
        db.query(ArtworkModel).filter(ArtworkModel.id == art_id, ArtworkModel.playlist_id == playlist_id).update({"display_order": index})
    db.commit(); return {"status": "success"}

@app.post("/playlists/{playlist_id}/upload", response_model=ArtworkSchema)
async def upload_artwork(playlist_id: int, file: UploadFile = File(...), db: Session = Depends(get_db)):
    p = db.query(PlaylistModel).filter(PlaylistModel.id == playlist_id).first()
    if not p: raise HTTPException(404)
    f_path = ARTWORK_ROOT / p.name / file.filename
    with open(f_path, "wb") as b: shutil.copyfileobj(file.file, b)
    
    with Image.open(f_path) as img:
        w, h = img.size
    
    new_a = ArtworkModel(filename=file.filename, playlist_id=playlist_id, display_order=len(p.artworks), original_width=w, original_height=h)
    db.add(new_a); db.commit(); db.refresh(new_a); return new_a

@app.get("/artworks/{artwork_id}/thumbnail")
async def get_artwork_thumbnail(artwork_id: int, db: Session = Depends(get_db)):
    art = db.query(ArtworkModel).filter(ArtworkModel.id == artwork_id).first()
    if not art: raise HTTPException(404)
    path = ARTWORK_ROOT / art.playlist.name / art.filename
    return Response(content=get_optimized_image(path, (400, 400), quality=70), media_type="image/jpeg")

@app.get("/artworks/{artwork_id}/preview")
async def get_artwork_preview(artwork_id: int, db: Session = Depends(get_db)):
    art = db.query(ArtworkModel).filter(ArtworkModel.id == artwork_id).first()
    if not art: raise HTTPException(404)
    path = ARTWORK_ROOT / art.playlist.name / art.filename
    return Response(content=get_optimized_image(path, (1920, 1080), quality=85), media_type="image/jpeg")

@app.patch("/artworks/{artwork_id}/crop", response_model=ArtworkSchema)
async def update_crop_metadata(artwork_id: int, crop_data: CropMetadataUpdate, db: Session = Depends(get_db)):
    art = db.query(ArtworkModel).filter(ArtworkModel.id == artwork_id).first()
    if not art: raise HTTPException(404)
    art.crop_x, art.crop_y, art.crop_width, art.crop_height = crop_data.crop_x, crop_data.crop_y, crop_data.crop_width, crop_data.crop_height
    db.commit(); db.refresh(art); return art

@app.delete("/artworks/{artwork_id}")
async def delete_artwork(artwork_id: int, db: Session = Depends(get_db)):
    art = db.query(ArtworkModel).filter(ArtworkModel.id == artwork_id).first()
    if not art: raise HTTPException(404)
    f_path = ARTWORK_ROOT / art.playlist.name / art.filename
    if f_path.exists(): f_path.unlink()
    db.delete(art); db.commit(); return {"status": "ok"}

@app.get("/next-image")
async def get_next_image(playlist_name: str, shuffle: bool = True, current_index: Optional[int] = Query(None), direction: int = Query(1), db: Session = Depends(get_db)):
    p = db.query(PlaylistModel).filter(PlaylistModel.name == playlist_name).first()
    if not p: raise HTTPException(404)
    
    # Explicitly query and sort to ensure DB order is strictly honored
    artworks = db.query(ArtworkModel).filter(ArtworkModel.playlist_id == p.id).order_by(ArtworkModel.display_order).all()
    if not artworks: raise HTTPException(404)
    
    count = len(artworks)
    if shuffle:
        idx = random.randint(0, count-1)
        if count > 1 and current_index == idx:
            while idx == current_index: idx = random.randint(0, count-1)
    else:
        idx = (current_index + direction) % count if current_index is not None else 0
    
    art = artworks[idx]
    image_path = f"{p.name}/{art.filename}"
    return {
        "index": idx, "image_url": f"/media/{'/'.join([quote(s) for s in image_path.split('/')])}",
        "playlist": playlist_name, "display_time": p.display_time,
        "crop": {"x": art.crop_x, "y": art.crop_y, "width": art.crop_width, "height": art.crop_height}
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

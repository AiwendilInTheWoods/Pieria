"""
AI Agents for Artwork Analysis using Gemini API.
Phase 3: Automated Metadata Generation with Image Optimization.
"""

import os
import json
import logging
import io
import google.generativeai as genai
from sqlalchemy.orm import Session
from pathlib import Path
from PIL import Image

from models import ArtworkModel

# Increase Pillow limit for high-res artwork
Image.MAX_IMAGE_PIXELS = 200000000 

logger = logging.getLogger("artwork-display-api.agents")

# Configure Gemini API
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
genai.configure(api_key=GEMINI_API_KEY)

async def process_artwork(artwork_id: int, db: Session):
    """
    Analyzes artwork using Gemini 2.5 Flash.
    Optimizes image size before sending to prevent timeouts.
    """
    logger.info(f"[AI Agent] Starting analysis for artwork ID: {artwork_id}")
    
    artwork = db.query(ArtworkModel).filter(ArtworkModel.id == artwork_id).first()
    if not artwork: return

    from app import LIBRARY_DIR
    image_path = LIBRARY_DIR / artwork.filename
    
    if not image_path.exists(): return

    try:
        model = genai.GenerativeModel('gemini-2.5-flash')
        
        # Optimization: Resize for AI processing (max 2048px)
        # This prevents 504 Deadlines and 429 rate limits on large files.
        with Image.open(image_path) as img:
            if img.mode in ("RGBA", "P"): img = img.convert("RGB")
            img.thumbnail((2048, 2048), Image.Resampling.LANCZOS)
            
            img_byte_arr = io.BytesIO()
            img.save(img_byte_arr, format='JPEG', quality=85)
            optimized_bytes = img_byte_arr.getvalue()

        image_data = {
            'mime_type': 'image/jpeg',
            'data': optimized_bytes
        }

        prompt = (
            "Analyze this artwork. Return ONLY a valid JSON object with the following keys: "
            "'title', 'artist', 'year', 'description' (a 2-sentence museum-style blurb), "
            "and 'tags' (a flat array of 5-10 descriptive strings covering medium, mood, subject, style, "
            "and season such as Spring, Summer, Fall, or Winter if applicable)."
        )

        response = model.generate_content(
            [prompt, image_data],
            generation_config={"response_mime_type": "application/json"}
        )

        metadata = json.loads(response.text)
        logger.info(f"[AI Agent] Metadata generated for {artwork.filename}")

        artwork.title = metadata.get('title', 'Untitled')
        artwork.artist = metadata.get('artist', 'Unknown Artist')
        artwork.year = metadata.get('year', 'Unknown')
        artwork.description = metadata.get('description', '')
        
        tags = metadata.get('tags', [])
        artwork.tags = ", ".join(tags) if isinstance(tags, list) else str(tags)

        db.commit()

    except Exception as e:
        logger.error(f"[AI Agent] AI processing failed: {e}")
        db.rollback()

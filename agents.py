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

async def process_artwork(artwork_id: int, db: Session, user_hint: str = None):
    """
    Analyzes artwork using Gemini 2.5 Flash.
    Optimizes image size before sending to prevent timeouts.
    """
    logger.info(f"[AI Agent] Starting analysis for artwork ID: {artwork_id} (Hint: {user_hint or 'None'})")
    
    artwork = db.query(ArtworkModel).filter(ArtworkModel.id == artwork_id).first()
    if not artwork: return

    from app import LIBRARY_DIR
    image_path = LIBRARY_DIR / artwork.filename
    
    if not image_path.exists(): return

    # Clean filename for context
    clean_filename = Path(artwork.filename).stem.replace("_", " ").replace("-", " ")

    try:
        model = genai.GenerativeModel('gemini-2.5-flash')
        
        # Optimization: Resize for AI processing (max 2048px)
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

        system_instruction = (
            "You are a strict, factual museum curator. I am providing an image. "
            f"The original filename was \"{clean_filename}\". Use this filename as a hint for the title or artist ONLY if it contains readable words; "
            "ignore it if it looks like random letters/numbers. "
        )
        
        if user_hint:
            system_instruction += f"If a User Hint is provided: \"{user_hint}\", treat it as absolute fact and build your description around it. "
        
        system_instruction += (
            "If you cannot confidently identify the artist or location from the visual data or hints, explicitly state \"Unknown Artist\" or \"Unknown Origin\" rather than guessing. "
            "Return ONLY a valid JSON object with the following keys: "
            "'title', 'artist', 'year', 'description' (a 2-sentence museum-style blurb), "
            "and 'tags' (a flat array of 5-10 descriptive strings covering medium, mood, subject, style, "
            "and season if applicable)."
        )

        response = model.generate_content(
            [system_instruction, image_data],
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
        return artwork # Return updated object

    except Exception as e:
        logger.error(f"[AI Agent] AI processing failed: {e}")
        db.rollback()
        return None

import os
import json
import asyncio
import io
import logging
import google.generativeai as genai
from PIL import Image
from sqlalchemy import text
from app import LIBRARY_DIR
from database import SessionLocal, init_db
from models import ArtworkModel

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("vra_migration")

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
genai.configure(api_key=GEMINI_API_KEY)

async def migrate_database():
    logger.info("Initializing DB and applying VRA schema migrations...")
    init_db()  # SQLAlchemy uses ALTER TABLE natively here!
    
    db = SessionLocal()
    
    logger.info("Fetching existing records...")
    # Safe raw SQL read to access the old unmapped 'artist' and 'year' fields
    try:
        old_records = db.execute(text("SELECT id, title, artist, year FROM artworks")).fetchall()
    except Exception as e:
        logger.error(f"Failed to fetch old records. Schema might be totally new: {e}")
        return
    
    model = genai.GenerativeModel('gemini-2.5-flash')
    
    for row in old_records:
        art_id, old_title, old_artist, old_year = row[0], row[1], row[2], row[3]
        
        art = db.query(ArtworkModel).filter(ArtworkModel.id == art_id).first()
        if not art:
            continue
            
        if art.agent_name and art.agent_name != "Unknown Artist":
            logger.info(f"Skipping {art.filename}, already enriched.")
            continue
            
        logger.info(f"Migrating ID {art_id}: {old_title} by {old_artist}")
        image_path = LIBRARY_DIR / art.filename
        
        if not image_path.exists():
            logger.warning(f"Image missing for {art.filename}")
            continue
            
        try:
            with Image.open(image_path) as img:
                if img.mode in ("RGBA", "P"): img = img.convert("RGB")
                img.thumbnail((2048, 2048), Image.Resampling.LANCZOS)
                buf = io.BytesIO()
                img.save(buf, format='JPEG', quality=85)
                optimized_bytes = buf.getvalue()
                
            image_data = {'mime_type': 'image/jpeg', 'data': optimized_bytes}
            
            hint = f"This is '{old_title}' by '{old_artist}' created in '{old_year}'. "
            
            system_instruction = (
                "You are an expert museum archivist migrating basic metadata to VRA Core format."
                f"You MUST use this existing data as absolute Ground Truth Context: {hint}. "
                "Analyze the image visually to determine the medium and cultural context. "
                "Return ONLY a valid JSON object strictly using these keys: "
                "'title', 'agent_name', 'agent_role' (e.g., 'Painter', 'Attributed to'), "
                "'creation_date', 'cultural_context' (e.g., 'Dutch', 'Post-Impressionist'), "
                "'medium' (e.g., 'Oil on canvas', 'Photography'), 'physical_dimensions', 'current_repository' (museum location if known, else 'Unknown')."
            )
            
            response = model.generate_content(
                [system_instruction, image_data],
                generation_config={"response_mime_type": "application/json"}
            )
            
            meta = json.loads(response.text)
            
            art.agent_name = meta.get('agent_name', old_artist)
            art.agent_role = meta.get('agent_role', 'Artist')
            art.creation_date = meta.get('creation_date', old_year)
            art.cultural_context = meta.get('cultural_context', '')
            art.medium = meta.get('medium', '')
            art.physical_dimensions = meta.get('physical_dimensions', '')
            art.current_repository = meta.get('current_repository', '')
            
            db.commit()
            logger.info(f"-- Success! Identified as: {art.medium} located at {art.current_repository}")
            
        except Exception as e:
            logger.error(f"Failed to migrate ID {art_id}: {e}")
            db.rollback()

if __name__ == "__main__":
    asyncio.run(migrate_database())

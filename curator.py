"""
Autonomous RAG Curator for Screen Docent.
Enriches artwork metadata using Wikipedia context and Gemini.
"""

import logging
import wikipedia
import google.generativeai as genai
import json
import os
import asyncio
from sqlalchemy.orm import Session
from models import ArtworkModel

logger = logging.getLogger("artwork-display-api.curator")

# Configure Gemini
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
genai.configure(api_key=GEMINI_API_KEY)

async def enrich_artwork(artwork_id: int, db: Session):
    """
    Fact-checks and enriches artwork metadata using Wikipedia RAG.
    """
    artwork = db.query(ArtworkModel).filter(ArtworkModel.id == artwork_id).first()
    if not artwork:
        return None

    search_query = f"{artwork.title} {artwork.artist}"
    logger.info(f"[RAG Curator] Enriching: {search_query}")

    fact_context = ""
    try:
        # Search Wikipedia for the first paragraph summary
        wiki_page = wikipedia.summary(search_query, sentences=3, auto_suggest=True)
        fact_context = wiki_page
        logger.info(f"[RAG Curator] Found Wikipedia context for {artwork.title}")
    except Exception as e:
        logger.warning(f"[RAG Curator] Wikipedia search failed for {search_query}: {e}")
        fact_context = "No additional factual context found."

    try:
        model = genai.GenerativeModel('gemini-2.5-flash')
        
        prompt = (
            f"You are a strict museum curator performing RAG (Retrieval-Augmented Generation). "
            f"Current Data: Title: {artwork.title}, Artist: {artwork.artist}. "
            f"Factual Context from Wikipedia: \"{fact_context}\" "
            "Task: Rewrite the museum placard metadata using the Factual Context as the primary source of truth. "
            "If the Wikipedia context contradicts the Current Data, prioritize Wikipedia. "
            "Return ONLY a valid JSON object with: 'title', 'artist', 'year', 'description' (2 sentences), and 'tags' (array)."
        )

        response = await asyncio.to_thread(model.generate_content, prompt, generation_config={"response_mime_type": "application/json"})
        metadata = json.loads(response.text)

        artwork.title = metadata.get('title', artwork.title)
        artwork.artist = metadata.get('artist', artwork.artist)
        artwork.year = metadata.get('year', artwork.year)
        artwork.description = metadata.get('description', artwork.description)
        
        tags = metadata.get('tags', [])
        if tags:
            artwork.tags = ", ".join(tags) if isinstance(tags, list) else str(tags)

        db.commit()
        logger.info(f"[RAG Curator] Successfully enriched {artwork.title}")
        return artwork

    except Exception as e:
        logger.error(f"[RAG Curator] Gemini enrichment failed: {e}")
        db.rollback()
        return None

async def batch_enrich_all(db: Session):
    """
    Runs enrichment on all approved artworks with rate-limiting.
    """
    artworks = db.query(ArtworkModel).filter(ArtworkModel.status == 'approved').all()
    logger.info(f"[RAG Curator] Starting batch enrichment for {len(artworks)} items.")
    
    for art in artworks:
        await enrich_artwork(art.id, db)
        await asyncio.sleep(2) # Rate-limiting delay

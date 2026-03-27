"""
Modular Semantic Art Scout for Screen Docent.
Discovers new high-resolution public-domain art.
"""

import logging
import httpx
import random
import traceback
import asyncio
from abc import ABC, abstractmethod
from typing import List, Dict
from sqlalchemy.orm import Session
from models import DiscoveryQueueModel

logger = logging.getLogger("artwork-display-api.scout")

class MuseumScout(ABC):
    @abstractmethod
    async def find_art(self, query: str = None) -> List[Dict]:
        """Returns a list of art dictionaries with source_url, thumbnail_url, etc."""
        pass

class ChicagoArtScout(MuseumScout):
    """
    Scout for the Art Institute of Chicago.
    """
    API_URL = "https://api.artic.edu/api/v1/artworks/search"
    IMAGE_BASE = "https://www.artic.edu/iiif/2/{identifier}/full/843,/0/default.jpg"
    FULL_RES_BASE = "https://www.artic.edu/iiif/2/{identifier}/full/max/0/default.jpg"

    async def find_art(self, query: str = None) -> List[Dict]:
        logger.info(f"[Scout] ChicagoArtScout searching for: {query or 'public domain'}")
        found = []
        headers = {"User-Agent": "ScreenDocent/1.0 (https://github.com/your-repo/screen-docent)"}
        
        try:
            async with httpx.AsyncClient(headers=headers) as client:
                params = {
                    "q": query or "public domain",
                    "query[term][is_public_domain]": "true",
                    "fields": "id,title,artist_title,image_id,width,height",
                    "limit": 50,
                    "page": 1 if query else random.randint(1, 20)
                }
                response = await client.get(self.API_URL, params=params, timeout=15.0)
                if response.status_code != 200: return []
                
                data = response.json()
                artworks = data.get('data', [])
                valid_items = [a for a in artworks if a.get('image_id')]
                selected = random.sample(valid_items, min(len(valid_items), 20))
                
                for art in selected:
                    img_id = art.get('image_id')
                    found.append({
                        "source_url": self.FULL_RES_BASE.format(identifier=img_id),
                        "thumbnail_url": self.IMAGE_BASE.format(identifier=img_id),
                        "proposed_title": art.get('title') or 'Unknown',
                        "proposed_artist": art.get('artist_title') or 'Unknown Artist',
                        "source_api": "Art Institute of Chicago"
                    })
        except Exception:
            logger.error(f"[Scout] ChicagoArtScout failed: {traceback.format_exc()}")
        return found

class MetMuseumScout(MuseumScout):
    """
    Scout for the Metropolitan Museum of Art.
    """
    SEARCH_URL = "https://collectionapi.metmuseum.org/public/collection/v1/search"
    OBJECT_URL = "https://collectionapi.metmuseum.org/public/collection/v1/objects/{id}"

    async def find_art(self, query: str = None) -> List[Dict]:
        q = query or "painting"
        logger.info(f"[Scout] MetMuseumScout searching for: {q}")
        found = []
        headers = {"User-Agent": "ScreenDocent/1.0"}
        
        try:
            async with httpx.AsyncClient(headers=headers) as client:
                params = { "q": q, "hasImages": "true", "isPublicDomain": "true" }
                response = await client.get(self.SEARCH_URL, params=params, timeout=15.0)
                if response.status_code != 200: return []
                
                data = response.json()
                object_ids = data.get('objectIDs', [])
                if not object_ids: return []
                
                selected_ids = random.sample(object_ids, min(len(object_ids), 80))
                count = 0
                for obj_id in selected_ids:
                    if count >= 20: break
                    obj_res = await client.get(self.OBJECT_URL.format(id=obj_id), timeout=10.0)
                    if obj_res.status_code != 200: continue
                    obj_data = obj_res.json()
                    img_url = obj_data.get('primaryImage')
                    if not img_url: continue
                    
                    found.append({
                        "source_url": img_url,
                        "thumbnail_url": obj_data.get('primaryImageSmall') or img_url,
                        "proposed_title": obj_data.get('title') or 'Unknown',
                        "proposed_artist": obj_data.get('artistDisplayName') or 'Unknown Artist',
                        "source_api": "The Metropolitan Museum of Art"
                    })
                    count += 1
        except Exception:
            logger.error(f"[Scout] MetMuseumScout failed: {traceback.format_exc()}")
        return found

class ClevelandArtScout(MuseumScout):
    """
    Scout for the Cleveland Museum of Art.
    """
    API_URL = "https://openaccess-api.clevelandart.org/api/artworks/"

    async def find_art(self, query: str = None) -> List[Dict]:
        q = query or "painting"
        logger.info(f"[Scout] ClevelandArtScout searching for: {q}")
        found = []
        headers = {"User-Agent": "ScreenDocent/1.0"}
        
        try:
            async with httpx.AsyncClient(headers=headers) as client:
                params = { "q": q, "has_image": "1", "cc0": "1", "limit": 30 }
                response = await client.get(self.API_URL, params=params, timeout=15.0)
                if response.status_code != 200: return []
                
                data = response.json()
                artworks = data.get('data', [])
                count = 0
                for art in artworks:
                    if count >= 20: break
                    images = art.get('images', {})
                    if not images: continue
                    full_res = images.get('print', {}).get('url') or images.get('web', {}).get('url')
                    if not full_res: continue
                    creators = art.get('creators', [])
                    artist = creators[0].get('description') if creators else 'Unknown Artist'
                    found.append({
                        "source_url": full_res,
                        "thumbnail_url": images.get('web', {}).get('url') or full_res,
                        "proposed_title": art.get('title') or 'Unknown',
                        "proposed_artist": artist,
                        "source_api": "Cleveland Museum of Art"
                    })
                    count += 1
        except Exception:
            logger.error(f"[Scout] ClevelandArtScout failed: {traceback.format_exc()}")
        return found

class RijksmuseumScout(MuseumScout):
    """
    Scout for the Rijksmuseum (Amsterdam) using the Open Data Linked Art search.
    Resolves LOD identifiers to extract high-res images.
    """
    SEARCH_URL = "https://data.rijksmuseum.nl/search/collection"

    async def find_art(self, query: str = None) -> List[Dict]:
        logger.info(f"[Scout] RijksmuseumScout searching for: {query or 'all'}")
        found = []
        headers = { "User-Agent": "ScreenDocent/1.0", "Accept": "application/json" }
        
        try:
            async with httpx.AsyncClient(headers=headers, follow_redirects=True) as client:
                params = { "imageAvailable": "true" }
                if query: params["description"] = query
                
                response = await client.get(self.SEARCH_URL, params=params, timeout=15.0)
                if response.status_code != 200: return []
                
                data = response.json()
                items = data.get('orderedItems', [])
                if not items: return []
                
                selected_items = random.sample(items, min(len(items), 20))
                tasks = [self._resolve_artwork(client, item['id']) for item in selected_items]
                results = await asyncio.gather(*tasks)
                for r in results:
                    if r: found.append(r)
        except Exception:
            logger.error(f"[Scout] RijksmuseumScout failed: {traceback.format_exc()}")
        return found

    async def _resolve_artwork(self, client: httpx.AsyncClient, object_id: str) -> Dict:
        try:
            res = await client.get(object_id, timeout=10.0)
            if res.status_code != 200: return None
            obj = res.json()
            
            title = "Unknown"
            for n in obj.get('identified_by', []):
                if n.get('type') == 'Name':
                    title = n.get('content', title)
                    break
            
            artist = "Unknown Artist"
            produced_by = obj.get('produced_by', {})
            for p in produced_by.get('part', []):
                carried_out = p.get('carried_out_by', [])
                if carried_out:
                    artist = carried_out[0].get('_label') or artist
                    if artist == "Unknown Artist":
                        notations = carried_out[0].get('notation', [])
                        if notations: artist = notations[0].get('@value', artist)
                    break

            shows = obj.get('shows', [])
            if not shows: return None
            v_res = await client.get(shows[0].get('id'), timeout=10.0)
            if v_res.status_code != 200: return None
            v_item = v_res.json()
            
            digitally_shown = v_item.get('digitally_shown_by', [])
            if not digitally_shown: return None
            d_res = await client.get(digitally_shown[0].get('id'), timeout=10.0)
            if d_res.status_code != 200: return None
            d_obj = d_res.json()
            
            access_points = d_obj.get('access_point', [])
            if not access_points: return None
            img_url = access_points[0].get('id')
            
            return {
                "source_url": img_url,
                "thumbnail_url": img_url.replace("/full/max/", "/full/843,/"),
                "proposed_title": title,
                "proposed_artist": artist,
                "source_api": "Rijksmuseum (Amsterdam)"
            }
        except Exception: return None

class SmkScout(MuseumScout):
    """
    Scout for the Statens Museum for Kunst (Denmark).
    """
    API_URL = "https://api.smk.dk/api/v1/art/search/"

    async def find_art(self, query: str = None) -> List[Dict]:
        q = query or "*"
        logger.info(f"[Scout] SmkScout searching for: {q}")
        found = []
        try:
            async with httpx.AsyncClient() as client:
                params = {
                    "keys": q,
                    "filters": "[has_image:true],[public_domain:true]",
                    "rows": 30,
                    "offset": random.randint(0, 100)
                }
                response = await client.get(self.API_URL, params=params, timeout=15.0)
                if response.status_code != 200: return []
                
                data = response.json()
                items = data.get('items', [])
                count = 0
                for item in items:
                    if count >= 20: break
                    image_url = item.get('image_native')
                    if not image_url:
                        iiif_id = item.get('image_iiif_id')
                        if iiif_id:
                            image_url = f"https://iip.smk.dk/iiif/jp2/{iiif_id}/full/max/0/default.jpg"
                    if not image_url: continue
                    
                    artist = "Unknown Artist"
                    production = item.get('production', [])
                    if production and isinstance(production, list):
                        artist = production[0].get('creator', artist)

                    found.append({
                        "source_url": image_url,
                        "thumbnail_url": item.get('image_thumbnail') or image_url,
                        "proposed_title": item.get('titles', [{}])[0].get('title') or 'Unknown',
                        "proposed_artist": artist,
                        "source_api": "Statens Museum for Kunst (Denmark)"
                    })
                    count += 1
        except Exception:
            logger.error(f"[Scout] SmkScout failed: {traceback.format_exc()}")
        return found

async def run_scouts(db: Session, query: str = None, sources: List[str] = None):
    """
    Runs selected active scouts and populates the DiscoveryQueue.
    """
    all_scouts = {
        "chicago": ChicagoArtScout(),
        "met": MetMuseumScout(),
        "cleveland": ClevelandArtScout(),
        "rijksmuseum": RijksmuseumScout(),
        "smk": SmkScout()
    }
    
    active_scouts = []
    if sources:
        for s in sources:
            if s in all_scouts: active_scouts.append(all_scouts[s])
    else:
        active_scouts = list(all_scouts.values())

    if not active_scouts: return

    tasks = [scout.find_art(query=query) for scout in active_scouts]
    results_lists = await asyncio.gather(*tasks)
    
    total_new = 0
    for results in results_lists:
        for item in results:
            existing = db.query(DiscoveryQueueModel).filter(DiscoveryQueueModel.source_url == item['source_url']).first()
            if not existing:
                new_entry = DiscoveryQueueModel(**item)
                db.add(new_entry)
                total_new += 1
    db.commit()
    logger.info(f"[Scout] DiscoveryQueue updated with {total_new} new items across {len(active_scouts)} sources.")

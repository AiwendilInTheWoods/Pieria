"""
Modular Semantic Art Scout for Screen Docent.
Discovers new high-resolution public-domain art.
"""

import logging
import json
import httpx
import random
import traceback
import asyncio
from abc import ABC, abstractmethod
from typing import List, Dict
from sqlalchemy.orm import Session
from models import DiscoveryQueueModel, SettingsModel

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
                    "fields": "id,title,artist_title,image_id,width,height,date_display,medium_display,credit_line,dimensions",
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
                        "source_api": "Art Institute of Chicago",
                        "context_hints": json.dumps(art)
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
                
                selected_ids = object_ids[:60]
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
                        "source_api": "The Metropolitan Museum of Art",
                        "context_hints": json.dumps(obj_data)
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
                        "source_api": "Cleveland Museum of Art",
                        "context_hints": json.dumps(art)
                    })
                    count += 1
        except Exception:
            logger.error(f"[Scout] ClevelandArtScout failed: {traceback.format_exc()}")
        return found

class RijksmuseumScout(MuseumScout):
    """
    Scout for the Rijksmuseum (Amsterdam) using the free Open Data Search API.
    Does NOT require an API key. 
    Uses Linked Art resolution to extract IIIF image endpoints and metadata.
    """
    SEARCH_URL = "https://data.rijksmuseum.nl/search/collection"

    async def find_art(self, query: str = None) -> List[Dict]:
        q = query or "painting"
        logger.info(f"[Scout] RijksmuseumScout (Open Data) searching for: {q}")
        found = []
        try:
            async with httpx.AsyncClient(follow_redirects=True) as client:
                # 1. Search for objects. We use 'description' as it supports broad text matching.
                # Tested success with 'description' and 'title' as reliable search hooks.
                params = { "type": "painting", "description": q }
                response = await client.get(self.SEARCH_URL, params=params, timeout=15.0)
                if response.status_code != 200: 
                    logger.error(f"[Scout] Rijksmuseum search failed: {response.status_code}")
                    return []
                
                data = response.json()
                items = data.get('orderedItems', [])
                if not items: return []
                
                # Limit to top 10 items to prevent flooding with LOD resolutions
                selected_items = items[:10]
                
                for item in selected_items:
                    item_url = item.get('id')
                    if not item_url: continue
                    
                    # Ensure we use the 'data' resolver directly to preserve profile params
                    item_url = item_url.replace("id.rijksmuseum.nl", "data.rijksmuseum.nl")
                    
                    # 2. Resolve each item using Dublin Core profile for easy metadata extraction
                    res_params = { "_profile": "dc" }
                    res_headers = { "Accept": "application/ld+json" }
                    res_resp = await client.get(item_url, params=res_params, headers=res_headers, timeout=10.0)
                    
                    if res_resp.status_code != 200: continue
                    
                    item_data = res_resp.json()
                    
                    # 3. Extract IIIF image from 'relation' field
                    relation = item_data.get('relation', {})
                    img_base_url = None
                    if isinstance(relation, dict):
                        img_base_url = relation.get('@id')
                    elif isinstance(relation, list):
                        for r in relation:
                            if isinstance(r, dict) and r.get('@id'):
                                img_base_url = r.get('@id')
                                break

                    if not img_base_url: continue
                    
                    # Ensure it's a direct image link or IIIF endpoint
                    source_url = img_base_url
                    # Thumbnails can be generated by adjusting IIIF region parameters
                    thumb_url = source_url.replace("/full/max/", "/full/400,/")
                    
                    def extract_label(node):
                        if isinstance(node, str): return node
                        if isinstance(node, dict):
                            t_val = node.get('title')
                            if isinstance(t_val, list):
                                for t in t_val:
                                    if isinstance(t, dict) and t.get('@language') == 'en':
                                        return t.get('@value')
                                if t_val: return t_val[0].get('@value') if isinstance(t_val[0], dict) else t_val[0]
                            return t_val or node.get('@id')
                        return str(node)

                    artist_node = item_data.get('creator', {})
                    artist = extract_label(artist_node) or "Unknown Artist"
                    
                    # Note: Top-level 'title' in Rijksmuseum LOD typically defaults to Dutch.
                    # We store it but flag the language for the AI Curator to translate/enrich.
                    raw_title = item_data.get('title')

                    found.append({
                        "source_url": source_url,
                        "thumbnail_url": thumb_url,
                        "proposed_title": raw_title or 'Unknown Title',
                        "proposed_artist": artist,
                        "source_api": "Rijksmuseum",
                        "context_hints": json.dumps({
                            "source_lang": "nl",
                            "original_title": raw_title,
                            "raw_metadata": item_data
                        })
                    })
        except Exception:
            logger.error(f"[Scout] RijksmuseumScout failed: {traceback.format_exc()}")
        return found

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
                    "lang": "en",
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
                        "source_api": "Statens Museum for Kunst (Denmark)",
                        "context_hints": json.dumps(item)
                    })
                    count += 1
        except Exception:
            logger.error(f"[Scout] SmkScout failed: {traceback.format_exc()}")
        return found

class VamScout(MuseumScout):
    """
    Scout for the Victoria and Albert Museum (London).
    """
    API_URL = "https://api.vam.ac.uk/v2/objects/search"

    async def find_art(self, query: str = None) -> List[Dict]:
        q = query or "painting"
        logger.info(f"[Scout] VamScout searching for: {q}")
        found = []
        try:
            async with httpx.AsyncClient() as client:
                params = {
                    "q": q,
                    "images_exist": 1,
                    "page_size": 30,
                    "page": 1 if query else random.randint(1, 10)
                }
                response = await client.get(self.API_URL, params=params, timeout=15.0)
                if response.status_code != 200: return []
                
                data = response.json()
                records = data.get('records', [])
                count = 0
                for item in records:
                    if count >= 20: break
                    img_id = item.get('_primaryImageId')
                    if not img_id: continue
                    
                    full_res = f"https://framemark.vam.ac.uk/collections/{img_id}/full/full/0/default.jpg"
                    thumbnail = f"https://framemark.vam.ac.uk/collections/{img_id}/full/!500,500/0/default.jpg"
                    
                    artist = item.get('_primaryMaker', {}).get('name', 'Unknown Artist')
                    title = item.get('_primaryTitle') or item.get('objectType', 'Unknown')
                    
                    found.append({
                        "source_url": full_res,
                        "thumbnail_url": thumbnail,
                        "proposed_title": title,
                        "proposed_artist": artist,
                        "source_api": "Victoria & Albert Museum",
                        "context_hints": json.dumps(item)
                    })
                    count += 1
        except Exception:
            logger.error(f"[Scout] VamScout failed: {traceback.format_exc()}")
        return found



class HarvardScout(MuseumScout):
    """Tier-2 Scout for Harvard Art Museums requiring API key."""
    API_URL = "https://api.harvardartmuseums.org/object"

    def __init__(self, api_key: str = None):
        self.api_key = api_key

    async def find_art(self, query: str = None) -> List[Dict]:
        if not self.api_key: return []
        
        logger.info(f"[Scout] HarvardScout searching for: {query or 'public domain'}")
        found = []
        try:
            async with httpx.AsyncClient() as client:
                params = {
                    "apikey": self.api_key,
                    "keyword": query or "public domain",
                    "hasimage": 1,
                    "size": 20,
                    "page": 1 if query else random.randint(1, 10),
                    "fields": "id,title,people,images,century,culture,medium,dimensions,creditline"
                }
                response = await client.get(self.API_URL, params=params, timeout=15.0)
                if response.status_code != 200: return []
                
                data = response.json()
                artworks = data.get('records', [])
                for art in artworks:
                    images = art.get('images', [])
                    if not images: continue
                    img = images[0].get('baseimageurl')
                    if not img: continue
                    
                    found.append({
                        "source_url": f"{img}?width=2000&apikey={self.api_key}",
                        "thumbnail_url": f"{img}?width=400&apikey={self.api_key}",
                        "proposed_title": art.get('title') or 'Unknown',
                        "proposed_artist": art.get('people', [{}])[0].get('name', 'Unknown') if art.get('people') else 'Unknown',
                        "source_api": "Harvard Art Museums",
                        "context_hints": json.dumps(art)
                    })
        except Exception as e:
            logger.error(f"[Scout] Harvard error: {e}")
        return found
        
class SmithsonianScout(MuseumScout):
    """Tier-2 Scout for Smithsonian Open Access requiring API key."""
    API_URL = "https://api.si.edu/openaccess/api/v1.0/search"

    def __init__(self, api_key: str = None):
        self.api_key = api_key

    async def find_art(self, query: str = None) -> List[Dict]:
        if not self.api_key: return []
        
        logger.info(f"[Scout] Smithsonian searching for: {query or 'art'}")
        found = []
        try:
            async with httpx.AsyncClient() as client:
                params = {
                    "api_key": self.api_key,
                    "q": f"{query or 'art'} AND online_media_type:Images",
                    "rows": 20,
                    "start": 0 if query else random.randint(0, 100)
                }
                response = await client.get(self.API_URL, params=params, timeout=15.0)
                if response.status_code != 200: return []
                
                rows = response.json().get('response', {}).get('rows', [])
                for art in rows:
                    content = art.get('content', {})
                    descriptive = content.get('descriptiveNonRepeating', {})
                    
                    images = descriptive.get('online_media', {}).get('media', [])
                    if not images: continue
                    
                    img = [i for i in images if i.get('type') == 'Images']
                    if not img: continue
                    img_url = img[0].get('content')
                    if not img_url: continue
                    
                    freetext = content.get('freetext', {})
                    creators = freetext.get('name', [])
                    artist = creators[0].get('content', 'Unknown') if creators else 'Unknown'
                    
                    found.append({
                        "source_url": img_url,
                        "thumbnail_url": img[0].get('thumbnail', img_url),
                        "proposed_title": art.get('title') or 'Unknown Smithsonian Object',
                        "proposed_artist": artist,
                        "source_api": f"Smithsonian {descriptive.get('data_source', '')}",
                        "context_hints": json.dumps(art)
                    })
        except Exception as e:
            logger.error(f"[Scout] Smithsonian error: {e}")
        return found

class EuropeanaScout(MuseumScout):
    """Tier-2 Scout for Europeana requiring API WSKey."""
    API_URL = "https://api.europeana.eu/record/v2/search.json"

    def __init__(self, api_key: str = None):
        self.api_key = api_key

    async def find_art(self, query: str = None) -> List[Dict]:
        if not self.api_key: return []
        
        logger.info(f"[Scout] Europeana searching for: {query or 'painting'}")
        found = []
        try:
            async with httpx.AsyncClient() as client:
                params = {
                    "wskey": self.api_key,
                    "query": query or "painting OR art",
                    "qf": "TYPE:IMAGE",
                    "reusability": "open",
                    "rows": 20,
                    "media": True,
                    "start": 1 if query else random.randint(1, 10)
                }
                response = await client.get(self.API_URL, params=params, timeout=15.0)
                if response.status_code != 200: return []
                
                items = response.json().get('items', [])
                for art in items:
                    images = art.get('edmPreview', [])
                    if not images: continue
                    
                    img_url = images[0]
                    artist = 'Unknown'
                    if art.get('dcCreator'):
                        artist = art.get('dcCreator')[0]
                        
                    title = art.get('title', ['Unknown Europeana Asset'])[0]

                    found.append({
                        "source_url": img_url,
                        "thumbnail_url": img_url,
                        "proposed_title": title,
                        "proposed_artist": artist,
                        "source_api": "Europeana",
                        "context_hints": json.dumps(art)
                    })
        except Exception as e:
            logger.error(f"[Scout] Europeana error: {e}")
        return found
        
async def run_scouts(db: Session, query: str = None, sources: List[str] = None):
    """
    Runs selected active scouts and populates the DiscoveryQueue.
    """
    settings = db.query(SettingsModel).all()
    keys = {s.setting_key: s.setting_value for s in settings}

    all_scouts = {
        "chicago": ChicagoArtScout(),
        "met": MetMuseumScout(),
        "cleveland": ClevelandArtScout(),
        "rijks": RijksmuseumScout(),
        "smk": SmkScout(),
        "vam": VamScout(),
        "harvard": HarvardScout(keys.get("harvard_api_key")),
        "smithsonian": SmithsonianScout(keys.get("smithsonian_api_key")),
        "europeana": EuropeanaScout(keys.get("europeana_api_key"))
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

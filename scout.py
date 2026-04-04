"""
Modular Semantic Art Scout for Screen Docent.
Discovers new high-resolution public-domain art.

Smart Search: Uses QueryClassifier to dispatch API-specific optimized queries.
"""

import logging
import json
import httpx
import random
import traceback
import asyncio
import uuid
from abc import ABC, abstractmethod
from typing import List, Dict, Optional
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from sqlalchemy.orm import Session
from models import DiscoveryQueueModel, SettingsModel
from query_classifier import SearchIntent

logger = logging.getLogger("artwork-display-api.scout")

class MuseumScout(ABC):
    @abstractmethod
    async def find_art(self, query: str = None, intent: SearchIntent = None,
                       offset: int = 0, limit: int = 10) -> List[Dict]:
        """Returns a list of art dictionaries with source_url, thumbnail_url, etc."""
        pass


class ChicagoArtScout(MuseumScout):
    """
    Scout for the Art Institute of Chicago.
    Uses Elasticsearch DSL for targeted field queries.
    """
    API_URL = "https://api.artic.edu/api/v1/artworks/search"

    async def find_art(self, query: str = None, intent: SearchIntent = None,
                       offset: int = 0, limit: int = 10) -> List[Dict]:
        q = query or "painting"
        logger.info(f"[Scout] ChicagoArtScout searching for: {q} (intent: {intent.query_type if intent else 'none'}, offset: {offset})")
        found = []
        headers = {"User-Agent": "ScreenDocent/1.0"}

        try:
            async with httpx.AsyncClient(headers=headers) as client:
                # Build query params based on intent type
                params = {
                    "fields": "id,title,artist_title,image_id,date_display,medium_display,"
                              "classification_titles,style_titles,is_boosted,thumbnail",
                    "limit": limit,
                    "page": (offset // limit) + 1,
                }

                if intent and intent.query_type == "artist":
                    # Use canonical name for better artist matching via q param
                    # Chicago's q param naturally boosts artist_title field matches
                    params["q"] = intent.canonical_name
                    params["query[term][is_public_domain]"] = "true"
                elif intent and intent.query_type == "genre":
                    # Genre search — q param matches against style_titles, classification_titles
                    params["q"] = intent.canonical_name
                    params["query[term][is_public_domain]"] = "true"
                else:
                    # Freetext / subject: use generic q param
                    params["q"] = q
                    params["query[term][is_public_domain]"] = "true"

                response = await client.get(self.API_URL, params=params, timeout=15.0)
                if response.status_code != 200:
                    logger.error(f"[Scout] Chicago API returned {response.status_code}")
                    return []

                data = response.json()
                artworks = data.get('data', [])
                iiif_base = data.get('config', {}).get('iiif_url', 'https://www.artic.edu/iiif/2')

                for art in artworks:
                    image_id = art.get('image_id')
                    if not image_id:
                        continue

                    full_url = f"{iiif_base}/{image_id}/full/max/0/default.jpg"
                    thumb_url = f"{iiif_base}/{image_id}/full/400,/0/default.jpg"

                    found.append({
                        "source_url": full_url,
                        "thumbnail_url": thumb_url,
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
    Uses artistOrCulture and isHighlight flags for targeted queries.
    """
    SEARCH_URL = "https://collectionapi.metmuseum.org/public/collection/v1/search"
    OBJECT_URL = "https://collectionapi.metmuseum.org/public/collection/v1/objects/{id}"

    async def find_art(self, query: str = None, intent: SearchIntent = None,
                       offset: int = 0, limit: int = 10) -> List[Dict]:
        q = query or "painting"
        logger.info(f"[Scout] MetMuseumScout searching for: {q} (intent: {intent.query_type if intent else 'none'}, offset: {offset})")
        found = []
        headers = {"User-Agent": "ScreenDocent/1.0"}

        try:
            async with httpx.AsyncClient(headers=headers, timeout=30.0) as client:
                params = {"q": intent.canonical_name if intent and intent.query_type == "artist" else q,
                           "hasImages": "true"}

                if intent and intent.query_type == "artist":
                    params["artistOrCulture"] = "true"
                elif intent and intent.query_type == "genre":
                    params["isHighlight"] = "true"

                response = await client.get(self.SEARCH_URL, params=params)
                if response.status_code != 200:
                    logger.error(f"[Scout] Met search returned {response.status_code}")
                    return []

                data = response.json()
                object_ids = data.get('objectIDs') or []  # Handle null explicitly
                logger.info(f"[Scout] Met search returned {len(object_ids)} object IDs (total: {data.get('total', 0)})")

                # Fallback: if artistOrCulture returned nothing, retry without it
                if not object_ids and intent and intent.query_type == "artist":
                    logger.info("[Scout] Met: artistOrCulture returned 0, retrying broad search...")
                    fallback_params = {"q": intent.canonical_name, "hasImages": "true"}
                    response = await client.get(self.SEARCH_URL, params=fallback_params)
                    if response.status_code == 200:
                        data = response.json()
                        object_ids = data.get('objectIDs') or []
                        logger.info(f"[Scout] Met fallback returned {len(object_ids)} object IDs")

                # Fallback: if isHighlight returned nothing for genre, retry without it
                if not object_ids:
                    if intent and intent.query_type == "genre" and "isHighlight" in params:
                        logger.info("[Scout] Met: No highlights found, retrying without isHighlight...")
                        del params["isHighlight"]
                        response = await client.get(self.SEARCH_URL, params=params)
                        if response.status_code == 200:
                            data = response.json()
                            object_ids = data.get('objectIDs') or []
                    if not object_ids:
                        logger.info("[Scout] Met: No results after fallbacks")
                        return []

                # Paginate: take a slice based on offset and limit
                selected_ids = object_ids[offset:offset + limit]
                if not selected_ids:
                    return []

                # Fetch object details sequentially with small delay
                # Met API rate-limits aggressive concurrent requests
                logger.info(f"[Scout] Met: Fetching details for {len(selected_ids)} objects: {selected_ids}")
                for obj_id in selected_ids:
                    try:
                        resp = await client.get(self.OBJECT_URL.format(id=obj_id))
                        if resp.status_code == 200:
                            obj_data = resp.json()
                            img_url = obj_data.get('primaryImage')
                            if not img_url:
                                logger.debug(f"[Scout] Met object {obj_id}: no primaryImage, skipping")
                                continue

                            found.append({
                                "source_url": img_url,
                                "thumbnail_url": obj_data.get('primaryImageSmall') or img_url,
                                "proposed_title": obj_data.get('title') or 'Unknown',
                                "proposed_artist": obj_data.get('artistDisplayName') or 'Unknown Artist',
                                "source_api": "The Metropolitan Museum of Art",
                                "context_hints": json.dumps(obj_data)
                            })
                        else:
                            logger.warning(f"[Scout] Met object {obj_id}: HTTP {resp.status_code}")
                    except Exception as e:
                        logger.warning(f"[Scout] Met object {obj_id} fetch failed: {e}")
                    # Small delay to avoid rate limiting
                    await asyncio.sleep(0.2)

        except Exception:
            logger.error(f"[Scout] MetMuseumScout failed: {traceback.format_exc()}")
        logger.info(f"[Scout] Met: returning {len(found)} artworks")
        return found


class ClevelandArtScout(MuseumScout):
    """
    Scout for the Cleveland Museum of Art.
    Uses dedicated 'artists' param and 'highlight' flag.
    """
    API_URL = "https://openaccess-api.clevelandart.org/api/artworks/"

    async def find_art(self, query: str = None, intent: SearchIntent = None,
                       offset: int = 0, limit: int = 10) -> List[Dict]:
        q = query or "painting"
        logger.info(f"[Scout] ClevelandArtScout searching for: {q} (intent: {intent.query_type if intent else 'none'}, offset: {offset})")
        found = []
        headers = {"User-Agent": "ScreenDocent/1.0"}

        try:
            async with httpx.AsyncClient(headers=headers) as client:
                params = {"has_image": "1", "cc0": "1", "limit": limit, "skip": offset}

                if intent and intent.query_type == "artist":
                    # Use dedicated artists param for precise artist search
                    params["artists"] = intent.canonical_name
                elif intent and intent.query_type == "genre":
                    params["q"] = intent.canonical_name
                    params["type"] = "Painting"
                    params["highlight"] = "1"
                else:
                    params["q"] = q

                response = await client.get(self.API_URL, params=params, timeout=15.0)
                if response.status_code != 200:
                    return []

                data = response.json()
                artworks = data.get('data', [])
                for art in artworks:
                    images = art.get('images', {})
                    if not images:
                        continue
                    full_res = images.get('print', {}).get('url') or images.get('web', {}).get('url')
                    if not full_res:
                        continue
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

    async def find_art(self, query: str = None, intent: SearchIntent = None,
                       offset: int = 0, limit: int = 10) -> List[Dict]:
        q = query or "painting"
        logger.info(f"[Scout] RijksmuseumScout (Open Data) searching for: {q} (intent: {intent.query_type if intent else 'none'})")
        found = []
        try:
            async with httpx.AsyncClient(follow_redirects=True) as client:
                params = {"imageAvailable": "true"}

                if intent and intent.query_type == "artist":
                    # Use 'creator' param for artist queries — returns actual works
                    # BY the artist, not just items mentioning them (posters, etc.)
                    # creator=Vincent van Gogh → 11 works with images
                    # vs description=Vincent van Gogh → 52 items (mostly merch)
                    params["creator"] = intent.canonical_name
                else:
                    # Use description for genre/subject/freetext
                    params["description"] = q

                response = await client.get(self.SEARCH_URL, params=params, timeout=15.0)
                if response.status_code != 200:
                    logger.error(f"[Scout] Rijksmuseum search failed: {response.status_code}")
                    return []

                data = response.json()
                items = data.get('orderedItems', [])
                if not items:
                    return []

                # Apply offset and limit
                selected_items = items[offset:offset + limit]

                for item in selected_items:
                    item_url = item.get('id')
                    if not item_url:
                        continue

                    # Ensure we use the 'data' resolver directly to preserve profile params
                    item_url = item_url.replace("id.rijksmuseum.nl", "data.rijksmuseum.nl")

                    # Resolve each item using Dublin Core profile for easy metadata extraction
                    res_params = {"_profile": "dc"}
                    res_headers = {"Accept": "application/ld+json"}
                    res_resp = await client.get(item_url, params=res_params, headers=res_headers, timeout=10.0)

                    if res_resp.status_code != 200:
                        continue

                    item_data = res_resp.json()

                    # Extract IIIF image from 'relation' field
                    relation = item_data.get('relation', {})
                    img_base_url = None
                    if isinstance(relation, dict):
                        img_base_url = relation.get('@id')
                    elif isinstance(relation, list):
                        for r in relation:
                            if isinstance(r, dict) and r.get('@id'):
                                img_base_url = r.get('@id')
                                break

                    if not img_base_url:
                        continue

                    source_url = img_base_url
                    thumb_url = source_url.replace("/full/max/", "/full/400,/")

                    def extract_label(node):
                        if isinstance(node, str):
                            return node
                        if isinstance(node, dict):
                            t_val = node.get('title')
                            if isinstance(t_val, list):
                                for t in t_val:
                                    if isinstance(t, dict) and t.get('@language') == 'en':
                                        return t.get('@value')
                                if t_val:
                                    return t_val[0].get('@value') if isinstance(t_val[0], dict) else t_val[0]
                            return t_val or node.get('@id')
                        return str(node)

                    artist_node = item_data.get('creator', {})
                    artist = extract_label(artist_node) or "Unknown Artist"

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
    Post-filters artist results to ensure artist match.
    """
    API_URL = "https://api.smk.dk/api/v1/art/search/"

    async def find_art(self, query: str = None, intent: SearchIntent = None,
                       offset: int = 0, limit: int = 10) -> List[Dict]:
        q = query or "*"
        logger.info(f"[Scout] SmkScout searching for: {q} (intent: {intent.query_type if intent else 'none'}, offset: {offset})")
        found = []
        try:
            async with httpx.AsyncClient() as client:
                params = {
                    "keys": intent.canonical_name if intent and intent.query_type == "artist" else q,
                    "filters": "[has_image:true],[public_domain:true]",
                    "lang": "en",
                    "rows": limit * 3 if intent and intent.query_type == "artist" else limit,
                    "offset": offset,
                }
                response = await client.get(self.API_URL, params=params, timeout=15.0)
                if response.status_code != 200:
                    return []

                data = response.json()
                items = data.get('items', [])
                count = 0
                for item in items:
                    if count >= limit:
                        break
                    image_url = item.get('image_native')
                    if not image_url:
                        iiif_id = item.get('image_iiif_id')
                        if iiif_id:
                            image_url = f"https://iip.smk.dk/iiif/jp2/{iiif_id}/full/max/0/default.jpg"
                    if not image_url:
                        continue

                    artist = "Unknown Artist"
                    production = item.get('production', [])
                    if production and isinstance(production, list):
                        artist = production[0].get('creator', artist)

                    # Post-filter: for artist queries, ensure artist matches
                    if intent and intent.query_type == "artist":
                        artist_lower = artist.lower()
                        query_lower = intent.original_query.lower()
                        # Check if query terms appear in artist name
                        if not any(term in artist_lower for term in query_lower.split()):
                            continue

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


# ---------------------------------------------------------------------------
# Premium (Tier-2) Scouts — Unchanged for now, future optimization pass
# ---------------------------------------------------------------------------

class HarvardScout(MuseumScout):
    """Tier-2 Scout for Harvard Art Museums requiring API key."""
    API_URL = "https://api.harvardartmuseums.org/object"

    def __init__(self, api_key: str = None):
        self.api_key = api_key

    async def find_art(self, query: str = None, intent: SearchIntent = None,
                       offset: int = 0, limit: int = 10) -> List[Dict]:
        if not self.api_key:
            return []

        logger.info(f"[Scout] HarvardScout searching for: {query or 'public domain'}")
        found = []
        try:
            async with httpx.AsyncClient() as client:
                params = {
                    "apikey": self.api_key,
                    "keyword": query or "public domain",
                    "hasimage": 1,
                    "size": limit,
                    "page": (offset // limit) + 1 if query else random.randint(1, 10),
                    "fields": "id,title,people,images,century,culture,medium,dimensions,creditline"
                }
                response = await client.get(self.API_URL, params=params, timeout=15.0)
                if response.status_code != 200:
                    return []

                data = response.json()
                artworks = data.get('records', [])
                for art in artworks:
                    images = art.get('images', [])
                    if not images:
                        continue
                    img = images[0].get('baseimageurl')
                    if not img:
                        continue

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

    async def find_art(self, query: str = None, intent: SearchIntent = None,
                       offset: int = 0, limit: int = 10) -> List[Dict]:
        if not self.api_key:
            return []

        logger.info(f"[Scout] Smithsonian searching for: {query or 'art'}")
        found = []
        try:
            async with httpx.AsyncClient() as client:
                params = {
                    "api_key": self.api_key,
                    "q": f"{query or 'art'} AND online_media_type:Images",
                    "rows": limit,
                    "start": offset if query else random.randint(0, 100)
                }
                response = await client.get(self.API_URL, params=params, timeout=15.0)
                if response.status_code != 200:
                    return []

                rows = response.json().get('response', {}).get('rows', [])
                for art in rows:
                    content = art.get('content', {})
                    descriptive = content.get('descriptiveNonRepeating', {})

                    images = descriptive.get('online_media', {}).get('media', [])
                    if not images:
                        continue

                    img = [i for i in images if i.get('type') == 'Images']
                    if not img:
                        continue
                    img_url = img[0].get('content')
                    if not img_url:
                        continue

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
    """
    Tier-2 Scout for Europeana requiring API WSKey.
    
    Quality filters:
    - contentTier:3/4 — high-quality records with good images
    - IMAGE_SIZE:large/extra_large — decent resolution images
    - what:painting — restricts to paintings for artist searches
    - profile=rich — returns fuller metadata
    """
    API_URL = "https://api.europeana.eu/record/v2/search.json"

    def __init__(self, api_key: str = None):
        self.api_key = api_key

    async def find_art(self, query: str = None, intent: SearchIntent = None,
                       offset: int = 0, limit: int = 10) -> List[Dict]:
        if not self.api_key:
            return []

        q = query or "painting"
        logger.info(f"[Scout] Europeana searching for: {q} (intent: {intent.query_type if intent else 'none'}, offset: {offset})")
        found = []
        try:
            async with httpx.AsyncClient() as client:
                # Base params shared across all strategies
                base_params = {
                    "wskey": self.api_key,
                    "reusability": "open",
                    "rows": limit,
                    "media": True,
                    "start": offset + 1,
                    "profile": "rich",
                }

                # Strategy: progressive fallback for best quality results
                # For artists: who: field returns actual works BY the artist
                # For genres: text search + TYPE:IMAGE 
                if intent and intent.query_type == "artist":
                    strategies = [
                        # 1st: who: field — small but precise (genuine works)
                        {
                            "query": f'who:"{intent.canonical_name}"',
                            "qf": ["TYPE:IMAGE"],
                        },
                        # 2nd: text + painting — broader, includes glass slides of paintings
                        {
                            "query": f'"{intent.canonical_name}" painting',
                            "qf": ["TYPE:IMAGE"],
                        },
                    ]
                else:
                    strategies = [
                        {
                            "query": q,
                            "qf": ["TYPE:IMAGE"],
                        },
                    ]

                items = []
                for strategy in strategies:
                    params = {
                        **base_params,
                        "query": strategy["query"],
                        "qf": strategy["qf"],
                    }
                    response = await client.get(self.API_URL, params=params, timeout=15.0)
                    if response.status_code != 200:
                        logger.warning(f"[Scout] Europeana returned {response.status_code} for query: {strategy['query']}")
                        continue

                    data = response.json()
                    items = data.get('items', [])
                    total = data.get('totalResults', 0)
                    logger.info(f"[Scout] Europeana strategy '{strategy['query']}' → {len(items)} items (total: {total})")
                    if items:
                        break  # Got results, stop trying fallbacks

                if not items:
                    logger.info("[Scout] Europeana: no results from any strategy")
                    return []

                for art in items:
                    # Prefer edmIsShownBy (full res) over edmPreview (thumbnail)
                    full_url = None
                    if art.get('edmIsShownBy'):
                        full_url = art['edmIsShownBy'][0]
                    
                    thumb_url = None
                    if art.get('edmPreview'):
                        thumb_url = art['edmPreview'][0]
                    
                    if not full_url and not thumb_url:
                        continue

                    img_url = full_url or thumb_url

                    artist = 'Unknown'
                    if art.get('dcCreator'):
                        artist = art['dcCreator'][0]
                    elif art.get('dcContributor'):
                        artist = art['dcContributor'][0]

                    title = art.get('title', ['Unknown Europeana Asset'])[0]

                    found.append({
                        "source_url": img_url,
                        "thumbnail_url": thumb_url or img_url,
                        "proposed_title": title,
                        "proposed_artist": artist,
                        "source_api": "Europeana",
                        "context_hints": json.dumps({
                            "provider": (art.get('dataProvider') or ['Unknown'])[0],
                            "country": (art.get('country') or ['Unknown'])[0],
                            "year": (art.get('year') or ['Unknown'])[0],
                            "rights": (art.get('rights') or ['Unknown'])[0],
                            "edmIsShownAt": (art.get('edmIsShownAt') or [None])[0],
                        })
                    })
        except Exception as e:
            logger.error(f"[Scout] Europeana error: {e}", exc_info=True)
        logger.info(f"[Scout] Europeana returning {len(found)} results")
        return found


# ---------------------------------------------------------------------------
# Search Session State — In-memory store for "Load More" pagination
# ---------------------------------------------------------------------------

@dataclass
class SearchSession:
    """Tracks state for a paginated search session."""
    session_id: str
    query: str
    intent: SearchIntent
    sources: List[str]
    offset: int = 0
    limit: int = 10
    created_at: datetime = field(default_factory=datetime.utcnow)

# In-memory session store — survives as long as the server runs
_search_sessions: Dict[str, SearchSession] = {}
SESSION_TTL_MINUTES = 30


def create_search_session(query: str, intent: SearchIntent, sources: List[str], limit: int = 10) -> SearchSession:
    """Create a new search session and return it."""
    _cleanup_expired_sessions()
    session = SearchSession(
        session_id=str(uuid.uuid4()),
        query=query,
        intent=intent,
        sources=sources,
        offset=0,
        limit=limit,
    )
    _search_sessions[session.session_id] = session
    logger.info(f"[Session] Created search session {session.session_id[:8]}... "
                f"(query='{query}', sources={sources}, limit={limit})")
    return session


def get_search_session(session_id: str) -> Optional[SearchSession]:
    """Retrieve an active session by ID, or None if expired/not found."""
    _cleanup_expired_sessions()
    return _search_sessions.get(session_id)


def _cleanup_expired_sessions():
    """Remove sessions older than TTL."""
    now = datetime.utcnow()
    expired = [
        sid for sid, session in _search_sessions.items()
        if (now - session.created_at) > timedelta(minutes=SESSION_TTL_MINUTES)
    ]
    for sid in expired:
        del _search_sessions[sid]
        logger.info(f"[Session] Expired session {sid[:8]}...")


# ---------------------------------------------------------------------------
# Scout Dispatcher
# ---------------------------------------------------------------------------

async def run_scouts(db: Session, query: str = None, sources: List[str] = None,
                     intent: SearchIntent = None, offset: int = 0, limit: int = 10) -> List[Dict]:
    """
    Runs selected active scouts and returns results (without inserting into DB).
    The caller is responsible for insertion.
    """
    settings = db.query(SettingsModel).all()
    keys = {s.setting_key: s.setting_value for s in settings}

    all_scouts = {
        "chicago": ChicagoArtScout(),
        "met": MetMuseumScout(),
        "cleveland": ClevelandArtScout(),
        "rijks": RijksmuseumScout(),
        "smk": SmkScout(),
        "harvard": HarvardScout(keys.get("harvard_api_key")),
        "smithsonian": SmithsonianScout(keys.get("smithsonian_api_key")),
        "europeana": EuropeanaScout(keys.get("europeana_api_key"))
    }

    active_scouts = []
    if sources:
        for s in sources:
            if s in all_scouts:
                active_scouts.append(all_scouts[s])
    else:
        active_scouts = list(all_scouts.values())

    if not active_scouts:
        return []

    tasks = [scout.find_art(query=query, intent=intent, offset=offset, limit=limit) for scout in active_scouts]
    results_lists = await asyncio.gather(*tasks)

    # Flatten results
    all_results = []
    for results in results_lists:
        all_results.extend(results)

    logger.info(f"[Scout] Scouts returned {len(all_results)} total items across {len(active_scouts)} sources.")
    return all_results

"""
Source adapters for the catalog builder. Each returns a list of *normalized* catalog items:

    {title, agent_name, agent_role, creation_date, cultural_context, medium, date_display,
     description_narrative, tags, source, license, source_url, thumbnail_url}

Museums reuse the app's existing scouts (run_scouts) so license/PD filtering and URL construction
match the live discovery pipeline. NASA / Library of Congress / Wikimedia are net-new fetchers.
"""

import asyncio
import difflib
import json
import logging
import re
import time
from urllib.parse import quote

import httpx

from scout import run_scouts
from query_classifier import QueryClassifier

logger = logging.getLogger("catalog-builder.sources")

UA = {"User-Agent": "ScreenDocent-CatalogBuilder/1.0 (https://github.com/AiwendilInTheWoods/Screen-Docent)"}
MUSEUM_SOURCES = {"chicago", "met", "cleveland", "rijks", "smk"}
# A catalog image must be at least this many pixels on its long edge to look good on big (up-to-4K)
# displays. Used both to pre-gate Wikimedia originals and to gate downloaded source images.
MIN_DISPLAY_EDGE = 2000
_classifier = QueryClassifier()


def _norm(**kw) -> dict:
    item = {
        "title": "Untitled", "agent_name": "Unknown Artist", "agent_role": "Artist",
        "creation_date": "", "cultural_context": "", "medium": "", "date_display": "",
        "description_narrative": "", "tags": "",
        "source": "", "license": "Public Domain", "source_url": "", "thumbnail_url": "",
    }
    item.update({k: v for k, v in kw.items() if v is not None})
    for k in ("source_url", "thumbnail_url"):
        v = item.get(k) or ""
        if v.startswith("//"):
            item[k] = "https:" + v
    return item


def _extract_museum_meta(raw: dict) -> dict:
    """Best-effort date/medium/culture from a scout's context_hints (keys differ per museum)."""
    def first(*keys):
        for k in keys:
            v = raw.get(k)
            if isinstance(v, dict):
                v = v.get("text") or v.get("title") or v.get("presentationDate")
            if isinstance(v, list):
                v = ", ".join(str(x) for x in v[:2]) if v else ""
            if v:
                return str(v)
        return ""
    return {
        "creation_date": first("date_display", "objectDate", "creation_date", "dated", "production_date"),
        "date_display": first("date_display", "objectDate", "dated"),
        "medium": first("medium_display", "medium", "technique", "materials_techniques", "physical_medium"),
        "cultural_context": first("place_of_origin", "culture", "cultural_context", "style_title", "classification"),
    }


def _from_scout_result(r: dict):
    try:
        raw = json.loads(r.get("context_hints") or "{}")
    except Exception:
        raw = {}
    source_api = r.get("source_api", "")
    # Met returns a mixed collection — keep only public-domain objects. Other keyless scouts
    # (Chicago/Cleveland/SMK/Rijks) already filter PD at the API level.
    if "Metropolitan" in source_api and not raw.get("isPublicDomain", False):
        return None
    src_url = r.get("source_url")
    if not src_url:
        return None
    meta = _extract_museum_meta(raw)
    return _norm(
        title=r.get("proposed_title") or raw.get("title") or "Untitled",
        agent_name=r.get("proposed_artist") or "Unknown Artist",
        source=source_api or "Museum",
        license="Public Domain",
        source_url=src_url,
        thumbnail_url=r.get("thumbnail_url") or src_url,
        **meta,
    )


async def from_museums(db, sources, queries, per_query=10) -> list:
    keyless = [s for s in sources if s in MUSEUM_SOURCES]
    out = []
    for q in queries:
        intent = _classifier.classify(q)
        try:
            results = await run_scouts(db, query=q, sources=keyless, intent=intent, limit=per_query)
        except Exception as e:
            logger.warning(f"[museums] '{q}' failed: {e}")
            continue
        for r in results:
            it = _from_scout_result(r)
            if it:
                out.append(it)
    return out


async def _nasa_best_asset(client, nasa_id: str):
    """Resolve the real full-resolution image from a NASA asset manifest (prefer ~orig/~large jpg).
    The search 'links' only give a thumbnail, and naive ~orig.jpg guessing often 404s."""
    try:
        r = await client.get(f"https://images-assets.nasa.gov/image/{nasa_id}/collection.json", timeout=20.0)
        if r.status_code != 200:
            return None
        urls = [u for u in r.json() if isinstance(u, str) and u.lower().endswith((".jpg", ".jpeg", ".png"))]
    except Exception:
        return None
    if not urls:
        return None
    for suffix in ("~orig.jpg", "~orig.jpeg", "~orig.png", "~large.jpg", "~medium.jpg"):
        for u in urls:
            if u.lower().endswith(suffix):
                return u
    return urls[-1]

async def from_nasa(queries, per_query=10) -> list:
    out = []
    async with httpx.AsyncClient(headers=UA, timeout=30.0) as client:
        for q in queries:
            try:
                r = await client.get("https://images-api.nasa.gov/search",
                                     params={"q": q, "media_type": "image"})
                if r.status_code != 200:
                    continue
                items = r.json().get("collection", {}).get("items", [])
            except Exception as e:
                logger.warning(f"[nasa] '{q}' failed: {e}")
                continue
            for it in items[:per_query]:
                d = (it.get("data") or [{}])[0]
                links = it.get("links") or []
                thumb = links[0].get("href") if links else None
                if not thumb:
                    continue
                full = await _nasa_best_asset(client, d.get("nasa_id", "")) or thumb
                created = (d.get("date_created") or "")
                out.append(_norm(
                    title=d.get("title") or "Untitled",
                    agent_name=f"NASA — {d.get('center')}" if d.get("center") else "NASA",
                    agent_role="Photograph",
                    creation_date=created[:10],
                    date_display=created[:4],
                    medium="Photograph",
                    cultural_context="Spaceflight / Astronomy",
                    description_narrative=(d.get("description") or "")[:600],
                    source="NASA",
                    license="Public Domain (NASA)",
                    source_url=full,
                    thumbnail_url=thumb,
                ))
    return out


def _loc_creator(res: dict) -> str:
    c = res.get("contributor") or res.get("creator") or []
    if isinstance(c, list) and c:
        return str(c[0]).title()
    if isinstance(c, str) and c:
        return c.title()
    return "Unknown"


# LoC search mixes real digitized items with essays, blog posts, and collection landing pages.
# These title phrases and host paths mark the non-artwork "web page" records to drop.
_LOC_JUNK_TITLE = re.compile(
    r"articles and essays|collection highlights|finding images|interview with|"
    r"virtual orientation|free to use and reuse|do the talking|webcast|web guide",
    re.I,
)

def _loc_best_asset(imgs):
    """Return (full, thumb) from the real digitized assets only (tile.loc.gov storage-services).
    Everything else LoC lists (static SVG icons, /static collection thumbnails, blog/guide images)
    is a web page, not the item — return (None, None) so the caller skips it."""
    tiles = [u for u in imgs if "tile.loc.gov/storage-services/service" in u]
    if not tiles:
        return None, None
    small = [u for u in tiles if "_150px" in u or "150px" in u]
    big = [u for u in tiles if u not in small]
    full = (big or tiles)[-1]
    thumb = (small or big or tiles)[0]
    return full, thumb

async def from_loc(queries, per_query=10) -> list:
    out = []
    async with httpx.AsyncClient(headers=UA, timeout=30.0) as client:
        for q in queries:
            try:
                r = await client.get("https://www.loc.gov/search/",
                                     params={"q": q, "fo": "json", "c": per_query, "at": "results"})
                if r.status_code != 200:
                    continue
                results = r.json().get("results", [])
            except Exception as e:
                logger.warning(f"[loc] '{q}' failed: {e}")
                continue
            for res in results[:per_query]:
                title = res.get("title") or "Untitled"
                if _LOC_JUNK_TITLE.search(title):
                    continue
                full, thumb = _loc_best_asset(res.get("image_url") or [])
                if not full:
                    continue
                date = str(res.get("date") or "")
                out.append(_norm(
                    title=title,
                    agent_name=_loc_creator(res),
                    agent_role="Poster / Photograph",
                    creation_date=date,
                    date_display=date[:4],
                    medium="Poster / Photograph",
                    cultural_context="Library of Congress",
                    source="Library of Congress",
                    license="Public Domain (Library of Congress; no known restrictions)",
                    source_url=full,
                    thumbnail_url=thumb,
                ))
    return out


def _wikimedia_filepath(filename: str, width: int) -> str:
    return f"https://commons.wikimedia.org/wiki/Special:FilePath/{quote(filename)}?width={width}"


def _title_from_filename(fn: str) -> str:
    stem = fn.rsplit(".", 1)[0]
    return stem.replace("_", " ").strip()


async def from_wikimedia(files) -> list:
    out = []
    for fn in files:
        title = _title_from_filename(fn)
        artist = "Alphonse Mucha" if "Mucha" in fn else ("Henri de Toulouse-Lautrec" if "Toulouse-Lautrec" in fn else "Unknown")
        out.append(_norm(
            title=title,
            agent_name=artist,
            agent_role="Lithograph / Poster",
            medium="Color lithograph",
            cultural_context="Art Nouveau",
            source="Wikimedia Commons",
            license="Public Domain",
            source_url=_wikimedia_filepath(fn, 2000),
            thumbnail_url=_wikimedia_filepath(fn, 600),
        ))
    return out


# --------------------------------------------------------------------------- #
# Single-work resolvers — used by the curated "must-see" picks pipeline.
# --------------------------------------------------------------------------- #
_TAG_RE = re.compile(r"<[^>]+>")

def _strip_html(s):
    return _TAG_RE.sub("", s or "").strip()

def _ratio(a, b):
    return difflib.SequenceMatcher(None, (a or "").lower(), (b or "").lower()).ratio()

def _wm_match(fname: str, title: str, artist: str = "") -> float:
    """Score a Commons filename against a wanted work. Commons filenames embed artist/date
    (e.g. 'Mucha-Job-1896.jpg'), so a plain ratio underrates short titles like 'Job' — instead
    reward title-token containment, then nudge for the artist surname."""
    f = re.sub(r"[_\-]+", " ", fname.rsplit(".", 1)[0]).lower()
    toks = [w for w in re.findall(r"[a-z0-9]+", title.lower()) if len(w) > 2]
    if toks and all(t in f for t in toks):
        score = 0.9
    else:
        present = (sum(1 for t in toks if t in f) / len(toks)) if toks else 0.0
        score = max(present, _ratio(f, title))
    if artist:
        surname = artist.lower().split()[-1]
        if len(surname) > 2 and surname in f:
            score = min(1.0, score + 0.15)
    return score

# Polite Wikimedia rate limiting: one request at a time, spaced out, descriptive UA.
_wm_lock = asyncio.Lock()
_wm_last = [0.0]
WM_MIN_INTERVAL = 1.2

async def _wm_throttle():
    async with _wm_lock:
        wait = WM_MIN_INTERVAL - (time.monotonic() - _wm_last[0])
        if wait > 0:
            await asyncio.sleep(wait)
        _wm_last[0] = time.monotonic()

def _wm_is_pd(extmeta: dict) -> bool:
    parts = []
    for k in ("LicenseShortName", "License", "UsageTerms"):
        v = (extmeta.get(k) or {}).get("value")
        if v:
            parts.append(str(v).lower())
    blob = " ".join(parts)
    if "public domain" in blob or "cc0" in blob or "pd-" in blob:
        return True
    if (extmeta.get("Copyrighted") or {}).get("value", "").strip().lower() in ("false", "no"):
        return True
    return False

async def resolve_wikimedia(title: str, artist: str = ""):
    """Find a public-domain Wikimedia Commons image for a specific work. URLs are built via
    Special:FilePath at display sizes (not the raw original, which can be huge). Returns a
    normalized item or None."""
    query = f"{title} {artist}".strip()
    params = {
        "action": "query", "format": "json", "generator": "search",
        "gsrsearch": query, "gsrnamespace": "6", "gsrlimit": "10",
        "prop": "imageinfo", "iiprop": "url|mime|size|extmetadata",
    }
    await _wm_throttle()
    try:
        async with httpx.AsyncClient(headers=UA, timeout=30.0) as client:
            r = None
            for attempt in range(3):
                r = await client.get("https://commons.wikimedia.org/w/api.php", params=params)
                if r.status_code == 429:
                    await asyncio.sleep(3 * (attempt + 1)); continue
                break
            if not r or r.status_code != 200:
                return None
            pages = (r.json().get("query") or {}).get("pages") or {}
    except Exception as e:
        logger.warning(f"[wikimedia] '{query}' failed: {e}")
        return None

    best, best_score = None, 0.0
    for p in pages.values():
        ii = (p.get("imageinfo") or [{}])[0]
        if not ii.get("mime", "").startswith("image/") or "svg" in ii.get("mime", ""):
            continue
        # Resolution gate: the original must be large enough to look good on up-to-4K displays.
        if max(ii.get("width") or 0, ii.get("height") or 0) < MIN_DISPLAY_EDGE:
            continue
        if not _wm_is_pd(ii.get("extmetadata") or {}):
            continue
        page_title = p.get("title", "")  # "File:Foo.jpg"
        s = _wm_match(page_title.replace("File:", ""), title, artist)
        if s > best_score:
            best, best_score = (page_title, ii), s
    if not best or best_score < 0.5:
        return None

    fname = best[0].split("File:", 1)[-1]
    artist_meta = _strip_html(((best[1].get("extmetadata") or {}).get("Artist") or {}).get("value", ""))
    return _norm(
        title=title,
        agent_name=artist or artist_meta or "Unknown",
        source="Wikimedia Commons",
        license="Public Domain",
        source_url=_wikimedia_filepath(fname, 3840),   # serve up to 4K (capped at the original)
        thumbnail_url=_wikimedia_filepath(fname, 600),
    )

async def resolve_nasa(title: str):
    items = await from_nasa([title], per_query=3)
    return max(items, key=lambda it: _ratio(it["title"], title)) if items else None

async def resolve_museums(db, title: str, artist: str, sources):
    items = await from_museums(db, sources, [f"{title} {artist}".strip()], per_query=6)
    items = [it for it in items if _ratio(it["title"], title) >= 0.4]
    return max(items, key=lambda it: _ratio(it["title"], title)) if items else None


async def fetch_collection(db, spec, per_query=None) -> list:
    """Dispatch a collection spec to its sources and return the combined normalized candidates."""
    sources = spec.get("sources", [])
    queries = spec.get("queries", [])
    pq = per_query or max(6, (spec.get("target", 20) // max(1, len(queries) or 1)) + 4)
    items = []
    if any(s in MUSEUM_SOURCES for s in sources) and queries:
        items += await from_museums(db, sources, queries, pq)
    if "nasa" in sources and queries:
        items += await from_nasa(queries, pq)
    if "loc" in sources and queries:
        items += await from_loc(queries, pq)
    if "wikimedia" in sources and spec.get("files"):
        items += await from_wikimedia(spec["files"])
    return items

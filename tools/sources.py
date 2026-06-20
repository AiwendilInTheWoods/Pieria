"""
Source adapters for the catalog builder. Each returns a list of *normalized* catalog items:

    {title, agent_name, agent_role, creation_date, cultural_context, medium, date_display,
     description_narrative, tags, source, license, source_url, thumbnail_url}

Museums reuse the app's existing scouts (run_scouts) so license/PD filtering and URL construction
match the live discovery pipeline. NASA / Library of Congress / Wikimedia are net-new fetchers.
"""

import json
import logging
from urllib.parse import quote

import httpx

from scout import run_scouts
from query_classifier import QueryClassifier

logger = logging.getLogger("catalog-builder.sources")

UA = {"User-Agent": "ScreenDocent-CatalogBuilder/1.0 (https://github.com/AiwendilInTheWoods/Screen-Docent)"}
MUSEUM_SOURCES = {"chicago", "met", "cleveland", "rijks", "smk"}
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
                # Derive the original asset from the thumbnail URL (most reliable).
                full = thumb.replace("~thumb.jpg", "~orig.jpg") if "~thumb" in thumb else thumb
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
                imgs = res.get("image_url") or []
                if not imgs:
                    continue
                # Prefer a sizeable image; LoC lists progressively larger URLs.
                thumb, full = imgs[0], imgs[-1]
                date = str(res.get("date") or "")
                out.append(_norm(
                    title=res.get("title") or "Untitled",
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

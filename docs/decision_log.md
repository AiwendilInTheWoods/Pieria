# Screen Docent — Decision Log

> Architecture Decision Records (ADRs) for major technical decisions made during development.

---

## ADR-001: Git-Based Docker Pipeline for Zero-Touch Deployments

**Date:** Phase 4–5 transition  
**Status:** ✅ Accepted & Implemented  
**Deciders:** Josh  

### Context

Early deployments to the MS-01 home server used `rsync` to sync the local development directory to the server, followed by manual restarts of Uvicorn. This was fragile:

- Partial syncs caused broken deployments when the transfer was interrupted.
- File permission mismatches between the dev machine and server caused silent failures.
- No rollback mechanism — a bad push required manual file restoration.
- Environment-specific `.env` files were at risk of being overwritten.

### Decision

Replace `rsync`-based deployments with a **Git + Docker Compose pipeline**:

1. The MS-01 server runs `git pull` from the repository to get the latest code.
2. `docker compose build` creates a fresh image from the `Dockerfile`.
3. `docker compose up -d` restarts the service with the new image.
4. Volume mounts for `./Artwork` and `./data` ensure media and database persistence survive container rebuilds.

### Consequences

- **Positive:** Atomic deployments — the entire application state is defined by a single Git commit. Rollback is `git checkout <sha> && docker compose up -d --build`.
- **Positive:** `.env` on the server is never tracked by Git, so secrets remain local to the machine.
- **Positive:** Dockerfile layer caching means `requirements.txt` changes only rebuild the dependency layer, not the entire image.
- **Negative:** Requires Git to be installed on the MS-01 server (trivial).
- **Negative:** Large binary assets in `Artwork/` must be excluded from the Git repo via `.gitignore` instead of `.dockerignore` alone.

---

## ADR-002: Volume-Mapping `/data` Directory Instead of the `.db` File

**Date:** Phase 4 (Docker stabilisation)  
**Status:** ✅ Accepted & Implemented  
**Deciders:** Josh  

### Context

The initial `docker-compose.yml` mounted the SQLite database file directly:

```yaml
volumes:
  - ./artwork.db:/app/artwork.db
```

This caused a critical data corruption bug. SQLite uses journal files (`.db-journal` or `-wal`/`-shm` in WAL mode) that must live alongside the main database file. When Docker bind-mounts a single file:

- The container sees the file at a specific inode.
- If `docker compose down && up` recreates the container, Docker may create a *new* inode for the mount, but SQLite's journal still references the old one.
- Write operations silently corrupt the database, or SQLite refuses to acquire a lock entirely.

### Decision

Mount the **entire directory** containing the database, not the file itself:

```yaml
volumes:
  - ./data:/app/data
```

The application's `database.py` was updated to point to `sqlite:///./data/artwork.db`, and a pre-flight `os.makedirs("./data", exist_ok=True)` ensures the directory exists on first boot.

### Consequences

- **Positive:** SQLite journal files (`.db-journal`, `.db-wal`, `.db-shm`) are always co-located with the main database file inside the same mounted directory.
- **Positive:** Database survives container rebuilds, restarts, and image upgrades without corruption.
- **Positive:** The `data/` directory can be backed up as a single unit.
- **Negative:** The old `artwork.db` in the project root became an orphan and needed manual migration to `data/artwork.db`.

---

## ADR-003: Resolving the TTFB Bottleneck with Uvicorn Workers and ASGI StaticFiles

**Date:** Phase 5  
**Status:** ✅ Accepted & Implemented  
**Deciders:** Josh  

### Context

After deploying Phase 4 to the MS-01, the Admin dashboard exhibited severe latency: thumbnail grids took 8–12 seconds to fully render, and the Canvas TV display occasionally showed blank frames during crossfades. Browser DevTools revealed:

1. **TTFB (Time to First Byte)** for thumbnail requests (`/artworks/{id}/thumbnail`) was averaging ~2s per image.
2. The Pillow-based thumbnail endpoint (`get_optimized_image()`) was CPU-bound — it opened the full-resolution image, resized it, and returned a `Response` with JPEG bytes, all on the single Uvicorn worker's event loop.
3. With 30+ thumbnails loading concurrently on the Admin page, the single worker serialised all image processing, creating a cascading queue.
4. Full-resolution artwork serving also used `FileResponse` in early iterations, which read and streamed the file synchronously.

### Decision

A three-pronged fix:

#### 1. Increase Uvicorn Workers to 4
The `Dockerfile` CMD was changed from:
```
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000"]
```
to:
```
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "4"]
```
This allows 4 independent processes to handle requests concurrently. Pillow operations in one worker no longer block API requests handled by another.

#### 2. ASGI StaticFiles for Artwork Serving
Full-resolution artwork is now served via FastAPI's `StaticFiles` mount:
```python
app.mount("/media", StaticFiles(directory=str(ARTWORK_ROOT)), name="media")
```
The Canvas client requests images at `/media/_Library/{filename}` instead of hitting a custom endpoint. `StaticFiles` handles efficient file serving, range requests, and concurrent access natively within the ASGI framework.

#### 3. Aggressive Cache-Control Headers via Middleware
A custom middleware injects `Cache-Control: public, max-age=31536000, immutable` on:
- Thumbnail routes (`/artworks/*/thumbnail`)
- Preview routes (`/artworks/*/preview`)
- All static assets (`/static/*`)

This ensures browsers and intermediate caches never re-request an image that hasn't changed, eliminating redundant TTFB entirely on subsequent visits.

#### 4. LRU Cache on Pillow Thumbnails
The `get_optimized_image()` function was wrapped with `@lru_cache(maxsize=256)`. Once a thumbnail is generated, subsequent requests for the same image + size return the cached bytes without touching Pillow or the filesystem.

### Consequences

- **Positive:** Admin dashboard thumbnail grid now loads in under 1 second on the MS-01.
- **Positive:** Canvas TV crossfades are seamless — the next image is pre-cached by the browser from the `StaticFiles` mount.
- **Positive:** Uvicorn workers distribute AI pipeline background tasks more evenly.
- **Negative:** 4 workers × SQLAlchemy sessions means SQLite's write lock can become a bottleneck under heavy concurrent writes (mitigated by `check_same_thread=False` and the single-user nature of the deployment).
- **Negative:** `@lru_cache` on `get_optimized_image` uses `Path` objects as keys, which works but means the cache is per-worker (not shared across the 4 Uvicorn processes).

---

## ADR-004: Query Classification with Progressive API Fallback

**Date:** 2026-04-04  
**Status:** ✅ Accepted & Implemented  
**Deciders:** Josh  

### Context

The Art Scout discovery system originally sent the user's raw query string directly to all museum APIs. This caused poor results:

- Searching "Van Gogh" sent a 2-word string instead of the canonical "Vincent van Gogh", reducing result counts by 90%+ on some APIs.
- Each museum API has different field names for artist filtering (`q=`, `artists=`, `creator=`, `who:`, `keys=`), but the scout used generic text search for all.
- Some APIs (like Met's `artistOrCulture`) worked in browsers but silently returned 0 results from server-side httpx calls.

### Decision

Implement a **hybrid query classifier** (`query_classifier.py`) with a **per-API progressive fallback strategy**:

1. **Classification Layer:** A `QueryClassifier` maps raw user input to a structured `SearchIntent` (query_type, canonical_name, era_hint) using:
   - Local dictionary (~200 artists, ~50 genres) for instant classification
   - Gemini Flash fallback for ambiguous queries

2. **Per-API Parameter Mapping:** Each scout translates the `SearchIntent` into API-specific parameters:
   - Chicago: `q=` + artist boosting
   - Met: `q=canonical_name` with `artistOrCulture=true`, fallback without it
   - Cleveland: `artists=canonical_name`
   - Rijksmuseum: `creator=canonical_name` + `imageAvailable=true`
   - Europeana: `who:"canonical_name"` with text fallback
   - SMK: `keys=canonical_name`

3. **Progressive Fallback:** Each scout tries its most precise query first. If it returns 0 results, it automatically retries with a broader query (e.g., Met drops `artistOrCulture`, Europeana falls back from `who:` to text search).

4. **Result Ranking:** A `ResultRanker` scores results on artist match (30pts), title relevance (20pts), highlight status (20pts), image quality (15pts), and metadata completeness (15pts), then deduplicates across sources.

### Consequences

- **Positive:** "Van Gogh" search now returns relevant paintings from all museums instead of merchandise, posters, and unrelated items.
- **Positive:** Progressive fallback ensures results always come through even when an API's preferred field is unreliable.
- **Positive:** The classifier dictionary provides instant classification for common queries; Gemini Flash handles the long tail.
- **Negative:** The dictionary must be manually maintained. New artists/genres require adding entries.
- **Negative:** The Met API's `artistOrCulture` unreliability from server-side clients remains unexplained — the fallback works around it.

---

## ADR-005: Tiered Cache-Control Strategy

**Date:** 2026-04-04  
**Status:** ✅ Accepted & Implemented  
**Deciders:** Josh  

### Context

The original cache middleware applied `Cache-Control: public, max-age=31536000, immutable` to all static assets including JS, CSS, and JSON files. This caused:

- Admin dashboard buttons becoming unresponsive after code changes (browser served stale JS from immutable cache)
- API responses being cached, causing the discovery queue to show outdated data
- External thumbnail URLs being cached by the browser, showing stale images for new search results

### Decision

Implement a **three-tier caching strategy** in the HTTP middleware:

| Tier | Paths | Cache-Control | Rationale |
|------|-------|--------------|----------|
| **API** | `/api/*` | `no-store, no-cache, must-revalidate` | Data changes constantly during search/approve/reject cycles |
| **Media** | `/artworks/*/thumbnail`, `/media/*`, `*.jpg/png/webp/svg` | `public, max-age=31536000, immutable` | Artwork images are content-addressed and rarely change |
| **Code** | `*.js`, `*.css`, `*.json` | `public, max-age=60, must-revalidate` | Allows rapid iteration during development |

Additionally, the discovery grid thumbnails append `?_cb={item_id}` as a cache-buster to external thumbnail URLs, preventing the browser from serving stale images when the same external API proxy URL appears across different search sessions.

### Consequences

- **Positive:** Code changes take effect within 60 seconds without requiring hard refresh.
- **Positive:** API responses are always fresh — no more stale queue data.
- **Positive:** External thumbnails always show the correct image for each discovery item.
- **Positive:** Artwork images still benefit from aggressive long-term caching.
- **Negative:** 60s code cache means developers may need to wait up to a minute or hard-refresh during rapid iteration.

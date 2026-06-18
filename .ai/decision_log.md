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

---

## ADR-006: Multi-Worker WebSocket Synchronization via SQLite

**Date:** 2026-04-29  
**Status:** ✅ Accepted & Implemented  
**Deciders:** AI Lead Backend Developer  

### Context

After scaling to 4 Uvicorn workers (ADR-003), the remote control system became intermittent. WebSockets were tracked in-memory, meaning each worker only knew about displays connected directly to it. 
- `GET /api/remote/displays` only returned displays held by the specific worker that received the REST request.
- `POST /api/remote/change` failed to deliver commands if the target display was held by a different worker.

### Decision

Implement a database-backed synchronization layer using SQLite to bridge isolated worker memory:

1. **Global Visibility:** Added `active_displays` table. WebSocket connections run a background heartbeat task to update their `last_seen_at` timestamp. Discovery API now queries this shared table with a 15s timeout.
2. **Command Relaying:** Added `remote_commands` table. Remote commands are persisted to the DB rather than being sent directly to the local `ConnectionManager`. 
3. **Poll & Relay:** Each active WebSocket runs a 1s command poller that checks the DB for commands targeting its `display_id`, relays them, and deletes the record.

### Consequences

- **Positive:** 100% reliability for display discovery and remote commands across any number of workers.
- **Positive:** Maintains zero-dependency architecture (no Redis/NATS required).
- **Negative:** Increased SQLite read/write frequency (1 write/5s/display for heartbeat, 1 read/1s/display for polling). Given the single-user/low-display nature of the app, this overhead is negligible.
- **Positive:** Heartbeats are automatically cleaned up on clean WebSocket disconnect, or expire naturally if a worker crashes.

---

## ADR-007: Stateful Playback Sessions and Bag Shuffle

**Date:** 2026-04-29  
**Status:** ✅ Accepted & Implemented  
**Deciders:** AI Lead Backend Developer  

### Context

The playlist playback system had two significant UX flaws:
1. Shuffled playlists were memoryless (`random.randint`), leading to frequent repeats and a lack of variety.
2. Sequential playlists always reset to the first image on display reconnect/reboot.
3. The frontend tried to guess the playlist settings before its first fetch, often defaulting to incorrect sequential mode on the first frame.

### Decision

Implement a persistent, stateful playback session manager in the backend:

1. **Session Persistence:** Added `display_playback_sessions` table mapping `display_id + playlist_id` to its current state.
2. **Bag Shuffle:** Instead of pure randomness, the backend maintains a "bag" (JSON list) of unplayed artwork IDs. It draws from the bag until empty, then refills. This guarantees 100% variety.
3. **Affinity-Weighted Draw:** Integrated Phase 6 telemetry by using `affinity_score` as a weight for the "bag" draw. High-affinity items appear earlier in the cycle.
4. **Authoritative Config:** The `/next-image` endpoint now resolves the configuration hierarchy, ensuring the first load is correct.

### Consequences

- **Positive:** Variety is mathematically guaranteed; every image is seen once before any is repeated.
- **Positive:** Displays resume exactly where they left off after power cycles or reboots.
- **Positive:** Simplifies frontend logic and reduces client-side state bugs.
- **Negative:** Increased DB writes (1 per image transition). Negligible impact for the expected usage scale.

---

## ADR-009: Frontend Telemetry Loop

**Date:** 2026-04-29  
**Status:** ✅ Accepted & Implemented  
**Deciders:** AI Lead Backend Developer  

### Context

To implement the Phase 6 Autonomous Director, the server needs to know how users interact with the artwork on the Canvas UI. Specifically, how long an image is allowed to display naturally, and when a user actively "skips" an image via the remote control or on-screen buttons.

### Decision

Implement an event-driven telemetry heartbeat in `static/app.js`:

1.  Track `activeArtworkId` and `activeImageStartTime` in global state.
2.  Just before fetching the *next* image, calculate the total elapsed display time of the *current* image.
3.  Add an `isSkipped` boolean flag to the transition logic. Natural timer transitions (`setTimeout`) pass `false`. Manual triggers (remote control, UI buttons) pass `true`.
4.  Send a non-blocking `POST` request with this data to the `/api/telemetry/heartbeat` endpoint.

### Consequences

- **Positive:** Closes the loop for Phase 6. The backend now actively learns from user behavior.
- **Positive:** Handles extremely rapid skipping gracefully by ignoring display durations of < 1 second.
- **Negative:** If a user closes the browser window/TV app abruptly, the telemetry for the *currently* displaying image is lost because the event only fires on transition. This is an acceptable tradeoff to avoid constant polling overhead.

---

## ADR-008: Multi-Worker Startup Leader Election

**Date:** 2026-04-29  
**Status:** ✅ Accepted & Implemented  
**Deciders:** AI Lead Backend Developer  

### Context

After moving to a 4-worker Uvicorn deployment (ADR-003), a race condition was discovered during Docker container initialization, especially following a Factory Reset. All 4 workers execute the FastAPI `lifespan` block simultaneously. This resulted in:
1. 4 concurrent attempts to run `alembic upgrade head`, risking SQLite lock errors and schema corruption.
2. 4 concurrent checks for `is_seed == True` in an empty database, causing all 4 workers to simultaneously launch the background factory bootstrapper, resulting in 4 duplicate copies of every masterpiece in the database and filesystem.

### Decision

Implement an OS-level file lock (`fcntl.flock` with `LOCK_EX | LOCK_NB`) in the FastAPI `lifespan` context manager.

1. The 4 workers attempt to acquire an exclusive, non-blocking lock on `/tmp/screen_docent_startup.lock`.
2. The first worker to succeed becomes the "Leader" and executes Alembic migrations, database syncs, and factory seeds.
3. The other 3 workers immediately catch a `BlockingIOError`, log that they are "Followers", skip the initialization block, and begin serving traffic.
4. Crucially, the file is intentionally *never unlocked* in the `lifespan` block to prevent slightly delayed workers from grabbing the lock while the Leader is still processing. The OS automatically reclaims the lock when the Uvicorn worker process terminates upon container shutdown.

### Consequences

- **Positive:** Guarantees migrations and seed downloads only happen exactly once, preventing duplicate rows and corrupted states.
- **Positive:** Extremely lightweight compared to using a Redis lock.
- **Negative:** Relies on UNIX/Linux specific `fcntl`. This is acceptable as the deployment target (Docker/MS-01) is strictly Linux.

---

## ADR-010: Fixed App-Shell Layout for the Admin Dashboard

**Date:** 2026-06-18
**Status:** ✅ Accepted & Implemented
**Deciders:** Josh

### Context

The admin dashboard exhibited two stacked vertical scrollbars on the right in the **Collections** view but only one in the **Full Library** view. Investigation (headless Chrome over the DevTools Protocol against the running container) showed:

- The outer scrollbar was the **document/`<html>`** scrollbar; the inner was `main`'s content scroll.
- In Collections, the left `aside` (playlist list + the expanded playlist settings panel) is taller than the viewport, but `aside` had `overflow: visible`. The overflow spilled below the body and stretched the whole document, creating the document-level scrollbar.
- In Full Library, `#sidebar-playlists` is hidden, so the sidebar was short, nothing overflowed, and only `main`'s scrollbar appeared. This asymmetry was the user-visible inconsistency.
- Separately, the `.action-bar` (drop zone + Add buttons) scrolled away with the grid in both views.

### Decision

Treat the admin as a **fixed app-shell**: the document itself never scrolls; each pane scrolls internally.

1. `body { overflow: hidden }` — clip the document so the spilled sidebar can never create a document scrollbar.
2. `aside { overflow-y: auto }` — the sidebar scrolls within its own pane when its content exceeds the viewport (keeps the New Playlist input reachable).
3. `.action-bar` and the Discover Mission Control panel use `position: sticky; top: 0` (with a solid background), and `main`'s padding was moved so the headers pin flush to the top. The drop zone + Add buttons now stay visible while the grid scrolls.

### Consequences

- **Positive:** Exactly one scrollbar per pane; headers stay pinned; Library and Collections behave identically.
- **Positive:** CSS-only change in `static/admin.html` — no JS or structural markup changes.
- **Note:** `static/` is not volume-mounted (only `./Artwork` and `./data`), so this — like any UI change — requires `docker compose build && up -d` to appear. Deliberately kept this way to preserve the self-contained image / "pull → rebuild" upgrade model from [ADR-001](#adr-001-git-based-docker-pipeline-for-zero-touch-deployments).
- **Future pattern:** New full-height admin panes should rely on internal pane scrolling, never document scroll.

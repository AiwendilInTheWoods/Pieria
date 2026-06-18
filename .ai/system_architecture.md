# Screen Docent — System Architecture

> **Version:** 0.6.0 · **Last Updated:** 2026-06-18

---

## Tech Stack

| Layer | Technology | Role |
|-------|-----------|------|
| **Runtime** | Python 3.11+ | Application language |
| **Web Framework** | FastAPI 0.111 | ASGI backend, REST API, WebSocket hub |
| **ASGI Server** | Uvicorn 0.30 (4 workers) | Multi-process HTTP/WS serving |
| **Database** | SQLite 3 via SQLAlchemy 2.0 | Local, file-based relational store (`./data/artwork.db`) |
| **Migrations** | Alembic 1.13 | Versioned schema migrations (`migrations/versions/`) |
| **AI / Vision** | Google Gemini 2.5 Flash (`google-generativeai`) | Artwork identification, VRA metadata generation, RAG enrichment |
| **RAG Context** | Wikipedia API (`wikipedia` 1.4) | Fact-checking ground truth for curator pipeline |
| **Image Processing** | Pillow 10.3 | Thumbnail generation, image optimisation, format conversion |
| **HTTP Client** | httpx 0.27 | Async museum API calls, image downloads |
| **Frontend (Canvas)** | Vanilla JS + CSS (GPU-accelerated) | Full-screen display engine with Ken Burns, crossfade, and matte modes |
| **Frontend (Admin)** | Vanilla JS + Cropper.js | Dashboard for library management, crop editing, AI review queue |
| **Frontend (Remote)** | Vanilla JS (PWA-ready) | Mobile-first remote control for targeted display management |
| **Containerisation** | Docker + Docker Compose | Zero-touch deployment to MS-01 server |

---

## Architecture Overview — The Two-Headed Architecture

Screen Docent is a **single FastAPI server** that exposes two fundamentally different user interfaces, connected by a shared WebSocket hub:

```
┌─────────────────────────────────────────────────────────────────────┐
│                      MS-01 Server (Docker)                          │
│                                                                     │
│  ┌───────────────────────────────────────────────────────────────┐  │
│  │                    FastAPI / Uvicorn (×4)                     │  │
│  │                                                               │  │
│  │  REST API ◄──────────────── Admin Dashboard (admin.html)      │  │
│  │     │                              ▲                          │  │
│  │     ▼                              │                          │  │
│  │  SQLite DB ◄─ SQLAlchemy + Alembic ─► Models (artwork,        │  │
│  │  (./data/)        playlist, discovery_queue, settings,        │  │
│  │                   active_displays, remote_commands,           │  │
│  │                   display_playback_sessions)                  │  │
│  │     │                                                         │  │
│  │     ▼                                                         │  │
│  │  AI Pipeline ──► agents.py (Gemini Vision)                    │  │
│  │     │            curator.py (RAG + Wikipedia)                 │  │
│  │     │            scout.py (8 Museum API Scouts)               │  │
│  │     │            query_classifier.py (Intent Classification)  │  │
│  │     │            result_ranker.py (Scoring + Deduplication)   │  │
│  │     │                                                         │  │
│  │  WebSocket Hub ──► ConnectionManager + DB cross-worker sync   │  │
│  │     │       │                                                 │  │
│  │     ▼       ▼                                                 │  │
│  │  Canvas     Remote                                            │  │
│  │  Display    Control                                           │  │
│  └───────────────────────────────────────────────────────────────┘  │
│                                                                     │
│  Volumes:                                                           │
│    ./Artwork  ──►  /app/Artwork   (media library)                   │
│    ./data     ──►  /app/data      (SQLite database)                 │
└─────────────────────────────────────────────────────────────────────┘
```

### Head 1: The Canvas (TV Display)

- **Route:** `/` → `static/index.html` + `static/app.js`
- **Purpose:** A zero-chrome, full-screen artwork display designed for Fire TV, Android TV, or any browser running in kiosk mode.
- **Key Behaviours:**
  - Connects via **WebSocket** at `/ws/{display_id}` for real-time remote commands.
  - Auto-cycles through approved artworks from a playlist, using the `/next-image` REST endpoint.
  - Supports three rendering modes: **Ken Burns Pan** (GPU-animated), **Static User-Defined Crop**, and **Contain with Blurred Matte**.
  - Displays a **Museum Placard** (glassmorphism panel) with VRA-Core metadata and a dynamic QR code.
  - Includes a "Sleep Defeater" — a hidden looping `<video>` element that prevents streaming hardware from entering standby.
  - **Hierarchical Config Override:** URL params (`?cycle_time=`, `?mode=`) → Playlist DB defaults → Global hardcoded defaults.

### Head 2: The Mobile Remote & Admin

- **Remote Route:** `/remote` → `static/remote.html`
  - A mobile-optimised PWA for switching playlists, navigating images, changing display modes, and triggering placard display on **specific** connected Canvas displays.
  - Polls `/api/remote/displays` every 5 seconds; live displays are read from the `active_displays` table (heartbeat-backed), so the list is correct regardless of which worker owns each WebSocket.
  - Sends targeted commands via `POST /api/remote/change`, which enqueues a row in `remote_commands` for the owning worker to deliver (see Multi-Worker Concurrency Model below).

- **Admin Route:** `/admin` → `static/admin.html` + `static/admin.js`
  - Full library management dashboard: upload, delete, re-order, crop editing (Cropper.js), playlist CRUD.
  - AI Review Queue: inspect AI-generated metadata, edit fields, approve/reject.
  - Art Scout Discovery: dispatch scouts to 8 tuned museum APIs, preview thumbnails, approve for download + RAG enrichment.
  - Admin utilities: Factory Reset (wipe non-seed data), Clear All Pending (clean test slate).
  - API Key management for Tier-2 (key-gated) museum sources: Harvard, Smithsonian, Europeana.

### Multi-Worker Concurrency Model (Phase 6)

Uvicorn runs **4 worker processes**, each with its own memory space — so in-process state
(`ConnectionManager`'s WebSocket dict) is **not shared across workers**. Cross-worker coordination
is therefore done through the SQLite database:

- **`active_displays`** — on each WebSocket connection, that worker runs a `heartbeat()` task that
  upserts a row with `last_seen_at`. `/api/remote/displays` lists displays seen within a recent cutoff.
- **`remote_commands`** — the remote enqueues a command targeting a `display_id`; the worker that owns
  that socket runs a `command_poller()` task which polls the table and delivers via its **local**
  `manager.send_personal_message(...)`, then deletes the row.
- **`display_playback_sessions`** — persists per-display "bag shuffle" next-image state so playback
  variety/sequence survives reconnects and is consistent no matter which worker serves `/next-image`.
- **First-run seeding** uses a file-lock so only one of the 4 workers performs the factory seed; the
  others skip with a `BlockingIOError`.

---

## File Tree (Core Application)

```
Screen-Docent/
├── app.py                  # FastAPI application: routes, WebSocket hub, middleware, lifespan
├── config.py               # Shared constants (ARTWORK_ROOT, LIBRARY_DIR); breaks circular imports
├── database.py             # SQLAlchemy engine, session factory, table bootstrap (create_all)
├── models.py               # ORM models: Playlist, Artwork, DiscoveryQueue, Settings, ActiveDisplay, RemoteCommand, DisplayPlaybackSession
├── agents.py               # Gemini Vision Agent: image analysis → VRA metadata JSON
├── curator.py              # RAG Curator: Wikipedia lookup → Gemini re-enrichment
├── scout.py                # 8 Museum API Scouts — keyless: Chicago, Met, Cleveland, Rijks, SMK; key-gated: Harvard, Smithsonian, Europeana
├── query_classifier.py     # Hybrid intent classifier: dictionary (~200 artists) + Gemini Flash fallback
├── result_ranker.py        # Multi-factor scoring (artist match, title, highlight, image quality, metadata)
│
├── migrations/             # Alembic migration environment
│   ├── env.py              # Alembic runtime config
│   └── versions/           # Versioned schema migration scripts
├── alembic.ini             # Alembic configuration
│
├── static/
│   ├── index.html          # Canvas TV display (full-screen artwork viewer)
│   ├── app.js              # Canvas client logic: crossfade, Ken Burns, WebSocket, placard
│   ├── styles.css          # Canvas + placard + controls styling (vmin-based, GPU-accelerated)
│   ├── admin.html          # Admin dashboard (library, playlists, review queue, discovery)
│   ├── admin.js            # Admin client logic: CRUD, crop modal, scout dispatch
│   ├── remote.html         # Mobile remote control (PWA-ready)
│   ├── help.html           # Help & documentation page
│   ├── logo.svg            # Screen Docent logo
│   └── factory_seed.json   # Bootstrap masterpiece dataset for first-run
│
├── Artwork/
│   └── _Library/           # Canonical image store (all originals live here)
│
├── data/
│   └── artwork.db          # SQLite database (volume-mapped in Docker)
│
├── Dockerfile              # Python 3.11-slim, Uvicorn with 4 workers
├── docker-compose.yml      # Service definition with Artwork + data volume mounts
├── requirements.txt        # Pinned Python dependencies
├── .env                    # GEMINI_API_KEY (gitignored)
├── .dockerignore           # Excludes .git, venv, __pycache__
├── .gitignore              # Standard Python + data exclusions
│
├── tests/
│   ├── conftest.py         # Pytest fixtures (in-memory SQLite)
│   ├── __init__.py
│   └── test_scout.py       # Scout module unit tests
├── requirements-dev.txt    # Dev/test dependencies (pytest, coverage)
│
├── README.md               # Project overview and setup guide
├── LICENSE                 # Project license
│
└── .ai/                    # Developer/AI context (system_architecture.md tracked;
                            #   active_context.md + decision_log.md are local-only / gitignored)
```

---

## Core Development Rules

> [!CAUTION]
> These guardrails are derived from the actual codebase and past architectural decisions. Violating them will introduce regressions.

### 1. No Heavy Frontend Frameworks
The frontend is **Vanilla JS, CSS, and HTML**. Do not introduce React, Vue, Svelte, or any SPA framework. The Canvas display must remain a lightweight, GPU-accelerated page that runs reliably on Fire TV Stick hardware with limited RAM.

### 2. Always Use `StaticFiles` for Serving Media
Artwork images are served via FastAPI's ASGI `StaticFiles` mount at `/media`. **Never** use `FileResponse` for artwork serving in production — it blocks the event loop and was the root cause of the Phase 5 TTFB bottleneck. The `StaticFiles` middleware handles range requests, caching, and concurrent delivery natively.

### 3. Volume-Map the `/data` Directory, Not the `.db` File
In Docker Compose, always map `./data:/app/data` (the directory). Mapping a single `artwork.db` file directly causes SQLite journal/WAL conflicts when the container recreates the inode. This was a critical Docker deployment bug.

### 4. Preserve the Hierarchical Config Override Pattern
Settings resolution follows: **URL Parameter → Playlist DB Default → Global Hardcoded Default**. This applies to `cycle_time`, `mode`, `shuffle`, `placard_wait`, `placard_show`, and `placard_manual`. New settings must follow this same three-tier cascade.

### 5. AI Pipelines Run as Background Tasks
All AI processing (`agents.py`, `curator.py`, `scout.py`) must run via FastAPI `BackgroundTasks` or `asyncio.create_task()`. They must never block the request/response cycle. Each background task must create and close its own `SessionLocal()` database session.

### 6. Image Optimisation Before AI Submission
Before sending images to Gemini, always resize to a maximum of 2048×2048 pixels using Pillow's `thumbnail()` with `LANCZOS` resampling, and convert to JPEG at 85% quality. This prevents API timeouts and reduces token costs.

### 7. Schema Changes Go Through Alembic
Schema is now versioned with **Alembic** (`migrations/versions/`); `database.py` only bootstraps tables
via `Base.metadata.create_all` on startup. New columns/tables must be added with an Alembic revision —
prefer **additive** changes (add column/table) because SQLite's `ALTER TABLE` cannot drop or rename
columns in place. **Never** hand-edit the live `artwork.db` schema or drop/rename columns ad hoc.

### 8. WebSocket Commands Are Targeted by `display_id` — Across Workers via the DB
With 4 Uvicorn workers, each worker's in-memory `ConnectionManager` only knows its **own** sockets, so
targeting cannot rely on a shared dict. Remote actions are enqueued in the **`remote_commands`** table;
the worker owning the target socket polls that table (`command_poller()`) and delivers locally via
`send_personal_message(message, display_id)`. Live displays are tracked in **`active_displays`** via
per-connection heartbeats. `broadcast()` exists but is for system-wide announcements only — all remote
control actions must be targeted. Never assume in-process state is visible to other workers.

### 9. All Artwork Lives in `Artwork/_Library/`
Regardless of playlist membership, the canonical copy of every image file lives in `Artwork/_Library/`. Playlist subdirectories (`Artwork/{PlaylistName}/`) are used only during initial filesystem ingestion and are then treated as symlink/organisation artifacts.

### 10. Rate-Limit External API Calls
Museum scouts and batch enrichment pipelines must include explicit `asyncio.sleep()` delays between requests. The factory seed bootstrapper uses exponential backoff on HTTP 429 responses. New scouts must follow this pattern.

### 11. Museum Scouts Must Use Progressive Fallback
Each scout's `find_art()` method should try its most precise API-specific query first (e.g., `creator=`, `who:`, `artists=`). If it returns 0 results, retry with a broader query automatically. **Never silently return 0 results** when a fallback strategy is available. Log each fallback attempt with `logger.info()`.

### 12. Background Tasks Must Log Errors Explicitly
All `BackgroundTasks` and `asyncio.create_task()` coroutines must wrap their entire body in `try/except Exception` with `logger.error(..., exc_info=True)`. FastAPI silently swallows background task exceptions — without explicit logging, failures are invisible.

### 13. API Responses Must Not Be Cached
The cache middleware must exclude all `/api/*` paths from caching (`no-store, no-cache, must-revalidate`). Discovery queue, search status, and admin data change constantly. Only serve cached responses for static media and code assets.

---

## Admin Utilities

### Factory Reset (`POST /api/admin/factory-reset`)
Wipes all non-seed artwork (DB records + disk files), clears the entire discovery queue, and resets search sessions. Requires a `confirmation` body field with the exact value `"RESET"`. Used for clean testing environments.

### Clear All Pending (`DELETE /api/discover/clear-pending`)
Removes all `pending` discovery queue items without affecting approved or rejected history. Used between test search runs to get a clean slate.

### Clear Rejected History (`DELETE /api/discover/history`)
Purges all `rejected` discovery queue records, allowing scouts to rediscover previously-skipped artwork.

### Clear Orphaned Approvals (`DELETE /api/discover/orphans`)
Removes discovery queue items marked `approved` that have no corresponding active artwork record (e.g., if the artwork was manually deleted from the library).

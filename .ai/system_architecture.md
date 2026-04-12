# Screen Docent — System Architecture

> **Version:** 0.5.0 · **Last Updated:** 2026-04-04

---

## Tech Stack

| Layer | Technology | Role |
|-------|-----------|------|
| **Runtime** | Python 3.11+ | Application language |
| **Web Framework** | FastAPI 0.111 | ASGI backend, REST API, WebSocket hub |
| **ASGI Server** | Uvicorn 0.30 (4 workers) | Multi-process HTTP/WS serving |
| **Database** | SQLite 3 via SQLAlchemy 2.0 | Local, file-based relational store (`./data/artwork.db`) |
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
│  │                    FastAPI / Uvicorn (×4)                      │  │
│  │                                                               │  │
│  │  REST API ◄──────────────── Admin Dashboard (admin.html)      │  │
│  │     │                              ▲                          │  │
│  │     ▼                              │                          │  │
│  │  SQLite DB ◄── SQLAlchemy ──► Models (playlist, artwork,      │  │
│  │  (./data/)                    discovery_queue, settings)       │  │
│  │     │                                                         │  │
│  │     ▼                                                         │  │
│  │  AI Pipeline ──► agents.py (Gemini Vision)                    │  │
│  │     │            curator.py (RAG + Wikipedia)                 │  │
│  │     │            scout.py (6 Museum API Scouts)               │  │
│  │     │            query_classifier.py (Intent Classification)  │  │
│  │     │            result_ranker.py (Scoring + Deduplication)    │  │
│  │     │                                                         │  │
│  │  WebSocket Hub ──► ConnectionManager (display_id routing)     │  │
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
  - Polls `/api/remote/displays` every 5 seconds for active WebSocket clients.
  - Sends targeted commands via `POST /api/remote/change`.

- **Admin Route:** `/admin` → `static/admin.html` + `static/admin.js`
  - Full library management dashboard: upload, delete, re-order, crop editing (Cropper.js), playlist CRUD.
  - AI Review Queue: inspect AI-generated metadata, edit fields, approve/reject.
  - Art Scout Discovery: dispatch scouts to 6 tuned museum APIs, preview thumbnails, approve for download + RAG enrichment.
  - Admin utilities: Factory Reset (wipe non-seed data), Clear All Pending (clean test slate).
  - API Key management for Tier-2 museum sources (Europeana).

---

## File Tree (Core Application)

```
Screen-Docent/
├── app.py                  # FastAPI application: routes, WebSocket hub, middleware, lifespan
├── database.py             # SQLAlchemy engine, session factory, lightweight migration helper
├── models.py               # ORM models: PlaylistModel, ArtworkModel, DiscoveryQueueModel, SettingsModel
├── agents.py               # Gemini Vision Agent: image analysis → VRA metadata JSON
├── curator.py              # RAG Curator: Wikipedia lookup → Gemini re-enrichment
├── scout.py                # 6 Museum API Scouts (Chicago, Met, Cleveland, Rijks, SMK, Europeana)
├── query_classifier.py     # Hybrid intent classifier: dictionary (~200 artists) + Gemini Flash fallback
├── result_ranker.py        # Multi-factor scoring (artist match, title, highlight, image quality, metadata)
├── migrate_vra.py          # One-shot migration: old (title/artist/year) → VRA Core schema
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
│   └── test_scout.py       # Scout module unit tests
│
├── GEMINI.md               # Workspace coding standards for AI assistants
├── PRD.md                  # Product Requirements Document (Phases 1–5)
├── README.md               # Project overview and setup guide
└── LICENSE                 # Project license
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

### 7. Database Migrations Are Additive Only
The `apply_migrations()` function in `database.py` uses `ALTER TABLE ADD COLUMN` to non-destructively add new columns. **Never drop or rename columns** — SQLite's ALTER TABLE is limited. For complex schema changes, use Alembic (planned for Phase 6).

### 8. WebSocket Commands Are Targeted by `display_id`
The `ConnectionManager` routes messages to specific displays using `send_personal_message(message, display_id)`. The `broadcast()` method exists but should only be used for system-wide announcements. All remote control actions must be targeted.

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

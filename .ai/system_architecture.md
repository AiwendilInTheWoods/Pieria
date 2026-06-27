# Screen Docent — System Architecture

> **Version:** 0.9.0 · **Last Updated:** 2026-06-21

---

## Thesis

**Own the curation brain; render beautifully onto whatever panel exists; make onboarding one step.**
A single self-hosted FastAPI server is the *brain* (sourcing, curation, AI enrichment, scheduling). It
drives **many output targets** — an animated browser/TV Canvas, a stateless e-ink image API, and a push
adapter for Samsung Frame TVs — and is growing a **federated catalog** so anyone can publish collections.

## Tech Stack

| Layer | Technology | Role |
|-------|-----------|------|
| **Runtime** | Python 3.12 | Application language |
| **Web Framework** | FastAPI 0.111 | ASGI backend, REST API, WebSocket hub |
| **ASGI Server** | Uvicorn 0.30 (4 workers) | Multi-process HTTP/WS serving |
| **Database** | SQLite 3 via SQLAlchemy 2.0 | Local, file-based relational store (`./data/artwork.db`) |
| **Migrations** | Alembic 1.13 | Versioned schema migrations (`migrations/versions/`) |
| **AI (BYO model)** | `ai_client.py` — one **OpenAI-compatible** client | Any provider (Gemini-compat, OpenAI, Anthropic, OpenRouter, local Ollama) = base_url + api_key + model. Configured in-GUI; `google-generativeai` **dropped**. |
| **RAG Context** | Wikipedia API (`wikipedia` 1.4) | Ground-truth context for the curator pipeline |
| **Image Processing** | Pillow 10.3 | Optimisation, EXIF-orient, crop, e-ink palette-quantize + Floyd–Steinberg dither |
| **HTTP Client** | httpx 0.27 | Async museum/image/manifest fetches |
| **Crypto** | PyNaCl 1.6 (Ed25519) | Federation: signed-manifest verification (verified-publisher tier) |
| **Frame TV** | `samsungtvws` 3.0 | Push render adapter (LAN Art Mode, no Samsung account) |
| **Frontend** | Vanilla JS + CSS (GPU-accelerated) | Canvas display, admin, mobile remote — **no SPA framework** |
| **Vendored JS** | Cropper.js, Sortable.js, QRCode.js (in `static/vendor/`) | Local copies — **zero external runtime deps** (air-gap-safe) |
| **Containerisation** | Docker + Docker Compose | Self-contained image; `static/` + Python baked in (rebuild to deploy) |
| **Dev tooling** | Ruff (lint), pytest (+asyncio, respx), pre-commit (gitleaks + ruff) | **Dev-only** — never in the shipped image (requirements-dev.txt) |

---

## Architecture Overview — one brain, many outputs

```
                         ┌──────────── CURATION BRAIN (FastAPI / Uvicorn ×4) ────────────┐
                         │                                                               │
  museum APIs ─ scout.py ┤  Sourcing: 8 museum scouts + NASA + Wikimedia (live Discover) │
                         │  Enrichment: agents.py (vision) · curator.py (RAG+Wikipedia)  │
                         │  Search: query_classifier.py · result_ranker.py (+clean_title)│
                         │  Director: get_next_image() bag-shuffle + affinity weighting  │
                         │  Store: SQLAlchemy + Alembic → ./data/artwork.db              │
                         │  Media: ./Artwork/_Library (canonical), served via StaticFiles│
                         └───────────────┬───────────────┬───────────────┬──────────────┘
                                         │               │               │
                  WebSocket /ws/{id} +   │   GET /display/{id}/current.{png,bmp}   POST → Frame
                  /next-image            │   (epaper.py: crop+quantize+dither)     (frame_push.py)
                                         ▼               ▼               ▼
                              ┌──────── OUTPUT TARGETS ─────────────────────────┐
                              │  Canvas (TV/browser)   e-ink / BYOS frames   Samsung Frame Art Mode │
                              │  index.html + app.js   pull-on-wake image     (push, LAN, no sub)    │
                              └─────────────────────────────────────────────────┘

  Catalog / Federation: static/catalog/ (bundled) + subscribed Manifest v2 feeds (federation.py)
  → merged in /api/catalog, badged Official / Verified / Community.

  Volumes:  ./Artwork → /app/Artwork   ·   ./data → /app/data
```

### Output target 1 — The Canvas (TV / browser)
- **Route:** `/` → `static/index.html` + `static/app.js`. Zero-chrome full-screen viewer for Fire TV /
  Android TV / kiosk browsers.
- WebSocket `/ws/{display_id}` for targeted remote commands; auto-cycles via `/next-image`.
- Three render modes: **Ken Burns pan** (GPU, **focal-adaptive** — anchors the zoom/drift on the
  artwork's focal point via the Web Animations API so off-center subjects aren't panned out of frame),
  **static crop**, **contain + blurred matte**.
- **Museum placard** with metadata + a **QR code → `/art/{id}`** (server-hosted "Learn More" page,
  works offline; no Google hand-off).
- **Empty-state** overlay ("No art yet — add it at <host>/admin") instead of a black screen; a
  first-load "Manage at …/admin" hint.
- **Sleep defeater:** a hidden looping **local** `static/assets/keepawake.mp4` (vendored — was a
  w3schools URL) so it works offline.
- Hierarchical config: URL param → Playlist DB default → Global default.

### Output target 2 — e-ink / BYOS frames (`epaper.py`)
- **Stateless pull-on-wake:** `GET /display/{id}/current.{png,bmp}?w=&h=&palette=`. Reuses
  `get_next_image()` selection, then EXIF-orient → RGB-normalise → **focal-anchored** cover/contain crop → contrast/sat
  pre-boost → palette quantize + Floyd–Steinberg dither. Palettes: `spectra6`/`acep7`/`gray4`/`gray16`.
  Returns `X-Refresh-After` as the sleep hint; upserts `active_displays`.

### Output target 3 — Samsung Frame TV push (`frame_push.py`)
- Renders the selected artwork to a full-colour TV-res JPEG (`epaper.render_fullcolor`) and uploads it
  into the Frame's Art Mode over the LAN (unofficial WebSocket via `samsungtvws`), leader-only loop.
  TV hidden behind a `FrameClient` ABC (`SamsungFrameClient` + `FakeFrameClient`) — all logic tested
  against the fake; real-hardware path in `integrations/frame-tv/`.

### Framing — per-artwork focal points
- Every artwork carries a normalized **`focal_x`/`focal_y`** (default `0.5,0.5` = center = prior behavior)
  — the visual subject the renderer keeps in frame. **One point feeds all three outputs:** the Canvas
  Ken Burns (transform-origin + background-position + focal-adaptive drift), and `epaper._fit_rgb`'s
  `ImageOps.fit` centering for e-ink + Frame.
- **Derived, baked once:** AI sets it during enrichment (`agents`/`curator`, shared
  `FOCAL_POINT_INSTRUCTION`/`apply_focal_point`); the bundled catalog + seed ship **pre-baked**
  `focal_point` (offline `tools/backfill_focal_*` — an in-IDE Claude-agent vision pass, no app API key);
  catalog/seed add + the seed loader copy it via `_focal_xy`. Settable per-artwork via
  `PATCH /artworks/{id}/crop` (which also persists the manual Cropper crop).

### The Admin & Mobile Remote
- **Admin** `/admin` (`admin.html`+`admin.js`): library/playlist CRUD, crop editing, **Review Queue**
  (live-enriching), **Discover** (8 museum scouts + NASA + Wikimedia), **Browse Catalog** (+ collection
  picker), **Settings** (📡 This Server · 🧠 AI Engine BYO-model · 🖼️ Frame TV · 🌐 Subscriptions ·
  📚 Catalog Source · premium museum keys · Maintenance). **Responsive** (slide-in drawer under 768px).
  Themed toast/modal pattern (no native `alert/confirm/prompt`).
- **Remote** `/remote` (`remote.html`): mobile PWA; targets specific Canvas displays via
  `active_displays` + `remote_commands` (cross-worker, see below).

### Studio — "My Photos" (personal photos)
- **`/studio`** (`studio.html`): a phone-first front door for a user's OWN photos — multi-upload (+camera
  capture), optional **AI caption** (evocative photo-album voice; `is_local_base_url()` gives an honest
  on-device-vs-cloud privacy note), and **tap-to-set focal point**.
- **`POST /upload/personal`:** local-only, EXIF-oriented, `is_personal=True`, `status=approved` — it
  **deliberately skips the museum AI pipeline** (the photo is never sent to a model — the privacy
  headline) and the Review Queue; auto-files into a "My Photos" playlist. `is_personal` also drives a
  **jargon-free placard** (caption + date only, no QR) on the Canvas and `/art/{id}`. Caption/date saved
  via `PATCH /api/studio/photo/{id}` (personal-only); a remote `catalog_url` is configurable separately.

### Multi-Worker Concurrency Model
Uvicorn runs 4 workers (separate memory). Cross-worker coordination via SQLite:
- **`active_displays`** — per-connection heartbeat upserts; `/api/remote/displays` lists recent.
- **`remote_commands`** — remote enqueues a targeted command; the owning worker's `command_poller()`
  delivers locally then deletes the row.
- **`display_playback_sessions`** — per-display bag-shuffle state survives reconnects.
- **Leader-only boot** (`fcntl` lock): one worker runs migrations, factory seed, and the Frame push loop.

---

## Catalog & Federation

- **Bundled catalog:** `static/catalog/` — a split manifest (`index.json` + per-collection files,
  524 served PD items / 24 collections, each carrying a baked **`focal_point`**) built offline by `tools/`
  (`catalog_spec.py` → `sources.py` resolvers → `build_catalog.py`, display-gated to ≥2000px). Loaded
  **from disk** — `origin: bundled`. A remote **`catalog_url`** (Settings → 📚 Catalog Source) can
  override it with a static-hosted manifest, falling back to bundled on any fetch failure.
- **Federation (`federation.py` + Manifest v2):** subscribe to a third-party collection by URL.
  - **`docs/manifest-v2.md`** is the spec; **`tools/manifest_validator.py`** the executable validator
    (pure-Python, strict on required/types/**per-asset licensing**, tolerant of unknown fields →
    forward-compatible with optional `interpretation` + `image.focal_point` assets).
  - **Safety (we index pointers, never host bytes):** SSRF guard (blocks private/loopback/link-local
    IPs), redirects disabled, size cap + content-type/JSON check + timeout, strict validation, a
    host/publisher blocklist. Untrusted strings are escaped at render time.
  - **Trust:** Ed25519 signed manifests (`canonical_bytes`/`verify_signature`/`assess_trust`).
    `verified` = signed **and** publisher key in the curated `registry/trusted_publishers.json`;
    else `community` (unsigned, or validly self-signed but not registry-trusted = TOFU). A
    present-but-invalid signature is **rejected** outright. `tools/sign_manifest.py` = publisher CLI.
  - **Provenance is OUR record:** the `subscriptions` table (`SubscriptionModel`) stamps origin/
    publisher/trust at subscribe-time — never trusted from the manifest body. Subscribed collections
    merge into `/api/catalog` namespaced `sub_<id>`, badged **Official / Verified / Community** in the
    admin. A third-party feed is structurally incapable of masquerading as bundled (disk vs. fetch path).
- **Shared downloader:** seed / discovery / catalog / federation-add all route through
  `_download_image_to_library` (descriptive UA, 429 retry, redirect, collision-safe name, image
  validation); federation-add additionally SSRF-guards the third-party image URL.

---

## File Tree (Core Application)

```
Screen-Docent/
├── app.py              # FastAPI: routes, WS hub, middleware, lifespan, catalog+federation+e-ink endpoints
├── ai_client.py        # ONE OpenAI-compatible client for all model calls (BYO provider/key/model)
├── config.py           # Shared constants (ARTWORK_ROOT, LIBRARY_DIR) — breaks circular imports
├── database.py         # SQLAlchemy engine, session factory, create_all bootstrap
├── models.py           # ORM: Playlist, Artwork, DiscoveryQueue, Settings, ActiveDisplay,
│                       #   RemoteCommand, DisplayPlaybackSession, Subscription
├── agents.py           # Vision agent: image → VRA metadata (via ai_client)
├── curator.py          # RAG curator: Wikipedia → re-enrichment (via ai_client)
├── scout.py            # Museum scouts (Chicago/Met/Cleveland/Rijks/SMK + Harvard/Smithsonian/
│                       #   Europeana key-gated) + NASA + Wikimedia; shared PD/resolution helpers
├── query_classifier.py # Hybrid intent classifier
├── result_ranker.py    # Scoring + dedup + clean_title (museum-title normalization)
├── epaper.py           # e-ink render: crop + palette quantize + dither; render_fullcolor (Frame)
├── frame_push.py       # Samsung Frame push adapter (FrameClient ABC + fake + samsungtvws)
├── federation.py       # Manifest v2 fetch (SSRF/size/type guards), Ed25519 trust, sync, mapping
│
├── tools/              # OFFLINE/dev (runtime-independent)
│   ├── catalog_spec.py · sources.py · build_catalog.py   # bundled catalog builder
│   ├── backfill_focal_fetch.py · backfill_focal_bake.py  # focal-point backfill (in-IDE agent vision)
│   ├── manifest_validator.py   # Manifest v2 validator (also imported at runtime by federation)
│   ├── sign_manifest.py        # publisher keygen + sign CLI
│   └── make_gif.py
├── registry/trusted_publishers.json  # curated verified-publisher Ed25519 keys (federation)
├── docs/manifest-v2.md           # Manifest v2 spec
│
├── migrations/versions/  # Alembic revisions (… → add_focal_point_and_is_personal)
├── static/
│   ├── index.html · app.js · styles.css     # Canvas + focal-adaptive Ken Burns + /art QR
│   ├── admin.html · admin.js                # admin (responsive, toast/modal, subs panel, badges)
│   ├── studio.html                          # "My Photos" personal-photo front door (/studio)
│   ├── remote.html · help.html · logo.svg · factory_seed.json
│   ├── catalog/            # bundled split manifest (index.json + per-collection)
│   ├── vendor/             # Cropper / Sortable / QRCode (vendored, offline)
│   └── assets/keepawake.mp4 # local sleep-defeater
├── integrations/          # MMM-ScreenDocent (MagicMirror²) · frame-tv (push_once CLI)
│
├── Artwork/_Library/      # canonical image store      ·  data/artwork.db  (volume-mapped)
├── Dockerfile · docker-compose.yml · requirements.txt · requirements-dev.txt
├── pyproject.toml (Ruff) · .pre-commit-config.yaml
├── tests/                 # pytest (223): scouts, resolvers, catalog, epaper, frame_push, ai_client,
│                          #   download, ranker, detail_page, manifest_validator, federation, signing,
│                          #   personal (Studio), focal
└── .ai/                   # dev context (system_architecture.md tracked; active_context + decision_log local)
```

---

## Core Development Rules

> [!CAUTION]
> Guardrails derived from the codebase + past decisions. Violating them introduces regressions.

1. **No heavy frontend frameworks.** Vanilla JS/CSS/HTML only — the Canvas must run on Fire-TV-class RAM.
2. **Serve media via `StaticFiles`** (`/media`), never `FileResponse` (event-loop block → the Phase-5 TTFB bug).
3. **Volume-map `./data` (the directory)**, not the `.db` file (SQLite WAL/inode conflicts).
4. **Preserve the hierarchical config override:** URL param → Playlist default → Global default.
5. **AI calls go through `ai_client.py`** (one OpenAI-compatible path). Do NOT reintroduce
   `google-generativeai` or hardcode a provider/model — config lives in `SettingsModel` (GUI-set, env fallback).
6. **AI pipelines run as background tasks** that create + close their own `SessionLocal()`.
7. **Optimise images before AI** (≤2048px, JPEG 85%, LANCZOS).
8. **Schema changes go through Alembic** (additive — SQLite can't drop/rename in place).
9. **WebSocket commands are targeted by `display_id` across workers via the DB** (`remote_commands` +
   `active_displays`); never assume in-process state is shared.
10. **All artwork lives in `Artwork/_Library/`** (canonical); playlist dirs are symlink/organisation only.
11. **One robust downloader.** New image-download paths use `_download_image_to_library` (UA + 429 retry +
    validation) — don't hand-roll a bare `httpx.get` (default UA → Wikimedia/NASA 403).
12. **Rate-limit + progressive-fallback external APIs**; **log background-task errors** (`exc_info=True`).
13. **Never cache `/api/*`** (no-store); only static media + code assets are cached.
14. **Federation safety is non-negotiable.** Any path that fetches a third-party URL goes through
    `federation`'s SSRF guard + size/type caps + Manifest v2 validation; treat all manifest strings as
    untrusted (HTML-escape on render). We **index pointers, never host** third-party bytes.
15. **Trust is recorded by us, not claimed by the manifest.** Origin/trust live in `SubscriptionModel`;
    `verified` requires an Ed25519 signature whose key is in `registry/trusted_publishers.json`.
16. **No external runtime deps in the client.** Vendor JS/assets into `static/vendor/` + `static/assets/`
    (offline/air-gap). Ruff/pytest etc. are dev-only (requirements-dev.txt) — never the shipped image.
17. **`static/` + Python are baked into the image.** UI/backend changes need `docker compose build && up -d`;
    bump the `admin.js?v=`/`app.js?v=` cache-bust when changing those files.

---

## Admin Utilities & Key Endpoints

- **Factory Reset** `POST /api/admin/factory-reset` (body `"RESET"`) — wipe non-seed data.
- **Discover maintenance** — clear pending / rejected history / orphaned approvals.
- **Catalog** — `GET /api/catalog` (index, origin-tagged) · `GET /api/catalog/{id}` · `POST /api/catalog/add`.
- **Federation** — `GET/POST/DELETE /api/subscriptions` · `POST /api/subscriptions/{id}/sync`.
- **Display image** — `GET /display/{id}/current.{png,bmp}` (e-ink). **Detail** — `GET /art/{id}`.
- **Studio** — `/studio` page · `POST /upload/personal` · `POST /api/studio/caption/{id}` ·
  `PATCH /api/studio/photo/{id}` · `PATCH /artworks/{id}/crop` (crop + focal point).
- **Settings** — `GET/POST /api/settings/ai` (+OAuth) · `/api/settings/frame` (+test) ·
  `/api/settings/catalog` (remote catalog source) · premium keys.

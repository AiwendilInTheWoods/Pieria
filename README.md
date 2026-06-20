# 🖼️ Screen Docent

**Screen Docent** is an open-source, AI-powered digital art curator and signage platform. It transforms any TV or monitor into a high-end museum display, complete with autonomous artwork analysis, intelligent metadata generation, and instant mobile remote control.

![Screen Docent Logo](static/logo.svg)

## ✨ Features

*   **🕵️ Museum Art Scouts:** Effortlessly search and pull high-res masterpieces directly from world-class APIs (The Met, Art Institute of Chicago, SMK, Cleveland, Rijksmuseum) straight into your discovery queue. Supports premium integrations for Harvard Art Museums, Smithsonian, and Europeana.
*   **🧠 Vision RAG Curator:** Automatically generates museum-grade VRA Core metadata for all artworks. The system features a built-in multilingual translation pipeline that automatically converts foreign metadata (e.g., Dutch Open Data from the Rijksmuseum) into fluent English using Gemini's visual grounding.
*   **🏛 VRA Core Database:** Built on the established Visual Resources Association schema, securely housing rich metadata alongside dynamic crop data and playlists. Supports Many-to-Many relationships for flexible artwork-to-playlist mapping and custom sequencing.
*   **📱 WebSocket Remote:** A mobile-first, no-refresh PWA remote to switch playlists, change modes, and trigger placards instantly.
*   **📺 Multi-Display Support:** Targeted routing using unique display IDs allows a single server to manage different artwork streams across multiple TVs.
*   **🎨 Advanced Rendering:** Choose between cinematic Ken Burns pans, static user-defined crops, or blurred matte effects.
*   **⚖️ Hierarchical Config:** Precise control via URL parameters that override playlist and global defaults.
*   **🔒 Human-in-the-Loop:** A dedicated Review Queue to audit and refine AI-generated content before it goes live to your screens.
*   **🔌 Bring Your Own Model:** Configure the AI engine from the GUI — Google Gemini, OpenAI, Anthropic, OpenRouter (one-click sign-in), or a local Ollama/LM Studio server — all through one OpenAI-compatible backend. No code edits, validated live before saving.
*   **💾 Persistent & Safe:** SQLite-backed state with automatic migrations and Docker volume persistence.

## 🚀 Quickstart Deployment

The fastest way to get Screen Docent running is using Docker.

### 1. Prerequisites
*   [Docker](https://docs.docker.com/get-docker/)
*   [Docker Compose](https://docs.docker.com/compose/install/)

### 2. Launch
No config files needed — clone and go:
```bash
git clone https://github.com/AiwendilInTheWoods/Screen-Docent.git
cd Screen-Docent
docker compose up -d --build
```
(No `.env` required; the stack runs out of the box and you configure the AI model in-app below.)

### 3. Access the System
*   **Admin Dashboard:** `http://localhost:8000/admin` (Upload, discover, and manage art)
*   **Main Display:** `http://localhost:8000/` (Point your TV browser here)
*   **Mobile Remote:** `http://localhost:8000/remote` (Control from your phone)

### 4. Connect a Model (optional, in-app)
Screen Docent works immediately without any AI — the starter art ships with full placards, and the
museum **Discover** scouts need no key. To unlock auto-curation (metadata generation, enrichment,
smarter search), open **Admin → ⚙ Settings → 🧠 AI Engine**, pick a provider (Google Gemini, OpenAI,
Anthropic, OpenRouter, or a local Ollama/LM Studio server), paste a key (or **Sign in with
OpenRouter** for one-click setup), and click **Test & Save** — validated live, effective in seconds.

> Prefer files? You can still pre-seed a default Gemini key with a `.env` (`GEMINI_API_KEY=…`) in the
> project root before launch; the in-app setting overrides it when set.

## 🖥️ Display Appliance (recommended for TVs)

The easiest way to drive a TV or monitor is the **Docent Appliance**: flash a
Raspberry Pi, point it at your server, and it boots straight into the fullscreen
display — no Fully Kiosk, no browser chrome, no URL typing. See
[`deploy/appliance/`](deploy/appliance/README.md). For the broader display
strategy (and e-ink support), see [`ROADMAP.md`](ROADMAP.md).

## 🖼️ e-ink & BYOS frames (image API)

Low-power e-ink and "bring-your-own-server" frames (DIY ESP32 + Waveshare, Inky Impression, a TRMNL
in BYOS mode) don't run the JS display — they just fetch a server-rendered image on a schedule:

```
GET /display/{display_id}/current.png?playlist=Masterpieces&w=1600&h=1200&palette=spectra6
```

The server runs the same curation brain (bag-shuffle + affinity), crops to the panel size, and
Floyd–Steinberg-dithers to the device palette. The response's **`X-Refresh-After`** header tells the
frame how long (seconds) to deep-sleep before the next fetch; each GET advances to the next image.

- **Palettes:** `spectra6` (E Ink Spectra 6), `acep7` (7-colour ACeP/Gallery), `gray4`, `gray16`.
- **Formats:** `.png` (default) or `.bmp` (firmware without a PNG decoder).
- **Fit:** `fit=cover` (fill, default) or `fit=contain` (letterbox on white).

See [`ROADMAP.md`](ROADMAP.md) (Track B).

## 🏛️ VRA Core Metadata Architecture

Screen Docent utilizes the **Visual Resources Association (VRA) Core** schema for its internal SQLite database design (`models.py`). This guarantees museum-quality structural integrity.
Supported schema properties mapped automatically by the AI include:
*   `title`
*   `agent_name` & `agent_role` (e.g., Maker, Artist, Photographer)
*   `creation_date` & `date_display` (e.g., 'c. 1890', '19th century')
*   `cultural_context` (e.g., 'Dutch', 'Edo Period')
*   `medium` (e.g., 'Oil on canvas')
*   `description_narrative` (Generated 2-sentence museum blurbs)
*   `tags` (Automated visual extraction tags)

## 🛠️ Configuration Hierarchy

Screen Docent uses a strict priority system for settings like `cycle_time`, `mode`, and `shuffle`:

1.  **URL Parameters:** `?mode=static-crop&cycle_time=60` (Highest Priority)
2.  **Playlist Defaults:** Configured per collection in the Admin UI.
3.  **Global Defaults:** System-wide fallbacks.

## 📖 Documentation
For URL parameters, the **Raspberry Pi appliance** how-to, **e-ink / "dumb" frame** setup, and hardware tips, visit the internal **Help & Docs** page at `http://localhost:8000/help` from your running server.

## 🔐 Advanced: Enabling HTTPS (SSL Secure Contexts)
If you intend to host the Admin UI on a semi-public LAN or want strict clipboard/PWA integration, **HTTPS is strongly recommended.**

Because Screen-Docent streams heavily to headless browsers (Smart TVs, Raspbian Kiosks), injecting self-signed SSL certificates directly into the Python backend breaks headless players with un-bypassable `ERR_CERT_AUTHORITY_INVALID` errors.

**Best Practice:** Keep the Screen-Docent python app running natively on `http://localhost:8000` and deploy a lightweight reverse proxy in front of it.
*   **[Caddy](https://caddyserver.com/):** The easiest solution. A 3-line `Caddyfile` will automatically generate trusted local certificates and securely route traffic.
*   **[mkcert](https://github.com/FiloSottile/mkcert):** Use this to natively forge locally trusted SSL root CAs on your operating system without triggering kiosk warnings.

---
*Built for art lovers, powered by AI.*

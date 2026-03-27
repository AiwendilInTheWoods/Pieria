# 🖼️ Screen Docent

**Screen Docent** is an open-source, AI-powered digital art curator and signage platform. It transforms any TV or monitor into a high-end museum display, complete with autonomous artwork analysis, intelligent metadata generation, and instant mobile remote control.

![Screen Docent Logo](static/logo.svg)

## ✨ Features

*   **🕵️ Museum Art Scouts:** Effortlessly search and pull high-res masterpieces directly from world-class APIs (The Met, Art Institute of Chicago, SMK, Cleveland) straight into your discovery queue. Supports premium integrations for Rijksmuseum, Harvard Art Museums, Smithsonian, and Europeana.
*   **🧠 Vision RAG Curator:** Automatically generates museum-grade VRA Core metadata (titles, narrative descriptions, display dates, cultural contexts, tags) for all uploaded or scouted images using the Gemini 2.5 Flash Vision pipeline and Wikipedia grounding.
*   **🏛 VRA Core Database:** Built on the established Visual Resources Association schema, securely housing rich metadata alongside dynamic crop data and playlists.
*   **📱 WebSocket Remote:** A mobile-first, no-refresh PWA remote to switch playlists, change modes, and trigger placards instantly.
*   **📺 Multi-Display Support:** Targeted routing using unique display IDs allows a single server to manage different artwork streams across multiple TVs.
*   **🎨 Advanced Rendering:** Choose between cinematic Ken Burns pans, static user-defined crops, or blurred matte effects.
*   **⚖️ Hierarchical Config:** Precise control via URL parameters that override playlist and global defaults.
*   **🔒 Human-in-the-Loop:** A dedicated Review Queue to audit and refine AI-generated content before it goes live to your screens.
*   **💾 Persistent & Safe:** SQLite-backed state with automatic migrations and Docker volume persistence.

## 🚀 Quickstart Deployment

The fastest way to get Screen Docent running is using Docker.

### 1. Prerequisites
*   [Docker](https://docs.docker.com/get-docker/)
*   [Docker Compose](https://docs.docker.com/compose/install/)

### 2. Configure Environment
Create a `.env` file in the project root:
```bash
# Get your free key at https://aistudio.google.com/
GEMINI_API_KEY=your_api_key_here
```

### 3. Launch
```bash
# Clone and enter the repo
git clone https://github.com/your-username/screen-docent.git
cd screen-docent

# Build and start
docker compose up -d --build
```

### 4. Access the System
*   **Admin Dashboard:** `http://localhost:8000/admin` (Upload, discover, and manage art)
*   **Main Display:** `http://localhost:8000/` (Point your TV browser here)
*   **Mobile Remote:** `http://localhost:8000/remote` (Control from your phone)

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
For a full list of URL parameters and hardware optimization tips (like using Fully Kiosk Browser), visit the internal **Help & Docs** page at `http://localhost:8000/help` from your running server.

## 🔐 Advanced: Enabling HTTPS (SSL Secure Contexts)
If you intend to host the Admin UI on a semi-public LAN or want strict clipboard/PWA integration, **HTTPS is strongly recommended.**

Because Screen-Docent streams heavily to headless browsers (Smart TVs, Raspbian Kiosks), injecting self-signed SSL certificates directly into the Python backend breaks headless players with un-bypassable `ERR_CERT_AUTHORITY_INVALID` errors.

**Best Practice:** Keep the Screen-Docent python app running natively on `http://localhost:8000` and deploy a lightweight reverse proxy in front of it.
*   **[Caddy](https://caddyserver.com/):** The easiest solution. A 3-line `Caddyfile` will automatically generate trusted local certificates and securely route traffic.
*   **[mkcert](https://github.com/FiloSottile/mkcert):** Use this to natively forge locally trusted SSL root CAs on your operating system without triggering kiosk warnings.

---
*Built for art lovers, powered by AI.*

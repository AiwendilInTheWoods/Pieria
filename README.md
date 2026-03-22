# 🖼️ Screen Docent

**Screen Docent** is an open-source, AI-powered digital art curator and signage platform. It transforms any TV or monitor into a high-end museum display, complete with autonomous artwork analysis, intelligent metadata generation, and instant mobile remote control.

![Screen Docent Logo](static/logo.svg)

## ✨ Features

*   **🤖 AI Curator Agent:** Automatically generates museum-grade titles, descriptions, and tags for uploaded images using Gemini 2.5 Flash.
*   **📱 WebSocket Remote:** A mobile-first, no-refresh PWA remote to switch playlists, change modes, and trigger placards instantly.
*   **📺 Multi-Display Support:** Targeted routing using unique display IDs allows a single server to manage different artwork streams across multiple TVs.
*   **🎨 Advanced Rendering:** Choose between cinematic Ken Burns pans, static user-defined crops, or blurred matte effects.
*   **⚖️ Hierarchical Config:** Precise control via URL parameters that override playlist and global defaults.
*   **🔒 Human-in-the-Loop:** A dedicated Review Queue to audit and refine AI-generated content before it goes live.
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
*   **Admin Dashboard:** `http://localhost:8000/admin` (Upload and manage art)
*   **Main Display:** `http://localhost:8000/` (Point your TV browser here)
*   **Mobile Remote:** `http://localhost:8000/remote` (Control from your phone)

## 🛠️ Configuration Hierarchy

Screen Docent uses a strict priority system for settings like `cycle_time`, `mode`, and `shuffle`:

1.  **URL Parameters:** `?mode=static-crop&cycle_time=60` (Highest Priority)
2.  **Playlist Defaults:** Configured per collection in the Admin UI.
3.  **Global Defaults:** System-wide fallbacks.

## 📖 Documentation
For a full list of URL parameters and hardware optimization tips (like using Fully Kiosk Browser), visit the internal **Help & Docs** page at `http://localhost:8000/help`.

---
*Built for art lovers, powered by AI.*

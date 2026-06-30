"""
Shared configuration constants for the Screen Docent application.
Extracted from app.py to break circular import dependencies.
"""

import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

ARTWORK_ROOT = Path(os.getenv("ARTWORK_ROOT", "Artwork"))
LIBRARY_DIR = ARTWORK_ROOT / "_Library"

# Wikimedia (and most museum/image hosts) reject the default httpx User-Agent; every outbound image
# fetch must send this descriptive UA. Lives here (not app.py) so the offline tools/ scripts can
# reuse it without importing the FastAPI app.
SD_USER_AGENT = "ScreenDocent/1.0 (https://github.com/AiwendilInTheWoods/Screen-Docent; art display) httpx"

# Deployment mode. Only the all-in-one appliance compose override sets SD_APPLIANCE_MODE=all-in-one;
# the generic/MS-01 server and thin-client (display-only) topologies leave it unset. Gates the
# host-health console + the GUI update bridge — surfaces that only make sense when the server runs
# ON the device being managed. A thin client's admin is served by a remote box that lacks this flag,
# so those surfaces correctly never appear there.
APPLIANCE_MODE = os.getenv("SD_APPLIANCE_MODE", "").strip().lower()  # "" | "all-in-one"
IS_APPLIANCE = APPLIANCE_MODE == "all-in-one"

# Where the appliance bridge exchanges files with the host helper. ./data is already bind-mounted to
# /app/data, so the unprivileged container can write here and a root systemd watcher can read it.
APPLIANCE_DIR = Path(os.getenv("APPLIANCE_DIR", "data/appliance"))

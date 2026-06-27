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

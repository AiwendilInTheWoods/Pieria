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

"""The factory seed must use the rot-proof Special:FilePath URL form (no network).

Wikimedia now 400s hand-built `/thumb/.../{N}px-` URLs at non-whitelisted widths, which left a fresh
server booting with an empty library. These tests lock the seed to the `Special:FilePath?width=N`
form (the same one the live scout/resolver use, which Wikimedia resolves server-side) so it can't
silently regress. Mirrors the URL-form assertion style in tests/test_scouts.py.
"""
import json
import re
from pathlib import Path

import pytest

SEED_FILE = Path(__file__).resolve().parent.parent / "static" / "factory_seed.json"
SEEDS = json.loads(SEED_FILE.read_text())
LEGACY_THUMB = re.compile(r"/thumb/.*\d+px-")


def _urls(item):
    return [item["source_url"], item["thumbnail_url"]]


def test_seed_has_items():
    assert len(SEEDS) == 25


@pytest.mark.parametrize("item", SEEDS, ids=lambda i: i.get("title", "?"))
def test_seed_urls_are_filepath_form(item):
    for url in _urls(item):
        assert "commons.wikimedia.org/wiki/Special:FilePath/" in url, url


def test_seed_source_and_thumb_widths():
    assert all("width=3840" in i["source_url"] for i in SEEDS)
    assert all("width=600" in i["thumbnail_url"] for i in SEEDS)


def test_no_legacy_thumb_urls_remain():
    bad = [u for i in SEEDS for u in _urls(i) if LEGACY_THUMB.search(u)]
    assert bad == [], f"legacy /thumb/...px- URLs must not return: {bad}"


def test_no_double_encoding():
    # The migration unquotes the captured filename before _wikimedia_filepath re-quotes it; a stray
    # %25 would mean a filename like "%2C" got encoded twice (-> a broken, un-resolvable URL).
    bad = [u for i in SEEDS for u in _urls(i) if "%25" in u]
    assert bad == [], f"double-encoded URLs: {bad}"

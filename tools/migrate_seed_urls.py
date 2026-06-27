#!/usr/bin/env python3
"""Migrate static/factory_seed.json Wikimedia image URLs to the rot-proof Special:FilePath form.

Wikimedia Commons now rejects hand-built thumbnail URLs at non-whitelisted widths
(`/commons/thumb/.../2000px-File.jpg` -> HTTP 400 "Use thumbnail sizes listed on w.wiki/GHai").
The factory seed was the only place still building those by hand; the live scout and catalog
resolver already use `_wikimedia_filepath` (Special:FilePath?width=N), which Wikimedia resolves to
a *servable* bucket server-side and is therefore immune to the whitelist.

This rewrites each seed item's `source_url`/`thumbnail_url` to that form, reusing the canonical
builder so there's no drift. It's a maintainer tool (not in the runtime image) and is safe to
re-run if Wikimedia changes the rules again.

    python -m tools.migrate_seed_urls            # rewrite the seed file in place
    python -m tools.migrate_seed_urls --dry-run  # print old -> new, write nothing
    python -m tools.migrate_seed_urls --check    # exit 1 if any legacy /thumb/...px- URL remains
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from urllib.parse import unquote

from scout import _wikimedia_filepath  # the one canonical Wikimedia URL builder — reuse, don't drift

REPO_ROOT = Path(__file__).resolve().parent.parent
SEED_FILE = REPO_ROOT / "static" / "factory_seed.json"

# Source at 4K-capable width (matches the live resolver, scout.py:864); thumbnail small. Because
# Special:FilePath rounds the request up to the nearest servable bucket, the exact value is not
# load-bearing — only that it clears the >=2000px display gate for the source.
SOURCE_WIDTH = 3840
THUMB_WIDTH = 600

# upload.wikimedia.org/wikipedia/commons/thumb/<a>/<ab>/<File>/<N>px-<File>
_THUMB_RE = re.compile(r"/commons/thumb/[0-9a-f]/[0-9a-f]{2}/(?P<file>[^/]+)/\d+px-[^/]+$")
# Any remaining hand-built thumbnail URL at a pixel width (the thing we're eradicating).
_LEGACY_RE = re.compile(r"/thumb/.*\d+px-")


def _commons_filename_from_thumb(url: str) -> str | None:
    """Return the *decoded* Commons File name from a /thumb/ URL, or None if it isn't one.

    Decoded on purpose: the path segment is already percent-encoded, and `_wikimedia_filepath`
    re-encodes via quote(); passing the encoded form would double-encode (e.g. %2C -> %252C).
    """
    m = _THUMB_RE.search(url or "")
    return unquote(m.group("file")) if m else None


def rebuild_item_urls(item: dict) -> tuple[str, str] | None:
    """(source_url, thumbnail_url) in Special:FilePath form, or None if no filename recoverable."""
    fname = (_commons_filename_from_thumb(item.get("source_url", ""))
             or _commons_filename_from_thumb(item.get("thumbnail_url", "")))
    if not fname:
        return None
    return _wikimedia_filepath(fname, SOURCE_WIDTH), _wikimedia_filepath(fname, THUMB_WIDTH)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true", help="print old -> new and write nothing")
    ap.add_argument("--check", action="store_true",
                    help="exit 1 if any legacy /thumb/...px- URL remains; write nothing")
    ap.add_argument("--file", type=Path, default=SEED_FILE, help="seed JSON path")
    args = ap.parse_args(argv)

    seeds = json.loads(args.file.read_text())

    if args.check:
        bad = [(i.get("title"), u) for i in seeds
               for u in (i.get("source_url"), i.get("thumbnail_url")) if u and _LEGACY_RE.search(u)]
        if bad:
            print(f"FAIL: {len(bad)} legacy /thumb/...px- URL(s) remain:")
            for t, u in bad:
                print(f"  - {t}: {u}")
            return 1
        print(f"OK: no legacy thumb URLs in {args.file.name} ({len(seeds)} items)")
        return 0

    unmatched, changes = [], []
    for item in seeds:
        rebuilt = rebuild_item_urls(item)
        if rebuilt is None:
            unmatched.append(item.get("title", "<untitled>"))
            continue
        new_source, new_thumb = rebuilt
        changes.append((item.get("title"), item.get("source_url"), new_source,
                        item.get("thumbnail_url"), new_thumb))
        if not args.dry_run:
            item["source_url"], item["thumbnail_url"] = new_source, new_thumb

    if unmatched:
        print(f"FAIL: could not extract a Commons filename for {len(unmatched)} item(s); wrote nothing:")
        for t in unmatched:
            print(f"  - {t}")
        return 1

    if args.dry_run:
        for title, os_, ns, ot, nt in changes:
            print(f"• {title}\n    source:    {os_}\n            -> {ns}\n"
                  f"    thumbnail: {ot}\n            -> {nt}")
        print(f"\n(dry-run) would migrate {len(changes)}/{len(seeds)} items")
        return 0

    args.file.write_text(json.dumps(seeds, indent=2, ensure_ascii=False) + "\n")
    print(f"migrated {len(changes)}/{len(seeds)} items -> {args.file}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

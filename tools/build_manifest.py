"""Third-party publisher CLI — build a signed Manifest v2 collection from a CSV.

The command-line sibling of the Publisher Studio: point it at a CSV of items (one artwork per row,
each with a PUBLIC image URL you host yourself) plus a little collection metadata, and it assembles,
validates, and optionally signs a manifest.json that anyone can subscribe to. Shares the assembly +
signing engine with the Studio via `publisher.py`, so GUI and CLI emit identical manifests.

NOT to be confused with `tools/build_catalog.py`, which is the maintainer's museum-scraper for the
bundled catalog. This tool hosts nothing and scrapes nothing — your images stay on your own hosting;
the manifest only points at them.

  python -m tools.build_manifest --csv items.csv --meta meta.json
  python -m tools.build_manifest --csv items.csv --slug my-art --title "My Art" \
      --publisher-id jane --publisher-name "Jane Doe" --key <private-b64> --out my-art.json

CSV columns (header row required; only `image_url` + `title` are required, the rest optional, unknown
columns ignored):
  image_url, title, artist, artist_role, date, creation_date, medium, culture,
  tags, placard, license, attribution, rights_holder, thumbnail_url,
  width, height, focal_x, focal_y, id
  · tags        — "|"- or ","-separated (use "|" if your tags contain commas)
  · focal_x/y   — floats 0..1; both required to emit a focal point
  · width/height— integers (pixels)

Meta (via --meta JSON and/or flags; flags override the JSON file):
  { "slug": "...", "title": "...", "description": "...", "default_license": "...",
    "publisher": { "id": "...", "name": "...", "url": "..." } }

Signing is optional: with --key the manifest is signed (subscribers can verify it; gets you the
"verified" tier once your key is in the registry). Without --key it's published unsigned and loads as
"community" tier — perfectly fine.
"""

import argparse
import csv
import json
import sys
from datetime import UTC, datetime

import publisher


def _load_meta(args) -> dict:
    """Merge --meta JSON (if any) with individual flags; flags win."""
    meta: dict = {}
    if args.meta:
        with open(args.meta) as f:
            meta = json.load(f) or {}
    meta.setdefault("publisher", {})
    for flag, key in (("slug", "slug"), ("title", "title"), ("description", "description"),
                      ("default_license", "default_license"), ("cover_image", "cover_image")):
        val = getattr(args, flag, None)
        if val:
            meta[key] = val
    for flag, key in (("publisher_id", "id"), ("publisher_name", "name"), ("publisher_url", "url")):
        val = getattr(args, flag, None)
        if val:
            meta["publisher"][key] = val
    return meta


def _load_rows(csv_path: str) -> list[dict]:
    with open(csv_path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Build (and optionally sign) a Manifest v2 from a CSV.")
    ap.add_argument("--csv", required=True, help="path to the items CSV")
    ap.add_argument("--meta", help="path to a collection-meta JSON file")
    ap.add_argument("--slug", help="collection id (slug); overrides --meta")
    ap.add_argument("--title", help="collection title; overrides --meta")
    ap.add_argument("--description", help="collection description")
    ap.add_argument("--default-license", dest="default_license", help="license applied to items that omit one")
    ap.add_argument("--cover-image", dest="cover_image", help="URL of the collection cover image")
    ap.add_argument("--publisher-id", dest="publisher_id", help="publisher id")
    ap.add_argument("--publisher-name", dest="publisher_name", help="publisher display name")
    ap.add_argument("--publisher-url", dest="publisher_url", help="publisher homepage url")
    ap.add_argument("--key", help="base64 Ed25519 private key — signs the manifest when given")
    ap.add_argument("--public", help="base64 public key (derived from --key if omitted)")
    ap.add_argument("--out", default="manifest.json", help="output path (default: manifest.json)")
    args = ap.parse_args(argv)

    meta = _load_meta(args)
    if not meta.get("slug") or not meta.get("title"):
        print("error: a collection slug and title are required (via --meta or --slug/--title).", file=sys.stderr)
        return 2
    rows = _load_rows(args.csv)
    if not rows:
        print(f"error: no rows found in {args.csv}.", file=sys.stderr)
        return 2

    generated_at = datetime.now(UTC).isoformat(timespec="seconds")
    if args.key:
        manifest, errors = publisher.assemble_validate_sign(
            meta, rows, args.key, args.public, generated_at=generated_at)
    else:
        manifest, errors = publisher.assemble_and_validate(meta, rows, generated_at=generated_at)

    if errors:
        print("error: manifest is invalid — nothing written:", file=sys.stderr)
        for e in errors:
            print(f"  · {e}", file=sys.stderr)
        return 1

    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2, ensure_ascii=False)
    signed = "signed" if args.key else "unsigned (community tier)"
    print(f"Wrote {args.out}: {len(manifest['items'])} items, {signed}, "
          f"publisher '{manifest.get('publisher', {}).get('id')}'.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

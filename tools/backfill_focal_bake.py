"""Bake focal points (from the in-IDE agent fan-out) into the catalog collection JSONs or the seed.

Reads a results file — a JSON array of {"source_url": "...", "focal_point": [x, y]} — matches each to
an item by source_url, and sets item["focal_point"] = [x, y]. Dry-run by default.

Usage (from repo root):
  venv/bin/python -m tools.backfill_focal_bake /tmp/sd_focal/results.json            # catalog, preview
  venv/bin/python -m tools.backfill_focal_bake /tmp/sd_focal/results.json --apply     # catalog, write
  venv/bin/python -m tools.backfill_focal_bake /tmp/sd_focal_seed/results.json --seed --apply

Matches each file's on-disk format (catalog: indent=1; seed: indent=2) so the only change is the
focal_point value. Skips _pending_artic.json.
"""
import argparse
import json
from pathlib import Path

CAT = Path("static/catalog")
SEED = Path("static/factory_seed.json")
SKIP = {"index.json", "_pending_artic.json"}


def clamp(v: float) -> float:
    return round(max(0.0, min(1.0, float(v))), 4)


def apply_focal(items, by_url) -> int:
    changed = 0
    for it in items:
        u = it.get("source_url")
        if u in by_url and it.get("focal_point") != by_url[u]:
            it["focal_point"] = by_url[u]
            changed += 1
    return changed


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("results")
    ap.add_argument("--seed", action="store_true", help="bake into factory_seed.json instead of the catalog")
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    by_url = {}
    for r in json.loads(Path(args.results).read_text()):
        u, f = r.get("source_url"), r.get("focal_point")
        if u and isinstance(f, (list, tuple)) and len(f) == 2:
            try:
                by_url[u] = [clamp(f[0]), clamp(f[1])]
            except (TypeError, ValueError):
                pass
    print(f"{len(by_url)} focal points from results")

    total = 0
    if args.seed:
        d = json.loads(SEED.read_text())
        changed = apply_focal(d, by_url)
        if changed:
            total = changed
            print(f"  factory_seed: {changed}")
            if args.apply:
                SEED.write_text(json.dumps(d, indent=2, ensure_ascii=False) + "\n")
    else:
        for path in sorted(CAT.glob("*.json")):
            if path.name in SKIP:
                continue
            d = json.loads(path.read_text())
            if not isinstance(d, dict):
                continue
            changed = apply_focal(d.get("items", []), by_url)
            if changed:
                total += changed
                print(f"  {path.stem}: {changed}")
                if args.apply:
                    path.write_text(json.dumps(d, indent=1, ensure_ascii=False))

    print(f"{'APPLIED' if args.apply else 'DRY RUN'}: {total} items updated"
          f"{' (re-run with --apply)' if not args.apply and total else ''}")


if __name__ == "__main__":
    main()

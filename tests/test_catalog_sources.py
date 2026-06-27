"""The shipped catalog must not point at artic.edu, and index/pending bookkeeping must stay honest.

AIC's IIIF host now sits behind a Cloudflare challenge that 403s every server-side client (see memory
catalog-host-rot). tools/resource_artic.py re-sourced the matchable works onto Wikimedia Commons via
exact AIC-accession identity and PARKED the rest into _pending_artic.json (kept, recoverable). These
offline tests lock that state in: no artic URL may sneak back into a *shipped* collection, the index
counts must match reality, and every parked item must retain the accession needed to hand-source it.
"""
import json
from pathlib import Path

CATALOG_DIR = Path(__file__).resolve().parent.parent / "static" / "catalog"
PENDING = CATALOG_DIR / "_pending_artic.json"


def _collection_files():
    return [f for f in sorted(CATALOG_DIR.glob("*.json"))
            if f.name not in ("index.json", PENDING.name)]


def test_no_artic_urls_in_shipped_catalog():
    bad = []
    for f in _collection_files():
        for it in json.loads(f.read_text()).get("items", []):
            for key in ("source_url", "thumbnail_url"):
                if "artic.edu" in it.get(key, ""):
                    bad.append(f"{f.name}: {it.get('title')}")
    assert bad == [], f"shipped catalog still references the Cloudflare-walled artic host: {bad}"


def test_index_has_no_artic_cover_thumbnails():
    index = json.loads((CATALOG_DIR / "index.json").read_text())
    bad = [c["id"] for c in index["collections"] if "artic.edu" in c.get("cover_thumbnail", "")]
    assert bad == [], f"index cover_thumbnail still points at artic: {bad}"


def test_index_counts_match_collection_files():
    index = json.loads((CATALOG_DIR / "index.json").read_text())
    for coll in index["collections"]:
        cfile = CATALOG_DIR / f"{coll['id']}.json"
        if cfile.exists():
            actual = len(json.loads(cfile.read_text()).get("items", []))
            assert coll["count"] == actual, f"{coll['id']}: index says {coll['count']}, file has {actual}"


def test_parked_items_retain_handsourcing_metadata():
    if not PENDING.exists():
        return  # nothing parked is a valid state
    parked = json.loads(PENDING.read_text())
    assert parked, "pending file exists but is empty"
    for p in parked:
        assert p.get("_park_reason"), f"parked item missing reason: {p.get('title')}"
        assert p["_park_reason"] != "unknown", f"parked item has unknown reason: {p.get('title')}"
        # The AIC accession is the key for hand-sourcing; it should survive parking.
        assert "_aic_accession" in p, f"parked item missing accession field: {p.get('title')}"

"""
Unit tests for the curated-pick resolvers' matching precision (no network).

Regression guard: a pick must not resolve to a same-keyword work by a *different* artist
(e.g. Tiffany's "Magnolia and Irises" once matched Monet's "Irises"). The museum resolver
gates on both a strong title ratio and an artist check (_artist_ok).
"""

import asyncio

import pytest

from tools import sources


# ----------------------------------------------------------------- _artist_ok
@pytest.mark.parametrize("want,cand,expected", [
    ("Tiffany Studios", "Claude Monet", False),       # the original bug
    ("Walter Crane", "Isoda Koryusai", False),        # "Crane" is a first name here, no match
    ("Vincent van Gogh", "Gogh, Vincent van", True),  # surname present (reordered)
    ("Rembrandt van Rijn", "Unknown", True),          # anonymous museum record -> allow
    ("Ancient Egyptian", "", True),                   # no candidate artist -> allow
    ("", "Claude Monet", True),                        # pick has no artist -> allow
    ("Claude Monet", "Claude Monet", True),           # exact
])
def test_artist_ok(want, cand, expected):
    assert sources._artist_ok(want, cand) is expected


# ----------------------------------------------------------------- resolve_museums
def _museum_item(title, artist):
    return {"title": title, "agent_name": artist, "source_url": f"http://x/{title}",
            "thumbnail_url": "http://x/t"}


def _patch_museums(monkeypatch, items):
    async def fake_from_museums(db, sources_, queries, per_query=6):
        return items
    monkeypatch.setattr(sources, "from_museums", fake_from_museums)


def test_resolve_museums_rejects_wrong_artist(monkeypatch):
    # The museum search for Tiffany's piece returns Monet's "Irises" — must be rejected.
    _patch_museums(monkeypatch, [_museum_item("Irises", "Claude Monet")])
    got = asyncio.run(sources.resolve_museums(None, "Magnolia and Irises", "Tiffany Studios", ["met"]))
    assert got is None


def test_resolve_museums_rejects_weak_title(monkeypatch):
    _patch_museums(monkeypatch, [_museum_item("A Completely Different Work", "Tiffany Studios")])
    got = asyncio.run(sources.resolve_museums(None, "Magnolia and Irises", "Tiffany Studios", ["met"]))
    assert got is None


def test_resolve_museums_accepts_correct_match(monkeypatch):
    _patch_museums(monkeypatch, [
        _museum_item("Irises", "Claude Monet"),                 # decoy, wrong artist
        _museum_item("Magnolia and Irises", "Tiffany Studios"),  # the right one
    ])
    got = asyncio.run(sources.resolve_museums(None, "Magnolia and Irises", "Tiffany Studios", ["met"]))
    assert got is not None
    assert got["agent_name"] == "Tiffany Studios"

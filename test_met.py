#!/usr/bin/env python3
"""Quick test to debug MetMuseumScout directly."""
import asyncio
import sys
sys.path.insert(0, '.')

from query_classifier import SearchIntent
from scout import MetMuseumScout

async def test():
    intent = SearchIntent(
        query_type="artist",
        canonical_name="Vincent van Gogh",
        era_hint="Post-Impressionism"
    )
    
    scout = MetMuseumScout()
    
    print(f"Testing with intent: {intent}")
    print(f"  query_type = {intent.query_type}")
    print(f"  canonical_name = {intent.canonical_name}")
    
    results = await scout.find_art(query="Van Gogh", intent=intent, offset=0, limit=5)
    
    print(f"\nGot {len(results)} results:")
    for i, r in enumerate(results):
        print(f"  {i+1}. {r['proposed_title']} by {r['proposed_artist']}")
        print(f"     URL: {r['source_url'][:80]}...")

if __name__ == "__main__":
    asyncio.run(test())

import asyncio
import logging
from scout import *

logging.basicConfig(level=logging.INFO)

async def test():
    scouts = [ChicagoArtScout(), MetMuseumScout(), ClevelandArtScout(), RijksmuseumScout(), SmkScout(), VamScout(), WhitneyScout(), GettyScout(), MaasScout()]
    for s in scouts:
        try:
            res = await s.find_art("impressionism")
            print(f">>> {s.__class__.__name__}: {len(res)} results")
            if res:
                print(f"Sample: {res[0].get('proposed_title')} by {res[0].get('proposed_artist')}")
        except Exception as e:
            print(f">>> {s.__class__.__name__}: error {e}")

if __name__ == "__main__":
    asyncio.run(test())

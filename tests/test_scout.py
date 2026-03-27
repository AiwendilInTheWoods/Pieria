import pytest
import respx
import httpx
from scout import ChicagoArtScout, SmkScout, run_scouts
from models import DiscoveryQueueModel

@pytest.mark.asyncio
@respx.mock
async def test_chicago_scout_success():
    scout = ChicagoArtScout()
    
    # Mock the API response for 200 OK
    mock_url = scout.API_URL
    respx.get(mock_url).respond(
        status_code=200,
        json={
            "data": [
                {
                    "title": "A Beautiful Mocked Painting",
                    "artist_title": "The Ghost in the Machine",
                    "image_id": "mock-uuid-1234"
                }
            ]
        }
    )
    
    artworks = await scout.find_art("painting")
    
    assert len(artworks) == 1
    assert artworks[0]["proposed_title"] == "A Beautiful Mocked Painting"
    assert artworks[0]["proposed_artist"] == "The Ghost in the Machine"
    assert "mock-uuid-1234" in artworks[0]["source_url"]


@pytest.mark.asyncio
@respx.mock
async def test_smk_scout_error_handling():
    scout = SmkScout()
    
    # Mock a massive server crash (500 Internal Server Error)
    mock_url = scout.API_URL
    respx.get(mock_url).respond(status_code=500)
    
    artworks = await scout.find_art("painting")
    
    # Assert graceful handling preventing server crash
    assert artworks == []
    
    # Mock a Network Timeout
    respx.get(mock_url).mock(side_effect=httpx.TimeoutException("Timeout"))
    
    artworks_timeout = await scout.find_art("painting")
    assert artworks_timeout == []


@pytest.mark.asyncio
@respx.mock
async def test_run_scouts_deduplication(testing_session):
    # Mock both Chicago and SMK endpoints to return unique items
    respx.get(ChicagoArtScout.API_URL).respond(
        status_code=200,
        json={"data": [{"title": "Art 1", "image_id": "uuid-1"}]}
    )
    
    respx.get(SmkScout.API_URL).respond(
        status_code=200,
        json={"items": [
            {"image_native": "https://example.com/uuid-1/full.jpg", "titles": [{"title": "SMK Art 1"}]},
            {"image_native": "https://example.com/uuid-2/full.jpg", "titles": [{"title": "SMK Art 2"}]}
        ]}
    )
    
    # 1. Run the orchestrator (Initial Sync)
    await run_scouts(db=testing_session, sources=["chicago", "smk"])
    
    items = testing_session.query(DiscoveryQueueModel).all()
    assert len(items) == 3 # 1 from chicago, 2 from SMK
    
    # 2. Run it again! (Simulating a second cron run finding the same items)
    await run_scouts(db=testing_session, sources=["chicago", "smk"])
    
    items_after = testing_session.query(DiscoveryQueueModel).all()
    # It should still be exactly 3 items, verifying deduplication logic!
    assert len(items_after) == 3 

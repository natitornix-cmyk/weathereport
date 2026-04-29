from __future__ import annotations

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential

# Singapore National Environment Agency, free public API.
MSS_24H = "https://api-open.data.gov.sg/v2/real-time/api/twenty-four-hr-forecast"


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=1, max=8))
async def fetch_singapore_24h() -> dict | None:
    """Return latest MSS 24-hour forecast payload, or None on failure.

    Useful as a sanity reference vs Open-Meteo for Changi.
    """
    async with httpx.AsyncClient(timeout=15) as client:
        r = await client.get(MSS_24H)
        if r.status_code != 200:
            return None
        return r.json()


def extract_high_low_c(payload: dict | None) -> tuple[float | None, float | None]:
    if not payload:
        return None, None
    try:
        recs = payload["data"]["records"]
        if not recs:
            return None, None
        general = recs[0]["general"]
        temp = general.get("temperature") or {}
        return temp.get("high"), temp.get("low")
    except (KeyError, IndexError, TypeError):
        return None, None

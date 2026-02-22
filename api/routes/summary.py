"""
FILE: api/routes/summary.py
Role: POST /api/tile/{tile_id}/summary?sort={sort} — AI executive summary.
Agent boundary: API — summary route
Dependencies: db.py get_conn(); routes/tile.py _get_* helpers; anthropic SDK
Output: {"summary": "<2-3 plain-text sentences>"}
How to test:
  curl -X POST "http://localhost:8000/api/tile/1/summary?sort=energy"
  Expect: {"summary": "<2-3 sentences, no markdown>"}
"""

import os
import json
from fastapi import APIRouter, Depends, Query, HTTPException, Path
from typing import Literal, Any
import asyncpg
import anthropic

from db import get_conn
from routes.tile import (
    _get_overall, _get_energy, _get_environment,
    _get_cooling, _get_connectivity, _get_planning,
)

router = APIRouter()

SortType = Literal["overall", "energy", "environment", "cooling", "connectivity", "planning"]

_DISPATCH = {
    "overall": _get_overall,
    "energy": _get_energy,
    "environment": _get_environment,
    "cooling": _get_cooling,
    "connectivity": _get_connectivity,
    "planning": _get_planning,
}

SYSTEM_PROMPT = (
    "You are a concise data analyst writing for a non-technical executive audience. "
    "Given JSON metric data for an Irish land tile evaluated for data centre suitability, "
    "write exactly 2–3 plain-text sentences summarising the key findings for the given "
    "sort category. Do NOT use markdown, bullet points, or headings. "
    "Focus on what matters most for a site-selection decision."
)


@router.post("/tile/{tile_id}/summary")
async def tile_summary(
    tile_id: int = Path(..., description="Tile primary key"),
    sort: SortType = Query(..., description="Active sort key"),
    conn: asyncpg.Connection = Depends(get_conn),
) -> dict[str, str]:
    """Generate an AI executive summary for a tile's sort-specific data."""

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise HTTPException(status_code=500, detail="ANTHROPIC_API_KEY not configured")

    # Fetch base tile row (same pattern as tile.py)
    tile_row = await conn.fetchrow(
        "SELECT tile_id, county, grid_ref, ST_X(centroid) AS lng, ST_Y(centroid) AS lat FROM tiles WHERE tile_id = $1",
        tile_id,
    )
    if not tile_row:
        raise HTTPException(status_code=404, detail=f"Tile {tile_id} not found")

    base = {
        "tile_id": tile_row["tile_id"],
        "county": tile_row["county"],
        "grid_ref": tile_row["grid_ref"],
        "centroid": [tile_row["lng"], tile_row["lat"]],
    }

    # Reuse the existing _get_* helper for this sort
    getter = _DISPATCH.get(sort)
    if not getter:
        raise HTTPException(status_code=400, detail=f"Unknown sort: {sort}")

    tile_data = await getter(conn, tile_id, base)

    # Call Claude
    client = anthropic.Anthropic(api_key=api_key)
    message = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=300,
        system=SYSTEM_PROMPT,
        messages=[
            {
                "role": "user",
                "content": (
                    f"Sort category: {sort}\n"
                    f"Tile data:\n{json.dumps(tile_data, default=str)}"
                ),
            }
        ],
    )

    summary_text = message.content[0].text.strip()

    return {"summary": summary_text}

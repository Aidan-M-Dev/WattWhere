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
    "You are a data-centre site-selection analyst writing for a non-technical executive audience. "
    "You are given JSON metric data for a 5 km² land tile in Ireland, scored for data centre suitability. "
    "Write exactly 2–3 plain-text sentences summarising the key findings for the given sort category. "
    "Do NOT use markdown, bullet points, or headings.\n\n"
    "Domain guidance per sort category:\n"
    "- overall: You receive data from ALL sub-categories. Give a holistic site verdict: highlight the composite "
    "score, call out the strongest and weakest sub-scores, flag any hard exclusions or deal-breakers "
    "(e.g. Natura 2000 overlap, poor grid access), and note proximity to existing data centres.\n"
    "- energy: Assess grid connection viability (substation distance/voltage, transmission line proximity), "
    "on-site renewable potential (wind speeds, solar GHI), and the local renewable-vs-fossil generation mix.\n"
    "- environment: Flag any Natura 2000 (SAC/SPA) or NHA overlaps that trigger hard exclusions, "
    "flood/landslide exposure, and water resource availability (aquifer, waterbody proximity).\n"
    "- cooling: Evaluate free-cooling hours, mean temperature and rainfall, "
    "and access to surface water (nearest waterbody, hydrometric flow rates) for cooling loops.\n"
    "- connectivity: Judge fibre broadband tier, distance to INEX peering points (Dublin/Cork), "
    "grid proximity, and road/rail access for equipment delivery.\n"
    "- planning: Summarise zoning suitability (industrial/enterprise land-use %), planning precedent, "
    "proximity to IDA sites, land price, population density, and any relevant planning applications."
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

    if sort == "overall":
        # For overall, fetch all sub-categories so the AI sees full detail
        tile_data = {}
        for key, getter in _DISPATCH.items():
            try:
                tile_data[key] = await getter(conn, tile_id, base)
            except HTTPException:
                tile_data[key] = None
    else:
        getter = _DISPATCH.get(sort)
        if not getter:
            raise HTTPException(status_code=400, detail=f"Unknown sort: {sort}")
        tile_data = await getter(conn, tile_id, base)

    # Call Claude
    client = anthropic.Anthropic(api_key=api_key)
    message = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=500 if sort == "overall" else 300,
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

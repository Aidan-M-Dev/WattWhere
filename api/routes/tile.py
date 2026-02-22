"""
FILE: api/routes/tile.py
Role: GET /api/tile/{tile_id}?sort={sort} — full tile data for sidebar.
Agent boundary: API — tile detail route (§7, §10)
Dependencies: db.py get_conn(); all sort score tables, tile_designation_overlaps,
              tile_planning_applications, composite_weights
Output: Sort-specific JSON payload consumed by useSuitabilityStore.fetchTileDetail()
How to test:
  curl "http://localhost:8000/api/tile/1?sort=overall" | python3 -m json.tool
  Expect: tile_id, county, centroid, score, sort-specific fields

Response shape varies per sort — see ARCHITECTURE.md §5 sidebar specs.
Each response always includes: tile_id, county, grid_ref, centroid, score.

GET /api/tile/{tile_id}/all returns all 6 sorts for a single tile (P2-08).
"""

from fastapi import APIRouter, Depends, Query, HTTPException, Path
from typing import Literal, Any
import asyncpg
import json
from db import get_conn

router = APIRouter()

SortType = Literal["overall", "energy", "environment", "cooling", "connectivity", "planning"]


@router.get("/tile/{tile_id}")
async def get_tile(
    tile_id: int = Path(..., description="Tile primary key"),
    sort: SortType = Query(..., description="Active sort key"),
    conn: asyncpg.Connection = Depends(get_conn),
) -> dict[str, Any]:
    """
    Return full tile data for the active sort's sidebar component.

    The response shape is sort-specific (see ARCHITECTURE.md §5 sidebar specs).
    Always includes: tile_id, county, grid_ref, centroid [lng, lat], score.
    """
    tile_row = await conn.fetchrow(
        "SELECT tile_id, county, grid_ref, ST_X(centroid) AS lng, ST_Y(centroid) AS lat FROM tiles WHERE tile_id = $1",
        tile_id
    )
    if not tile_row:
        raise HTTPException(status_code=404, detail=f"Tile {tile_id} not found")

    base = {
        "tile_id": tile_row["tile_id"],
        "county": tile_row["county"],
        "grid_ref": tile_row["grid_ref"],
        "centroid": [tile_row["lng"], tile_row["lat"]],
    }

    # Per-sort queries
    if sort == "overall":
        return await _get_overall(conn, tile_id, base)
    elif sort == "energy":
        return await _get_energy(conn, tile_id, base)
    elif sort == "environment":
        return await _get_environment(conn, tile_id, base)
    elif sort == "cooling":
        return await _get_cooling(conn, tile_id, base)
    elif sort == "connectivity":
        return await _get_connectivity(conn, tile_id, base)
    elif sort == "planning":
        return await _get_planning(conn, tile_id, base)
    else:
        raise HTTPException(status_code=400, detail=f"Unknown sort: {sort}")


@router.get("/tile/{tile_id}/all")
async def get_tile_all(
    tile_id: int = Path(..., description="Tile primary key"),
    conn: asyncpg.Connection = Depends(get_conn),
) -> dict[str, Any]:
    """Return data from all 6 sorts for a single tile in one response."""
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

    dispatch = {
        "overall": _get_overall,
        "energy": _get_energy,
        "environment": _get_environment,
        "cooling": _get_cooling,
        "connectivity": _get_connectivity,
        "planning": _get_planning,
    }

    results = {}
    for sort_key, fn in dispatch.items():
        try:
            results[sort_key] = await fn(conn, tile_id, base)
        except HTTPException as e:
            if e.status_code == 404:
                results[sort_key] = None
            else:
                raise

    if all(v is None for v in results.values()):
        raise HTTPException(status_code=404, detail="Tile not found in any sort table")

    return results


async def _get_overall(conn: asyncpg.Connection, tile_id: int, base: dict) -> dict:
    """Fetch overall_scores + composite_weights for overall sidebar."""
    row = await conn.fetchrow(
        """SELECT o.*, cw.energy, cw.connectivity, cw.environment, cw.cooling, cw.planning
           FROM overall_scores o, composite_weights cw
           WHERE o.tile_id = $1 AND cw.id = 1""",
        tile_id
    )
    if not row:
        raise HTTPException(status_code=404, detail=f"No overall score for tile {tile_id}")

    return {
        **base,
        "score": float(row["score"]),
        "energy_score": _f(row["energy_score"]),
        "environment_score": _f(row["environment_score"]),
        "cooling_score": _f(row["cooling_score"]),
        "connectivity_score": _f(row["connectivity_score"]),
        "planning_score": _f(row["planning_score"]),
        "has_hard_exclusion": row["has_hard_exclusion"],
        "exclusion_reason": row["exclusion_reason"],
        "nearest_data_centre_km": _f(row["nearest_data_centre_km"]),
        "weights": {
            "energy": float(row["energy"]),
            "connectivity": float(row["connectivity"]),
            "environment": float(row["environment"]),
            "cooling": float(row["cooling"]),
            "planning": float(row["planning"]),
        },
    }


async def _get_energy(conn: asyncpg.Connection, tile_id: int, base: dict) -> dict:
    """Fetch energy_scores for energy sidebar."""
    row = await conn.fetchrow("SELECT * FROM energy_scores WHERE tile_id = $1", tile_id)
    if not row:
        raise HTTPException(status_code=404, detail=f"No energy score for tile {tile_id}")

    return {
        **base,
        "score": float(row["score"]),
        "wind_speed_50m": _f(row["wind_speed_50m"]),
        "wind_speed_100m": _f(row["wind_speed_100m"]),
        "wind_speed_150m": _f(row["wind_speed_150m"]),
        "solar_ghi": _f(row["solar_ghi"]),
        "grid_proximity": _f(row["grid_proximity"]),
        "nearest_transmission_line_km": _f(row["nearest_transmission_line_km"]),
        "nearest_substation_km": _f(row["nearest_substation_km"]),
        "nearest_substation_name": row["nearest_substation_name"],
        "nearest_substation_voltage": row["nearest_substation_voltage"],
        "grid_low_confidence": row["grid_low_confidence"],
    }


async def _get_environment(conn: asyncpg.Connection, tile_id: int, base: dict) -> dict:
    """Fetch environment_scores + tile_designation_overlaps for environment sidebar."""
    row = await conn.fetchrow("SELECT * FROM environment_scores WHERE tile_id = $1", tile_id)
    if not row:
        raise HTTPException(status_code=404, detail=f"No environment score for tile {tile_id}")

    designations = await conn.fetch(
        "SELECT designation_type, designation_name, designation_id, pct_overlap FROM tile_designation_overlaps WHERE tile_id = $1 ORDER BY pct_overlap DESC",
        tile_id
    )

    return {
        **base,
        "score": float(row["score"]),
        "designation_overlap": _f(row["designation_overlap"]),
        "flood_risk": _f(row["flood_risk"]),
        "landslide_risk": _f(row["landslide_risk"]),
        "has_hard_exclusion": row["has_hard_exclusion"],
        "exclusion_reason": row["exclusion_reason"],
        "intersects_sac": row["intersects_sac"],
        "intersects_spa": row["intersects_spa"],
        "intersects_nha": row["intersects_nha"],
        "intersects_pnha": row["intersects_pnha"],
        "intersects_current_flood": row["intersects_current_flood"],
        "intersects_future_flood": row["intersects_future_flood"],
        "landslide_susceptibility": row["landslide_susceptibility"],
        "designations": [dict(d) for d in designations],
    }


async def _get_cooling(conn: asyncpg.Connection, tile_id: int, base: dict) -> dict:
    """Fetch cooling_scores for cooling sidebar."""
    row = await conn.fetchrow("SELECT * FROM cooling_scores WHERE tile_id = $1", tile_id)
    if not row:
        raise HTTPException(status_code=404, detail=f"No cooling score for tile {tile_id}")

    return {
        **base,
        "score": float(row["score"]),
        "temperature": _f(row["temperature"]),
        "water_proximity": _f(row["water_proximity"]),
        "rainfall": _f(row["rainfall"]),
        "aquifer_productivity": _f(row["aquifer_productivity"]),
        "free_cooling_hours": _f(row["free_cooling_hours"]),
        "nearest_waterbody_name": row["nearest_waterbody_name"],
        "nearest_waterbody_km": _f(row["nearest_waterbody_km"]),
        "nearest_hydrometric_station_name": row["nearest_hydrometric_station_name"],
        "nearest_hydrometric_flow_m3s": _f(row["nearest_hydrometric_flow_m3s"]),
        "aquifer_productivity_rating": row["aquifer_productivity_rating"],
    }


async def _get_connectivity(conn: asyncpg.Connection, tile_id: int, base: dict) -> dict:
    """Fetch connectivity_scores for connectivity sidebar."""
    row = await conn.fetchrow("SELECT * FROM connectivity_scores WHERE tile_id = $1", tile_id)
    if not row:
        raise HTTPException(status_code=404, detail=f"No connectivity score for tile {tile_id}")

    return {
        **base,
        "score": float(row["score"]),
        "broadband": _f(row["broadband"]),
        "ix_distance": _f(row["ix_distance"]),
        "road_access": _f(row["road_access"]),
        "inex_dublin_km": _f(row["inex_dublin_km"]),
        "inex_cork_km": _f(row["inex_cork_km"]),
        "broadband_tier": row["broadband_tier"],
        "nearest_motorway_junction_km": _f(row["nearest_motorway_junction_km"]),
        "nearest_motorway_junction_name": row["nearest_motorway_junction_name"],
        "nearest_national_road_km": _f(row["nearest_national_road_km"]),
        "nearest_rail_freight_km": _f(row["nearest_rail_freight_km"]),
    }


async def _get_planning(conn: asyncpg.Connection, tile_id: int, base: dict) -> dict:
    """Fetch planning_scores + tile_planning_applications for planning sidebar."""
    row = await conn.fetchrow("SELECT * FROM planning_scores WHERE tile_id = $1", tile_id)
    if not row:
        raise HTTPException(status_code=404, detail=f"No planning score for tile {tile_id}")

    apps = await conn.fetch(
        "SELECT app_ref, name, status, app_date, app_type FROM tile_planning_applications WHERE tile_id = $1 ORDER BY app_date DESC NULLS LAST",
        tile_id
    )

    return {
        **base,
        "score": float(row["score"]),
        "zoning_tier": _f(row["zoning_tier"]),
        "planning_precedent": _f(row["planning_precedent"]),
        "pct_industrial": float(row["pct_industrial"]),
        "pct_enterprise": float(row["pct_enterprise"]),
        "pct_mixed_use": float(row["pct_mixed_use"]),
        "pct_agricultural": float(row["pct_agricultural"]),
        "pct_residential": float(row["pct_residential"]),
        "pct_other": float(row["pct_other"]),
        "nearest_ida_site_km": _f(row["nearest_ida_site_km"]),
        "population_density_per_km2": _f(row["population_density_per_km2"]),
        "county_dev_plan_ref": row["county_dev_plan_ref"],
        "land_price_score": _f(row["land_price_score"]),
        "avg_price_per_sqm_eur": _f(row["avg_price_per_sqm_eur"]),
        "transaction_count": int(row["transaction_count"]) if row["transaction_count"] is not None else None,
        "planning_applications": [
            {
                "app_ref": a["app_ref"],
                "name": a["name"],
                "status": a["status"],
                "app_date": a["app_date"].isoformat() if a["app_date"] else None,
                "app_type": a["app_type"],
            }
            for a in apps
        ],
    }


def _f(val) -> float | None:
    """Safe float conversion — returns None if val is None."""
    return float(val) if val is not None else None

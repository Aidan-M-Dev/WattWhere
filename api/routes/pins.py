"""
FILE: api/routes/pins.py
Role: GET /api/pins?sort={sort} — GeoJSON FeatureCollection of pins for a sort.
Agent boundary: API — pins route (§7, §10)
Dependencies: db.py get_conn(); pin tables (pins_overall, pins_energy, etc.) + ida_sites
Output: GeoJSON FeatureCollection consumed by useSuitabilityStore.fetchPins()
How to test:
  curl "http://localhost:8000/api/pins?sort=energy" | python3 -m json.tool
  Expect: {"type": "FeatureCollection", "features": [...]}

ARCHITECTURE RULES:
  - Pins are sort-level, NOT metric-level. No 'metric' param on this endpoint.
  - Switching sub-metric must never trigger a pin refetch.
  - IDA sites are served via JOIN (shared table) for overall and planning sorts.
  - Pin tile_id may be null (coastal/boundary pins outside any tile).

Pin types per sort (ARCHITECTURE.md §5):
  overall:      data_centre (pins_overall) + IDA sites (ida_sites JOIN)
  energy:       wind_farm, transmission_node, substation (pins_energy)
  environment:  sac, spa, nha, pnha, flood_zone (pins_environment)
  cooling:      hydrometric_station, waterbody, met_station (pins_cooling)
  connectivity: internet_exchange, motorway_junction, broadband_area (pins_connectivity)
  planning:     zoning_parcel, planning_application (pins_planning) + IDA sites (ida_sites JOIN)
"""

from fastapi import APIRouter, Depends, Query, HTTPException
from typing import Literal
import asyncpg
import json
from db import get_conn

router = APIRouter()

SortType = Literal["overall", "energy", "environment", "cooling", "connectivity", "planning"]


@router.get("/pins")
async def get_pins(
    sort: SortType = Query(..., description="Active sort key"),
    conn: asyncpg.Connection = Depends(get_conn),
):
    """
    Return GeoJSON FeatureCollection of pins for the given sort.

    Each feature has:
      geometry: Point (EPSG:4326)
      properties: { pin_id, tile_id, name, type, ...sort-specific fields }
    """
    # TODO: implement per-sort SQL queries

    # Query template (substitute pin_table and extra columns per sort):
    # SELECT
    #   json_build_object(
    #     'type', 'Feature',
    #     'geometry', ST_AsGeoJSON(geom)::json,
    #     'properties', json_build_object(
    #       'pin_id', pin_id,
    #       'tile_id', tile_id,
    #       'name', name,
    #       'type', type,
    #       ... sort-specific columns
    #     )
    #   ) AS feature
    # FROM {pin_table}

    SORT_QUERIES: dict[str, str] = {
        "overall": """
            SELECT json_build_object(
                'type', 'Feature',
                'geometry', ST_AsGeoJSON(geom)::json,
                'properties', json_build_object(
                    'pin_id', pin_id, 'tile_id', tile_id,
                    'name', name, 'type', type,
                    'operator', operator, 'dc_status', dc_status,
                    'capacity_mw', capacity_mw
                )
            ) AS feature FROM pins_overall
            UNION ALL
            SELECT json_build_object(
                'type', 'Feature',
                'geometry', ST_AsGeoJSON(geom)::json,
                'properties', json_build_object(
                    'pin_id', -ida_site_id, 'tile_id', tile_id,
                    'name', name, 'type', 'ida_site',
                    'site_type', site_type, 'county', county
                )
            ) FROM ida_sites
        """,
        "energy": """
            SELECT json_build_object(
                'type', 'Feature',
                'geometry', ST_AsGeoJSON(geom)::json,
                'properties', json_build_object(
                    'pin_id', pin_id, 'tile_id', tile_id,
                    'name', name, 'type', type,
                    'capacity_mw', capacity_mw, 'voltage_kv', voltage_kv,
                    'operator', operator
                )
            ) AS feature FROM pins_energy
        """,
        "environment": """
            SELECT json_build_object(
                'type', 'Feature',
                'geometry', ST_AsGeoJSON(geom)::json,
                'properties', json_build_object(
                    'pin_id', pin_id, 'tile_id', tile_id,
                    'name', name, 'type', type,
                    'designation_id', designation_id, 'area_ha', area_ha
                )
            ) AS feature FROM pins_environment
        """,
        "cooling": """
            SELECT json_build_object(
                'type', 'Feature',
                'geometry', ST_AsGeoJSON(geom)::json,
                'properties', json_build_object(
                    'pin_id', pin_id, 'tile_id', tile_id,
                    'name', name, 'type', type,
                    'station_id', station_id, 'mean_flow_m3s', mean_flow_m3s,
                    'waterbody_type', waterbody_type
                )
            ) AS feature FROM pins_cooling
        """,
        "connectivity": """
            SELECT json_build_object(
                'type', 'Feature',
                'geometry', ST_AsGeoJSON(geom)::json,
                'properties', json_build_object(
                    'pin_id', pin_id, 'tile_id', tile_id,
                    'name', name, 'type', type,
                    'ix_asn', ix_asn, 'road_ref', road_ref
                )
            ) AS feature FROM pins_connectivity
        """,
        "planning": """
            SELECT json_build_object(
                'type', 'Feature',
                'geometry', ST_AsGeoJSON(geom)::json,
                'properties', json_build_object(
                    'pin_id', pin_id, 'tile_id', tile_id,
                    'name', name, 'type', type,
                    'app_ref', app_ref, 'app_status', app_status, 'app_date', app_date
                )
            ) AS feature FROM pins_planning
            UNION ALL
            SELECT json_build_object(
                'type', 'Feature',
                'geometry', ST_AsGeoJSON(geom)::json,
                'properties', json_build_object(
                    'pin_id', -ida_site_id, 'tile_id', tile_id,
                    'name', name, 'type', 'ida_site',
                    'site_type', site_type, 'county', county
                )
            ) FROM ida_sites
        """,
    }

    query = SORT_QUERIES.get(sort)
    if not query:
        raise HTTPException(status_code=400, detail=f"Unknown sort: {sort}")

    # TODO: implement — execute query and build FeatureCollection
    rows = await conn.fetch(query)
    features = [json.loads(row["feature"]) for row in rows]

    return {
        "type": "FeatureCollection",
        "features": features,
    }

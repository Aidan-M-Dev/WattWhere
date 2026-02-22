"""
FILE: api/routes/metric_range.py
Role: GET /api/metric-range?sort={sort}&metric={metric} — legend min/max for raw sub-metrics.
Agent boundary: API — metric range route (§7, §10)
Dependencies: db.py get_conn(); metric_ranges table (populated by pipeline)
Output: { "min": float, "max": float, "unit": str }
        Consumed by useSuitabilityStore.fetchMetricRange() → shown in MapLegend
How to test:
  curl "http://localhost:8000/api/metric-range?sort=energy&metric=wind_speed_100m"
  Expect: {"min": 3.2, "max": 12.8, "unit": "m/s"}

Only called for raw-value sub-metrics:
  energy:  wind_speed_100m (m/s), solar_ghi (kWh/m²/yr)
  cooling: temperature (°C), rainfall (mm/yr)
Pre-normalised 0–100 metrics never call this endpoint.

Response is cached (metric_ranges updated only by pipeline runs).
"""

from fastapi import APIRouter, Depends, Query, HTTPException
from fastapi_cache.decorator import cache
from typing import Literal
from pydantic import BaseModel
import asyncpg
from db import get_conn

router = APIRouter()


class MetricRangeResponse(BaseModel):
    min: float
    max: float
    unit: str


# Valid raw sub-metrics that have entries in metric_ranges table
VALID_RAW_METRICS: set[tuple[str, str]] = {
    ("energy", "wind_speed_100m"),
    ("energy", "solar_ghi"),
    ("cooling", "temperature"),
    ("cooling", "rainfall"),
}


@router.get("/metric-range", response_model=MetricRangeResponse)
@cache(expire=1800)
async def get_metric_range(
    sort: str = Query(..., description="Sort key (e.g. 'energy')"),
    metric: str = Query(..., description="Metric key (e.g. 'wind_speed_100m')"),
    conn: asyncpg.Connection = Depends(get_conn),
) -> MetricRangeResponse:
    """
    Return the actual data min/max for a raw sub-metric.
    Used by MapLegend to display real-world units (m/s, °C, mm, kWh/m²/yr)
    instead of the generic 0–100 normalised range.

    This data is pre-computed by the pipeline and stored in metric_ranges.
    Do NOT compute it on the fly from sort tables (expensive on 14k tiles).
    """
    if (sort, metric) not in VALID_RAW_METRICS:
        raise HTTPException(
            status_code=400,
            detail=f"No metric range available for sort='{sort}' metric='{metric}'. "
                   f"Only raw sub-metrics have ranges: {list(VALID_RAW_METRICS)}"
        )

    row = await conn.fetchrow(
        "SELECT min_val, max_val, unit FROM metric_ranges WHERE sort = $1 AND metric = $2",
        sort, metric
    )
    if not row:
        raise HTTPException(
            status_code=404,
            detail=f"metric_ranges not populated for sort='{sort}' metric='{metric}'. "
                   "Run the pipeline to populate this table."
        )

    return MetricRangeResponse(
        min=float(row["min_val"]),
        max=float(row["max_val"]),
        unit=row["unit"],
    )

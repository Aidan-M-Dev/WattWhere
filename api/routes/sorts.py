"""
FILE: api/routes/sorts.py
Role: GET /api/sorts — returns all sort metadata for the frontend DataBar.
Agent boundary: API — sorts route (§7, §10)
Dependencies: db.py get_conn(); composite_weights table (for weight values)
Output: JSON list of SortMeta objects consumed by useSuitabilityStore.init()
How to test: curl http://localhost:8000/api/sorts | python3 -m json.tool

ARCHITECTURE RULE: This endpoint is the ONLY source of truth for the frontend
DataBar. The frontend must not hardcode sort or metric lists.

Schema returned:
  [
    {
      "key": "overall",
      "label": "Overall",
      "icon": "BarChart3",
      "description": "...",
      "metrics": [
        {"key": "score", "label": "Overall composite", "unit": "0–100", "isDefault": true},
        ...
      ]
    },
    ...
  ]

Implementation notes:
  - Metrics list is static (defined in code below) — it maps to columns in the
    sort score tables and to valid combinations in the tile_heatmap SQL function.
  - Do not query the database for metric definitions — they are static schema.
  - Weights can optionally be fetched from composite_weights and surfaced in
    the overall sort's metadata for frontend display.
"""

from fastapi import APIRouter, Depends
from fastapi_cache.decorator import cache
from pydantic import BaseModel
import asyncpg
from db import get_conn

router = APIRouter()


# ── Pydantic models ──────────────────────────────────────────

class MetricMeta(BaseModel):
    key: str
    label: str
    unit: str
    isDefault: bool


class SortMeta(BaseModel):
    key: str
    label: str
    icon: str
    description: str
    metrics: list[MetricMeta]


# ── Static sort + metric definitions ─────────────────────────
# These mirror ARCHITECTURE.md §5 exactly.
# Metric keys must match column names in sort tables AND CASE branches in tile_heatmap().

SORTS_METADATA: list[SortMeta] = [
    SortMeta(
        key="overall",
        label="Overall",
        icon="BarChart3",
        description="Composite weighted score combining all sort dimensions into a single data centre suitability score.",
        metrics=[
            MetricMeta(key="score",               label="Overall composite",    unit="0–100",        isDefault=True),
            MetricMeta(key="energy_score",         label="Energy sub-score",     unit="0–100",        isDefault=False),
            MetricMeta(key="environment_score",    label="Constraints sub-score",unit="0–100",        isDefault=False),
            MetricMeta(key="cooling_score",        label="Cooling sub-score",    unit="0–100",        isDefault=False),
            MetricMeta(key="connectivity_score",   label="Connectivity sub-score",unit="0–100",       isDefault=False),
            MetricMeta(key="planning_score",       label="Planning sub-score",   unit="0–100",        isDefault=False),
        ],
    ),
    SortMeta(
        key="energy",
        label="Energy",
        icon="Zap",
        description="Renewable energy generation potential and proximity to grid infrastructure.",
        metrics=[
            MetricMeta(key="score",           label="Energy Score",          unit="0–100",        isDefault=True),
            MetricMeta(key="wind_speed_100m", label="Wind speed at 100m",   unit="m/s",          isDefault=False),
            MetricMeta(key="solar_ghi",       label="Solar irradiance (GHI)",unit="kWh/m²/yr",   isDefault=False),
            MetricMeta(key="grid_proximity",  label="Grid proximity score", unit="0–100",        isDefault=False),
        ],
    ),
    SortMeta(
        key="environment",
        label="Constraints",
        icon="ShieldAlert",
        description="Environmental and natural hazard designations that restrict or prohibit development.",
        metrics=[
            MetricMeta(key="score",                label="Constraint composite",      unit="0–100", isDefault=True),
            MetricMeta(key="designation_overlap",  label="Designation severity",      unit="0–100", isDefault=False),
            MetricMeta(key="flood_risk",           label="Flood risk (inverted)",     unit="0–100", isDefault=False),
            MetricMeta(key="landslide_risk",       label="Landslide risk (inverted)", unit="0–100", isDefault=False),
        ],
    ),
    SortMeta(
        key="cooling",
        label="Cooling",
        icon="Thermometer",
        description="Suitability of local climate and water resources for data centre cooling.",
        metrics=[
            MetricMeta(key="score",              label="Cooling Score",            unit="0–100",  isDefault=True),
            MetricMeta(key="temperature",        label="Mean annual temperature",  unit="°C",     isDefault=False),
            MetricMeta(key="water_proximity",    label="Water proximity score",    unit="0–100",  isDefault=False),
            MetricMeta(key="rainfall",           label="Annual rainfall",          unit="mm/yr",  isDefault=False),
            MetricMeta(key="aquifer_productivity", label="Aquifer productivity",     unit="0–100",  isDefault=False),
        ],
    ),
    SortMeta(
        key="connectivity",
        label="Connectivity",
        icon="Globe",
        description="Digital connectivity, internet exchange proximity, and physical transport access.",
        metrics=[
            MetricMeta(key="score",       label="Connectivity Score",    unit="0–100", isDefault=True),
            MetricMeta(key="broadband",   label="Broadband coverage",   unit="0–100", isDefault=False),
            MetricMeta(key="ix_distance", label="IX distance score",    unit="0–100", isDefault=False),
            MetricMeta(key="road_access", label="Road access score",    unit="0–100", isDefault=False),
        ],
    ),
    SortMeta(
        key="planning",
        label="Planning",
        icon="Map",
        description="Favourability of local planning and zoning context for data centre development.",
        metrics=[
            MetricMeta(key="score",                  label="Planning Score",          unit="0–100",  isDefault=True),
            MetricMeta(key="zoning_tier",            label="Zoning tier score",       unit="0–100",  isDefault=False),
            MetricMeta(key="planning_precedent",     label="Planning precedent score",unit="0–100",  isDefault=False),
            MetricMeta(key="land_price",             label="Land price score",        unit="0–100",  isDefault=False),
            MetricMeta(key="avg_price_per_sqm_eur",  label="Property price (raw)",    unit="€/m²",   isDefault=False),
        ],
    ),
]


# ── Endpoint ─────────────────────────────────────────────────

@router.get("/sorts", response_model=list[SortMeta])
@cache(expire=3600)
async def get_sorts(conn: asyncpg.Connection = Depends(get_conn)):
    """
    Return all sort + sub-metric metadata.
    The frontend DataBar is built entirely from this response.
    """
    # TODO: optionally enrich with current weights from composite_weights table
    # row = await conn.fetchrow("SELECT energy, connectivity, environment, cooling, planning FROM composite_weights WHERE id = 1")
    # if row: attach weights to overall sort metadata for sidebar display
    return SORTS_METADATA

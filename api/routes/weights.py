"""
FILE: api/routes/weights.py
Role: GET/PUT /api/weights — composite score weights (admin-protected PUT).
Agent boundary: API — weights route (§7, §10)
Dependencies: db.py get_conn(); composite_weights table (single-row, id=1)
              ADMIN_KEY environment variable
Output: Current weights (GET) or updated weights + trigger recompute (PUT)
How to test:
  GET:  curl http://localhost:8000/api/weights
  PUT:  curl -X PUT http://localhost:8000/api/weights \
          -H "X-Admin-Key: your_admin_key" \
          -H "Content-Type: application/json" \
          -d '{"energy":0.30,"connectivity":0.25,"environment":0.20,"cooling":0.10,"planning":0.15}'

ARCHITECTURE RULES (§11 D4):
  - PUT requires X-Admin-Key header. Key from ADMIN_KEY env var.
  - Return 401 if key missing or incorrect.
  - Weights must sum to 1.0 (enforced by DB CHECK constraint + Pydantic validator).
  - After weight update, overall_scores are stale — the pipeline must be re-run
    to recompute them. This endpoint does NOT auto-trigger recomputation.
  - Production would need proper auth (OAuth2). This is intentionally minimal for hackathon.
"""

import os
from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, field_validator, model_validator
from decimal import Decimal
import asyncpg
from db import get_conn

router = APIRouter()


class WeightsResponse(BaseModel):
    energy: float
    connectivity: float
    environment: float
    cooling: float
    planning: float


class WeightsUpdate(BaseModel):
    energy: float
    connectivity: float
    environment: float
    cooling: float
    planning: float

    @model_validator(mode="after")
    def weights_must_sum_to_one(self) -> "WeightsUpdate":
        total = self.energy + self.connectivity + self.environment + self.cooling + self.planning
        if abs(total - 1.0) > 0.0001:
            raise ValueError(f"Weights must sum to 1.0, got {total:.4f}")
        return self

    @field_validator("energy", "connectivity", "environment", "cooling", "planning", mode="before")
    @classmethod
    def must_be_positive(cls, v: float) -> float:
        if v < 0 or v > 1:
            raise ValueError("Each weight must be between 0 and 1")
        return v


def _check_admin_key(x_admin_key: str | None = Header(default=None)) -> None:
    """FastAPI dependency: validates X-Admin-Key header."""
    expected = os.environ.get("ADMIN_KEY", "")
    if not expected:
        raise HTTPException(status_code=500, detail="ADMIN_KEY not configured on server")
    if x_admin_key != expected:
        raise HTTPException(status_code=401, detail="Invalid or missing X-Admin-Key header")


@router.get("/weights", response_model=WeightsResponse)
async def get_weights(conn: asyncpg.Connection = Depends(get_conn)) -> WeightsResponse:
    """Return the current composite score weights."""
    row = await conn.fetchrow(
        "SELECT energy, connectivity, environment, cooling, planning FROM composite_weights WHERE id = 1"
    )
    if not row:
        raise HTTPException(status_code=500, detail="composite_weights table not seeded")

    return WeightsResponse(
        energy=float(row["energy"]),
        connectivity=float(row["connectivity"]),
        environment=float(row["environment"]),
        cooling=float(row["cooling"]),
        planning=float(row["planning"]),
    )


@router.put("/weights", response_model=WeightsResponse)
async def update_weights(
    weights: WeightsUpdate,
    conn: asyncpg.Connection = Depends(get_conn),
    _: None = Depends(_check_admin_key),
) -> WeightsResponse:
    """
    Update composite score weights. Requires X-Admin-Key header.

    NOTE: This does NOT recompute overall_scores. After updating weights,
    run: docker compose --profile pipeline run pipeline python overall/compute_composite.py
    Then restart Martin to flush its tile cache.
    """
    await conn.execute(
        """UPDATE composite_weights SET
            energy = $1, connectivity = $2, environment = $3,
            cooling = $4, planning = $5, updated_at = now()
           WHERE id = 1""",
        weights.energy, weights.connectivity, weights.environment,
        weights.cooling, weights.planning,
    )

    return WeightsResponse(
        energy=weights.energy,
        connectivity=weights.connectivity,
        environment=weights.environment,
        cooling=weights.cooling,
        planning=weights.planning,
    )

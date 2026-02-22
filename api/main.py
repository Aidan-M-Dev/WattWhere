"""
FILE: api/main.py
Role: FastAPI application entry point — CORS, lifespan, router registration.
Agent boundary: API layer (§7, §10)
Dependencies: db.py (pool), routes/*.py (endpoints)
Output: HTTP JSON API on :8000 with prefix /api/
How to test: uvicorn main:app --reload; curl http://localhost:8000/api/sorts
             Or: docker compose up api

Endpoints registered:
  GET  /api/sorts
  GET  /api/pins?sort={sort}
  GET  /api/tile/{tile_id}?sort={sort}
  GET  /api/metric-range?sort={sort}&metric={metric}
  GET  /api/weights
  PUT  /api/weights  (X-Admin-Key header required)

ARCHITECTURE NOTE: FastAPI serves JSON data only. Tile geometry is served
by Martin (MVT). Do NOT add geometry/GeoJSON tile endpoints to this API.
"""

from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi_cache import FastAPICache
from fastapi_cache.backends.inmemory import InMemoryBackend

from db import init_pool, close_pool
from routes import sorts, pins, tile, metric_range, weights, admin


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Init DB pool on startup, close on shutdown."""
    await init_pool()
    FastAPICache.init(InMemoryBackend())
    yield
    await close_pool()


app = FastAPI(
    title="Ireland Data Centre Suitability API",
    description="JSON data API for the HackEurope suitability platform.",
    version="0.1.0",
    lifespan=lifespan,
)

# ── CORS ──────────────────────────────────────────────────────
# In dev: allow Vite dev server. Tighten in production.
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://localhost:80",
        "http://frontend:5173",
    ],
    allow_credentials=False,
    allow_methods=["GET", "PUT", "POST"],
    allow_headers=["*"],
)

# ── Routers ───────────────────────────────────────────────────
app.include_router(sorts.router,         prefix="/api")
app.include_router(pins.router,          prefix="/api")
app.include_router(tile.router,          prefix="/api")
app.include_router(metric_range.router,  prefix="/api")
app.include_router(weights.router,       prefix="/api")
app.include_router(admin.router,         prefix="/api")


@app.get("/health")
async def health():
    """Health check endpoint for Docker healthcheck."""
    return {"status": "ok"}

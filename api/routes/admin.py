"""
FILE: api/routes/admin.py
Role: POST /api/admin/invalidate-cache — clear in-memory cache (admin-protected).
Agent boundary: API — admin route
Dependencies: FastAPICache (in-memory backend), ADMIN_KEY env var
How to test:
  curl -s -X POST -H "X-Admin-Key: devkey" http://localhost:8000/api/admin/invalidate-cache
  # Expect: {"status": "cache cleared"}
"""

import os
from fastapi import APIRouter, Header, HTTPException, Depends
from fastapi_cache import FastAPICache

router = APIRouter()


def _check_admin_key(
    x_admin_key: str | None = Header(default=None, alias="X-Admin-Key"),
) -> None:
    """FastAPI dependency: validates X-Admin-Key header."""
    expected = os.environ.get("ADMIN_KEY", "")
    if not x_admin_key or x_admin_key != expected:
        raise HTTPException(status_code=401, detail="Invalid or missing X-Admin-Key header")


@router.post("/admin/invalidate-cache")
async def invalidate_cache(_: None = Depends(_check_admin_key)):
    """Clear all in-memory cached responses. Requires X-Admin-Key header."""
    await FastAPICache.clear()
    return {"status": "cache cleared"}

"""
api/tests/test_routes.py
Uses httpx.AsyncClient with the FastAPI ASGI transport.
Requires: pytest, httpx, pytest-asyncio
Run: pytest api/tests/ -v
"""

import json
import os
import sys
from decimal import Decimal
from unittest.mock import AsyncMock, patch

import pytest
from httpx import AsyncClient, ASGITransport

# Set env vars before importing app modules so lifespan + admin key work
os.environ.setdefault("DATABASE_URL", "postgresql://test:test@localhost/test")
os.environ.setdefault("ADMIN_KEY", "testkey")

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from db import get_conn  # noqa: E402 — must come after sys.path insert
from fastapi_cache import FastAPICache  # noqa: E402
from fastapi_cache.backends.inmemory import InMemoryBackend  # noqa: E402
from main import app  # noqa: E402


# ── Helpers ───────────────────────────────────────────────────────────────────


class FakeRecord(dict):
    """Dict subclass that mimics asyncpg.Record's __getitem__ interface."""


def fr(**kwargs) -> FakeRecord:
    """Shorthand constructor for FakeRecord."""
    return FakeRecord(kwargs)


# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture
def mock_conn() -> AsyncMock:
    return AsyncMock()


@pytest.fixture
async def client(mock_conn: AsyncMock):
    async def override():
        yield mock_conn

    app.dependency_overrides[get_conn] = override
    with (
        patch("db.init_pool", new_callable=AsyncMock),
        patch("db.close_pool", new_callable=AsyncMock),
    ):
        FastAPICache.init(InMemoryBackend())
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as ac:
            yield ac
    app.dependency_overrides.clear()


# ── Tests ─────────────────────────────────────────────────────────────────────


async def test_sorts_returns_six_items(client):
    r = await client.get("/api/sorts")
    assert r.status_code == 200
    data = r.json()
    assert len(data) == 6
    for sort in data:
        assert "key" in sort
        assert "label" in sort
        assert "metrics" in sort


async def test_pins_overall_returns_feature_collection(client, mock_conn):
    feature = json.dumps({
        "type": "Feature",
        "geometry": {"type": "Point", "coordinates": [-6.26, 53.33]},
        "properties": {"pin_id": 1, "tile_id": 1, "name": "Test DC", "type": "data_centre"},
    })
    mock_conn.fetch.return_value = [fr(feature=feature)]

    r = await client.get("/api/pins?sort=overall")
    assert r.status_code == 200
    data = r.json()
    assert data["type"] == "FeatureCollection"
    assert isinstance(data["features"], list)


async def test_pins_invalid_sort_returns_422(client):
    r = await client.get("/api/pins?sort=invalid")
    assert r.status_code == 422


async def test_tile_overall_has_tile_id_county_score_and_weights(client, mock_conn):
    tile_row = fr(tile_id=1, county="Dublin", grid_ref="R001C001", lng=-6.26, lat=53.33)
    overall_row = fr(
        score=Decimal("75.50"),
        energy_score=Decimal("80.00"),
        environment_score=Decimal("70.00"),
        cooling_score=Decimal("65.00"),
        connectivity_score=Decimal("85.00"),
        planning_score=Decimal("78.00"),
        has_hard_exclusion=False,
        exclusion_reason=None,
        nearest_data_centre_km=Decimal("12.345"),
        energy=Decimal("0.25"),
        connectivity=Decimal("0.25"),
        environment=Decimal("0.20"),
        cooling=Decimal("0.15"),
        planning=Decimal("0.15"),
    )
    mock_conn.fetchrow.side_effect = [tile_row, overall_row]

    r = await client.get("/api/tile/1?sort=overall")
    assert r.status_code == 200
    data = r.json()
    assert "tile_id" in data
    assert "county" in data
    assert "score" in data
    assert "weights" in data


async def test_tile_energy_has_wind_speed_100m(client, mock_conn):
    tile_row = fr(tile_id=1, county="Mayo", grid_ref="R010C010", lng=-9.0, lat=53.8)
    energy_row = fr(
        score=Decimal("88.00"),
        wind_speed_50m=Decimal("7.2"),
        wind_speed_100m=Decimal("8.5"),
        wind_speed_150m=Decimal("9.1"),
        solar_ghi=Decimal("1050.0"),
        grid_proximity=Decimal("72.0"),
        nearest_transmission_line_km=Decimal("3.2"),
        nearest_substation_km=Decimal("5.1"),
        nearest_substation_name="Castlebar 110kV",
        nearest_substation_voltage="110kV",
        grid_low_confidence=False,
    )
    mock_conn.fetchrow.side_effect = [tile_row, energy_row]

    r = await client.get("/api/tile/1?sort=energy")
    assert r.status_code == 200
    data = r.json()
    assert "wind_speed_100m" in data


async def test_tile_environment_has_designations_list(client, mock_conn):
    tile_row = fr(tile_id=1, county="Kerry", grid_ref="R005C005", lng=-9.5, lat=52.0)
    env_row = fr(
        score=Decimal("45.00"),
        designation_overlap=Decimal("30.00"),
        flood_risk=Decimal("70.00"),
        landslide_risk=Decimal("80.00"),
        has_hard_exclusion=True,
        exclusion_reason="SAC overlap",
        intersects_sac=True,
        intersects_spa=False,
        intersects_nha=False,
        intersects_pnha=False,
        intersects_current_flood=False,
        intersects_future_flood=False,
        landslide_susceptibility="low",
    )
    desig_rows = [
        fr(
            designation_type="SAC",
            designation_name="Killarney National Park",
            designation_id="IE0000099",
            pct_overlap=Decimal("45.0"),
        )
    ]
    mock_conn.fetchrow.side_effect = [tile_row, env_row]
    mock_conn.fetch.return_value = desig_rows

    r = await client.get("/api/tile/1?sort=environment")
    assert r.status_code == 200
    data = r.json()
    assert "designations" in data
    assert isinstance(data["designations"], list)


async def test_tile_not_found_returns_404(client, mock_conn):
    mock_conn.fetchrow.return_value = None

    r = await client.get("/api/tile/99999?sort=overall")
    assert r.status_code == 404


async def test_metric_range_returns_min_max_unit(client, mock_conn):
    mock_conn.fetchrow.return_value = fr(
        min_val=Decimal("3.2"), max_val=Decimal("12.8"), unit="m/s"
    )

    r = await client.get("/api/metric-range?sort=energy&metric=wind_speed_100m")
    assert r.status_code == 200
    data = r.json()
    assert "min" in data
    assert "max" in data
    assert "unit" in data


async def test_metric_range_score_is_not_raw_metric_returns_400(client):
    r = await client.get("/api/metric-range?sort=energy&metric=score")
    assert r.status_code == 400


async def test_weights_returns_200_and_sums_to_one(client, mock_conn):
    mock_conn.fetchrow.return_value = fr(
        energy=Decimal("0.25"),
        connectivity=Decimal("0.25"),
        environment=Decimal("0.20"),
        cooling=Decimal("0.15"),
        planning=Decimal("0.15"),
    )

    r = await client.get("/api/weights")
    assert r.status_code == 200
    data = r.json()
    total = sum(data[k] for k in ["energy", "connectivity", "environment", "cooling", "planning"])
    assert abs(total - 1.0) < 0.001


async def test_weights_put_without_header_returns_401(client):
    r = await client.put(
        "/api/weights",
        json={"energy": 0.25, "connectivity": 0.25, "environment": 0.20, "cooling": 0.15, "planning": 0.15},
    )
    assert r.status_code == 401


async def test_weights_put_with_wrong_key_returns_401(client):
    r = await client.put(
        "/api/weights",
        headers={"X-Admin-Key": "wrongkey"},
        json={"energy": 0.25, "connectivity": 0.25, "environment": 0.20, "cooling": 0.15, "planning": 0.15},
    )
    assert r.status_code == 401


async def test_weights_put_with_correct_key_returns_200(client, mock_conn):
    mock_conn.execute.return_value = None

    r = await client.put(
        "/api/weights",
        headers={"X-Admin-Key": "testkey"},
        json={"energy": 0.30, "connectivity": 0.25, "environment": 0.20, "cooling": 0.10, "planning": 0.15},
    )
    assert r.status_code == 200
    data = r.json()
    total = sum(data[k] for k in ["energy", "connectivity", "environment", "cooling", "planning"])
    assert abs(total - 1.0) < 0.001


async def test_sorts_cached_response_is_identical(client):
    r1 = await client.get("/api/sorts")
    r2 = await client.get("/api/sorts")
    assert r1.status_code == 200
    assert r2.status_code == 200
    assert r1.json() == r2.json()


async def test_tile_all_returns_all_sorts(client, mock_conn):
    tile_row = fr(tile_id=1, county="Dublin", grid_ref="R001C001", lng=-6.26, lat=53.33)
    overall_row = fr(
        score=Decimal("75.50"),
        energy_score=Decimal("80.00"), environment_score=Decimal("70.00"),
        cooling_score=Decimal("65.00"), connectivity_score=Decimal("85.00"),
        planning_score=Decimal("78.00"), has_hard_exclusion=False,
        exclusion_reason=None, nearest_data_centre_km=Decimal("12.345"),
        energy=Decimal("0.25"), connectivity=Decimal("0.25"),
        environment=Decimal("0.20"), cooling=Decimal("0.15"), planning=Decimal("0.15"),
    )
    energy_row = fr(
        score=Decimal("88.00"), wind_speed_50m=Decimal("7.2"),
        wind_speed_100m=Decimal("8.5"), wind_speed_150m=Decimal("9.1"),
        solar_ghi=Decimal("1050.0"), grid_proximity=Decimal("72.0"),
        nearest_transmission_line_km=Decimal("3.2"), nearest_substation_km=Decimal("5.1"),
        nearest_substation_name="Castlebar 110kV", nearest_substation_voltage="110kV",
        grid_low_confidence=False,
    )
    env_row = fr(
        score=Decimal("45.00"), designation_overlap=Decimal("30.00"),
        flood_risk=Decimal("70.00"), landslide_risk=Decimal("80.00"),
        has_hard_exclusion=False, exclusion_reason=None,
        intersects_sac=False, intersects_spa=False, intersects_nha=False,
        intersects_pnha=False, intersects_current_flood=False,
        intersects_future_flood=False, landslide_susceptibility="low",
    )
    cooling_row = fr(
        score=Decimal("60.00"), temperature=Decimal("10.5"),
        water_proximity=Decimal("65.0"), rainfall=Decimal("1100.0"),
        aquifer_productivity=Decimal("50.0"), free_cooling_hours=Decimal("4500"),
        nearest_waterbody_name="River Liffey", nearest_waterbody_km=Decimal("2.1"),
        nearest_hydrometric_station_name="Islandbridge", nearest_hydrometric_flow_m3s=Decimal("15.3"),
        aquifer_productivity_rating="Moderate",
    )
    conn_row = fr(
        score=Decimal("82.00"), broadband=Decimal("90.0"),
        ix_distance=Decimal("75.0"), road_access=Decimal("80.0"),
        inex_dublin_km=Decimal("5.0"), inex_cork_km=Decimal("220.0"),
        broadband_tier="NGA", nearest_motorway_junction_km=Decimal("3.5"),
        nearest_motorway_junction_name="J7 Naas North",
        nearest_national_road_km=Decimal("1.2"), nearest_rail_freight_km=Decimal("8.0"),
    )
    planning_row = fr(
        score=Decimal("70.00"), zoning_tier=Decimal("80.0"),
        planning_precedent=Decimal("60.0"), pct_industrial=Decimal("25.0"),
        pct_enterprise=Decimal("15.0"), pct_mixed_use=Decimal("10.0"),
        pct_agricultural=Decimal("30.0"), pct_residential=Decimal("15.0"),
        pct_other=Decimal("5.0"), nearest_ida_site_km=Decimal("4.0"),
        population_density_per_km2=Decimal("350.0"), county_dev_plan_ref="DCC-2022",
    )

    # fetchrow calls: tile_base, overall, energy, env, cooling, connectivity, planning
    mock_conn.fetchrow.side_effect = [
        tile_row, overall_row, energy_row, env_row, cooling_row, conn_row, planning_row,
    ]
    # fetch calls: environment designations, planning applications
    mock_conn.fetch.side_effect = [[], []]

    r = await client.get("/api/tile/1/all")
    assert r.status_code == 200
    data = r.json()
    assert set(data.keys()) == {
        "overall", "energy", "environment", "cooling", "connectivity", "planning",
    }
    assert data["overall"]["tile_id"] == 1
    assert data["energy"]["wind_speed_100m"] == 8.5
    assert data["planning"]["score"] == 70.0


async def test_tile_all_not_found(client, mock_conn):
    mock_conn.fetchrow.return_value = None

    r = await client.get("/api/tile/99999/all")
    assert r.status_code == 404


async def test_admin_invalidate_cache_without_key_returns_401(client):
    r = await client.post("/api/admin/invalidate-cache")
    assert r.status_code == 401


async def test_admin_invalidate_cache_with_correct_key_returns_200(client):
    r = await client.post(
        "/api/admin/invalidate-cache",
        headers={"X-Admin-Key": "testkey"},
    )
    assert r.status_code == 200
    assert r.json() == {"status": "cache cleared"}

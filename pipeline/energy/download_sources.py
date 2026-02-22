"""
FILE: pipeline/energy/download_sources.py
Role: Download real source data for the energy pipeline.

Sources:
  Wind speed (100m): Global Wind Atlas 3.0 — DTU/World Bank
    GeoTIFF, EPSG:4326, 250m resolution
    https://globalwindatlas.info (CC BY 4.0)

  Solar GHI: NASA POWER Climatology — NASA Langley
    Regional JSON API (0.5° grid) → interpolated GeoTIFF, EPSG:4326
    https://power.larc.nasa.gov  (public domain)

  OSM power infrastructure: OpenStreetMap via Overpass API
    power=substation / line / cable for Republic of Ireland
    https://overpass-api.de  (ODbL)

Run: python energy/download_sources.py
     (saves to /data/energy/ — re-run is idempotent, skips existing files)
"""

import sys
import io
import json
import time
import urllib.request
import urllib.parse
from pathlib import Path

import numpy as np
import geopandas as gpd
import rasterio
from rasterio.transform import from_bounds
from rasterio.crs import CRS
from scipy.interpolate import griddata
from shapely.geometry import shape

sys.path.insert(0, str(Path(__file__).parent.parent))
from config import WIND_ATLAS_FILE, SOLAR_ATLAS_FILE, OSM_POWER_FILE, SEAI_WIND_FARMS_FILE, OSM_GENERATORS_FILE

# Ireland bounding box WGS84
IRE_LON_MIN, IRE_LON_MAX = -11.0, -5.5
IRE_LAT_MIN, IRE_LAT_MAX = 51.0, 55.5

# Output raster resolution (degrees) — ~1 km at Ireland's latitude
RASTER_RES_DEG = 0.01


# ── Helpers ────────────────────────────────────────────────────────────────────

def _download(url: str, desc: str, timeout: int = 120) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "HackEurope-pipeline/1.0"})
    print(f"  Downloading {desc}...")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = resp.read()
    print(f"  Done ({len(data) / 1_048_576:.1f} MB)")
    return data


# ── Wind speed (100m) — Global Wind Atlas GeoTIFF ─────────────────────────────

def download_wind():
    if WIND_ATLAS_FILE.exists():
        print(f"[wind] Already present: {WIND_ATLAS_FILE}")
        return

    url = "https://gwa.cdn.nazkamapps.com/country_tifs_v4/IRL_wind-speed_100m.tif"
    data = _download(url, "Global Wind Atlas IRL 100m GeoTIFF")

    WIND_ATLAS_FILE.parent.mkdir(parents=True, exist_ok=True)
    WIND_ATLAS_FILE.write_bytes(data)
    print(f"  Saved to {WIND_ATLAS_FILE}")

    # Quick sanity check
    with rasterio.open(str(WIND_ATLAS_FILE)) as src:
        arr = src.read(1, masked=True)
        print(f"  Wind range: {arr.min():.2f}–{arr.max():.2f} m/s  "
              f"(shape: {arr.shape}, CRS: {src.crs.to_epsg()})")


# ── Solar GHI — NASA POWER regional API → GeoTIFF ─────────────────────────────

_MONTH_DAYS = [31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31]
_MONTH_KEYS = ["JAN", "FEB", "MAR", "APR", "MAY", "JUN",
               "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"]


def _annual_ghi_kwh_m2_yr(monthly_daily: dict) -> float:
    """Sum monthly (kWh/m²/day × days_in_month) to get annual kWh/m²/yr."""
    return sum(
        monthly_daily.get(m, 0.0) * d
        for m, d in zip(_MONTH_KEYS, _MONTH_DAYS)
    )


def download_solar():
    if SOLAR_ATLAS_FILE.exists():
        print(f"[solar] Already present: {SOLAR_ATLAS_FILE}")
        return

    url = (
        "https://power.larc.nasa.gov/api/temporal/climatology/regional"
        "?parameters=ALLSKY_SFC_SW_DWN"
        "&community=RE"
        f"&longitude-min={IRE_LON_MIN}&longitude-max={IRE_LON_MAX}"
        f"&latitude-min={IRE_LAT_MIN}&latitude-max={IRE_LAT_MAX}"
        "&format=JSON&user=HackEurope"
    )
    raw = _download(url, "NASA POWER regional GHI for Ireland (~0.5° grid)")
    features = json.loads(raw).get("features", [])
    print(f"  Grid points returned: {len(features)}")

    # Extract lon, lat, annual GHI
    lons, lats, ghis = [], [], []
    for feat in features:
        lon, lat = feat["geometry"]["coordinates"][:2]
        monthly = feat["properties"]["parameter"]["ALLSKY_SFC_SW_DWN"]
        annual = _annual_ghi_kwh_m2_yr(monthly)
        lons.append(lon)
        lats.append(lat)
        ghis.append(annual)

    points = np.column_stack([lons, lats])
    values = np.array(ghis)
    print(f"  Annual GHI range: {values.min():.0f}–{values.max():.0f} kWh/m²/yr")

    # Interpolate to regular grid at RASTER_RES_DEG resolution
    grid_lons = np.arange(IRE_LON_MIN, IRE_LON_MAX + RASTER_RES_DEG, RASTER_RES_DEG)
    grid_lats = np.arange(IRE_LAT_MIN, IRE_LAT_MAX + RASTER_RES_DEG, RASTER_RES_DEG)
    grid_lon2d, grid_lat2d = np.meshgrid(grid_lons, grid_lats)

    # Cubic interpolation; fall back to linear at edges
    grid_ghi = griddata(points, values, (grid_lon2d, grid_lat2d), method="cubic")
    grid_ghi_fill = griddata(points, values, (grid_lon2d, grid_lat2d), method="linear")
    nan_mask = np.isnan(grid_ghi)
    grid_ghi[nan_mask] = grid_ghi_fill[nan_mask]

    # Any remaining NaN → nearest-neighbour fill
    nan_mask2 = np.isnan(grid_ghi)
    if nan_mask2.any():
        grid_ghi_nn = griddata(points, values, (grid_lon2d, grid_lat2d), method="nearest")
        grid_ghi[nan_mask2] = grid_ghi_nn[nan_mask2]

    # Raster is stored N→S (top row = highest latitude)
    grid_ghi_ns = np.flipud(grid_ghi).astype(np.float32)

    width  = grid_ghi_ns.shape[1]
    height = grid_ghi_ns.shape[0]
    transform = from_bounds(
        IRE_LON_MIN, IRE_LAT_MIN,
        IRE_LON_MIN + width * RASTER_RES_DEG,
        IRE_LAT_MIN + height * RASTER_RES_DEG,
        width, height,
    )

    SOLAR_ATLAS_FILE.parent.mkdir(parents=True, exist_ok=True)
    with rasterio.open(
        str(SOLAR_ATLAS_FILE), "w", driver="GTiff",
        height=height, width=width, count=1,
        dtype="float32", crs=CRS.from_epsg(4326),
        transform=transform, nodata=-9999.0,
    ) as dst:
        dst.write(grid_ghi_ns, 1)
    print(f"  Saved to {SOLAR_ATLAS_FILE}  ({width}×{height} px at {RASTER_RES_DEG}° ≈ ~1 km)")


# ── OSM power infrastructure — Overpass API ───────────────────────────────────

_OVERPASS_URL = "https://overpass-api.de/api/interpreter"

# Republic of Ireland area ID (OSM relation 62273)
_OVERPASS_QUERY = """
[out:json][timeout:180];
area(3600062273)->.irl;
(
  node["power"~"^(substation|tower)$"](area.irl);
  way["power"~"^(substation|line|cable)$"](area.irl);
  relation["power"~"^(substation|line|cable)$"](area.irl);
  node["generator:source"="wind"](area.irl);
  way["generator:source"="wind"](area.irl);
);
out geom;
"""


def _overpass_to_geodataframe(raw: bytes) -> gpd.GeoDataFrame:
    """Convert Overpass JSON response to a GeoDataFrame."""
    data = json.loads(raw)
    elements = data.get("elements", [])
    print(f"  OSM elements returned: {len(elements)}")

    rows = []
    for el in elements:
        el_type = el.get("type")
        tags = el.get("tags", {})

        if el_type == "node":
            geom = {"type": "Point", "coordinates": [el["lon"], el["lat"]]}
        elif el_type == "way":
            coords = [[n["lon"], n["lat"]] for n in el.get("geometry", [])]
            if not coords:
                continue
            if coords[0] == coords[-1] and len(coords) >= 4:
                geom = {"type": "Polygon", "coordinates": [coords]}
            else:
                geom = {"type": "LineString", "coordinates": coords}
        elif el_type == "relation":
            # Use centroid of bounding box for relations
            bounds = el.get("bounds")
            if not bounds:
                continue
            cx = (bounds["minlon"] + bounds["maxlon"]) / 2
            cy = (bounds["minlat"] + bounds["maxlat"]) / 2
            geom = {"type": "Point", "coordinates": [cx, cy]}
        else:
            continue

        rows.append({
            "osm_id": str(el.get("id", "")),
            "power": tags.get("power"),
            "generator_source": tags.get("generator:source"),
            "name": tags.get("name"),
            "voltage": tags.get("voltage"),
            "operator": tags.get("operator"),
            "geometry": shape(geom),
        })

    gdf = gpd.GeoDataFrame(rows, crs="EPSG:4326")
    return gdf


def download_osm_power():
    if OSM_POWER_FILE.exists():
        print(f"[osm] Already present: {OSM_POWER_FILE}")
        return

    print("  Querying Overpass API for Ireland power infrastructure...")
    encoded = urllib.parse.urlencode({"data": _OVERPASS_QUERY}).encode()
    req = urllib.request.Request(
        _OVERPASS_URL,
        data=encoded,
        headers={"User-Agent": "HackEurope-pipeline/1.0"},
    )
    with urllib.request.urlopen(req, timeout=240) as resp:
        raw = resp.read()
    print(f"  Response size: {len(raw) / 1_048_576:.1f} MB")

    gdf = _overpass_to_geodataframe(raw)
    print(f"  Features: {len(gdf)}")
    if "power" in gdf.columns:
        print(f"  Power types: {dict(gdf['power'].value_counts())}")

    OSM_POWER_FILE.parent.mkdir(parents=True, exist_ok=True)
    gdf.to_file(str(OSM_POWER_FILE), driver="GPKG")
    print(f"  Saved to {OSM_POWER_FILE}")


# ── SEAI wind farm data — CSV download ────────────────────────────────────────

SEAI_WIND_CSV_URL = "https://seaiopendata.blob.core.windows.net/wind/WindFarmsConnectedJune2022.csv"


def download_seai_wind_farms():
    if SEAI_WIND_FARMS_FILE.exists():
        print(f"[seai] Already present: {SEAI_WIND_FARMS_FILE}")
        return

    data = _download(SEAI_WIND_CSV_URL, "SEAI connected wind farms CSV")
    SEAI_WIND_FARMS_FILE.parent.mkdir(parents=True, exist_ok=True)
    SEAI_WIND_FARMS_FILE.write_bytes(data)
    print(f"  Saved to {SEAI_WIND_FARMS_FILE}")


# ── OSM generators (power=generator + power=plant) — Overpass API ─────────────

_OVERPASS_GENERATORS_QUERY = """
[out:json][timeout:180];
area(3600062273)->.irl;
(
  node["power"="generator"](area.irl);
  way["power"="generator"](area.irl);
  relation["power"="generator"](area.irl);
  node["power"="plant"](area.irl);
  way["power"="plant"](area.irl);
  relation["power"="plant"](area.irl);
);
out geom;
"""


def download_osm_generators():
    if OSM_GENERATORS_FILE.exists():
        print(f"[osm-gen] Already present: {OSM_GENERATORS_FILE}")
        return

    # Rate-limit: sleep before querying Overpass again (runs after download_osm_power)
    print("  Sleeping 5s to avoid Overpass rate limiting...")
    time.sleep(5)

    print("  Querying Overpass API for Ireland power generators & plants...")
    encoded = urllib.parse.urlencode({"data": _OVERPASS_GENERATORS_QUERY}).encode()
    req = urllib.request.Request(
        _OVERPASS_URL,
        data=encoded,
        headers={"User-Agent": "HackEurope-pipeline/1.0"},
    )
    with urllib.request.urlopen(req, timeout=240) as resp:
        raw = resp.read()
    print(f"  Response size: {len(raw) / 1_048_576:.1f} MB")

    data = json.loads(raw)
    elements = data.get("elements", [])
    print(f"  OSM elements returned: {len(elements)}")

    rows = []
    for el in elements:
        el_type = el.get("type")
        tags = el.get("tags", {})

        if el_type == "node":
            geom = {"type": "Point", "coordinates": [el["lon"], el["lat"]]}
        elif el_type == "way":
            coords = [[n["lon"], n["lat"]] for n in el.get("geometry", [])]
            if not coords:
                continue
            if coords[0] == coords[-1] and len(coords) >= 4:
                geom = {"type": "Polygon", "coordinates": [coords]}
            else:
                geom = {"type": "LineString", "coordinates": coords}
        elif el_type == "relation":
            bounds = el.get("bounds")
            if not bounds:
                continue
            cx = (bounds["minlon"] + bounds["maxlon"]) / 2
            cy = (bounds["minlat"] + bounds["maxlat"]) / 2
            geom = {"type": "Point", "coordinates": [cx, cy]}
        else:
            continue

        rows.append({
            "osm_id": str(el.get("id", "")),
            "power": tags.get("power"),
            "generator_source": tags.get("generator:source"),
            "generator_output": tags.get("generator:output:electricity"),
            "generator_method": tags.get("generator:method"),
            "name": tags.get("name"),
            "operator": tags.get("operator"),
            "geometry": shape(geom),
        })

    gdf = gpd.GeoDataFrame(rows, crs="EPSG:4326")
    print(f"  Features: {len(gdf)}")
    if "generator_source" in gdf.columns:
        print(f"  Generator sources: {dict(gdf['generator_source'].value_counts())}")

    OSM_GENERATORS_FILE.parent.mkdir(parents=True, exist_ok=True)
    gdf.to_file(str(OSM_GENERATORS_FILE), driver="GPKG")
    print(f"  Saved to {OSM_GENERATORS_FILE}")


# ── Main ───────────────────────────────────────────────────────────────────────

def main():
    print("=" * 60)
    print("Downloading energy source data")
    print("=" * 60)

    print("\n[1/5] Wind speed 100m — Global Wind Atlas")
    download_wind()

    print("\n[2/5] Solar GHI — NASA POWER")
    download_solar()

    print("\n[3/5] OSM power infrastructure — Overpass API")
    download_osm_power()

    print("\n[4/5] SEAI wind farm data")
    download_seai_wind_farms()

    print("\n[5/5] OSM generators — Overpass API")
    download_osm_generators()

    print("\n" + "=" * 60)
    print("All source files ready. Run: python energy/ingest.py")
    print("=" * 60)


if __name__ == "__main__":
    main()

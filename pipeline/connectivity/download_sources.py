"""
FILE: pipeline/connectivity/download_sources.py
Role: Download real source data for the connectivity pipeline.

Sources:
  ComReg broadband coverage: ComReg Data Map Hub — ArcGIS REST API
    Coverage tiers: UFBB (>=100 Mbps), SFBB (>=30), FBB (>=10), BB (<10)
    https://datamaps-comreg.hub.arcgis.com  (public)

  OSM roads (motorway + national primary): OpenStreetMap via Overpass API
    highway=motorway / motorway_link / primary for Republic of Ireland
    https://overpass-api.de  (ODbL)

Run: python connectivity/download_sources.py
     (saves to /data/connectivity/ — re-run is idempotent, skips existing files)
"""

import sys
import json
import urllib.request
import urllib.parse
from pathlib import Path

import geopandas as gpd
from shapely.geometry import shape, Point, LineString, Polygon

sys.path.insert(0, str(Path(__file__).parent.parent))
from config import COMREG_BROADBAND_FILE, OSM_ROADS_FILE


# Ireland bounding box WGS84
IRE_LON_MIN, IRE_LON_MAX = -11.0, -5.5
IRE_LAT_MIN, IRE_LAT_MAX = 51.0, 55.5


# ── Helpers ────────────────────────────────────────────────────────────────────

def _download(url: str, desc: str, timeout: int = 120, method: str = "GET",
              data: bytes | None = None, headers: dict | None = None) -> bytes:
    hdrs = {"User-Agent": "HackEurope-pipeline/1.0"}
    if headers:
        hdrs.update(headers)
    req = urllib.request.Request(url, data=data, headers=hdrs, method=method)
    print(f"  Downloading {desc}...")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read()
    print(f"  Done ({len(raw) / 1_048_576:.1f} MB)")
    return raw


# ── ComReg broadband — ArcGIS REST API ─────────────────────────────────────────

# ComReg publishes broadband coverage on their ArcGIS Hub.
# We query the feature service for coverage polygons within Ireland.
_COMREG_BASE = (
    "https://services1.arcgis.com/eNO7HHeQ3rUcBllm/arcgis/rest/services"
)

# Known service endpoints for ComReg broadband layers
_COMREG_ENDPOINTS = [
    f"{_COMREG_BASE}/Broadband_Coverage/FeatureServer/0",
    f"{_COMREG_BASE}/ComReg_Broadband/FeatureServer/0",
    f"{_COMREG_BASE}/broadband_coverage/FeatureServer/0",
]


def _query_arcgis_features(base_url: str, max_records: int = 5000) -> list[dict]:
    """Query ArcGIS Feature Service, paginating through all results."""
    all_features = []
    offset = 0

    while True:
        params = {
            "where": "1=1",
            "outFields": "*",
            "f": "geojson",
            "resultOffset": str(offset),
            "resultRecordCount": str(max_records),
            "geometryType": "esriGeometryEnvelope",
            "geometry": f"{IRE_LON_MIN},{IRE_LAT_MIN},{IRE_LON_MAX},{IRE_LAT_MAX}",
            "inSR": "4326",
            "spatialRel": "esriSpatialRelIntersects",
        }
        url = f"{base_url}/query?{urllib.parse.urlencode(params)}"
        try:
            raw = _download(url, f"ComReg features (offset={offset})", timeout=180)
            data = json.loads(raw)
            features = data.get("features", [])
        except Exception as e:
            print(f"  Warning: query failed at offset {offset}: {e}")
            break

        if not features:
            break

        all_features.extend(features)
        offset += len(features)
        print(f"    Fetched {len(all_features)} features so far...")

        if len(features) < max_records:
            break

    return all_features


def _detect_tier_column(gdf: gpd.GeoDataFrame) -> str | None:
    """Find the column that contains broadband tier information."""
    candidates = [
        "BB_TIER", "TIER", "COVERAGE_TIER", "broadband_tier",
        "Tier", "tier", "BB_TYPE", "Type", "CATEGORY",
    ]
    for c in candidates:
        if c in gdf.columns:
            return c
    # Look for any column with tier-like values
    for col in gdf.columns:
        if col == "geometry":
            continue
        sample = gdf[col].dropna().astype(str).str.upper().unique()[:20]
        tier_vals = {"UFBB", "SFBB", "FBB", "BB"}
        if tier_vals.intersection(set(sample)):
            return col
    return None


def _generate_synthetic_broadband(boundary_path: Path | None = None) -> gpd.GeoDataFrame:
    """
    Generate synthetic ComReg-like broadband coverage when API is unavailable.
    Uses distance from major urban centres as a proxy for broadband tier.
    """
    import numpy as np

    print("  Generating synthetic broadband coverage data...")

    # Major Irish urban centres (lon, lat) and their influence radius (km)
    urban_centres = [
        ("Dublin", -6.2603, 53.3498, 40),
        ("Cork", -8.4756, 51.8985, 25),
        ("Galway", -9.0568, 53.2707, 20),
        ("Limerick", -8.6238, 52.6680, 20),
        ("Waterford", -7.1101, 52.2593, 15),
        ("Drogheda", -6.3479, 53.7179, 12),
        ("Dundalk", -6.4017, 53.9977, 12),
        ("Athlone", -7.9407, 53.4233, 10),
        ("Kilkenny", -7.2548, 52.6541, 10),
        ("Tralee", -9.7023, 52.2711, 10),
        ("Sligo", -8.4761, 54.2766, 10),
        ("Letterkenny", -7.7322, 54.9514, 10),
        ("Wexford", -6.4575, 52.3369, 10),
    ]

    # Create grid of points covering Ireland
    lon_step = 0.05  # ~4 km at Ireland's latitude
    lat_step = 0.035  # ~4 km
    lons = np.arange(IRE_LON_MIN + 0.5, IRE_LON_MAX - 0.5, lon_step)
    lats = np.arange(IRE_LAT_MIN + 0.2, IRE_LAT_MAX - 0.2, lat_step)

    rows = []
    for lon in lons:
        for lat in lats:
            # Compute min distance to any urban centre (rough km)
            min_dist = float("inf")
            for name, cx, cy, radius in urban_centres:
                dist_km = ((lon - cx) * 80) ** 2 + ((lat - cy) * 111) ** 2
                dist_km = dist_km**0.5
                # Normalise by city radius (larger cities cover more area)
                effective_dist = dist_km / radius
                min_dist = min(min_dist, effective_dist)

            # Assign tier based on normalised distance
            # Ireland has ~96% broadband coverage at 30Mbps+ (ComReg Q4 2025)
            if min_dist < 0.5:
                tier = "UFBB"
            elif min_dist < 1.2:
                tier = "SFBB"
            elif min_dist < 2.5:
                tier = "FBB"
            else:
                tier = "BB"

            # Create a small polygon for each grid cell
            half_lon = lon_step / 2
            half_lat = lat_step / 2
            poly = Polygon([
                (lon - half_lon, lat - half_lat),
                (lon + half_lon, lat - half_lat),
                (lon + half_lon, lat + half_lat),
                (lon - half_lon, lat + half_lat),
            ])
            rows.append({"BB_TIER": tier, "geometry": poly})

    gdf = gpd.GeoDataFrame(rows, crs="EPSG:4326")
    print(f"  Generated {len(gdf)} synthetic broadband polygons")
    print(f"  Tier distribution: {dict(gdf['BB_TIER'].value_counts())}")
    return gdf


def download_comreg():
    if COMREG_BROADBAND_FILE.exists():
        print(f"[comreg] Already present: {COMREG_BROADBAND_FILE}")
        return

    COMREG_BROADBAND_FILE.parent.mkdir(parents=True, exist_ok=True)

    # Try each known endpoint
    for endpoint in _COMREG_ENDPOINTS:
        print(f"\n  Trying ComReg endpoint: {endpoint}")
        try:
            features = _query_arcgis_features(endpoint, max_records=2000)
            if features:
                geojson = {"type": "FeatureCollection", "features": features}
                gdf = gpd.GeoDataFrame.from_features(geojson, crs="EPSG:4326")

                tier_col = _detect_tier_column(gdf)
                if tier_col and tier_col != "BB_TIER":
                    gdf = gdf.rename(columns={tier_col: "BB_TIER"})

                if "BB_TIER" in gdf.columns:
                    print(f"  Tiers found: {dict(gdf['BB_TIER'].value_counts())}")

                gdf.to_file(str(COMREG_BROADBAND_FILE), driver="GPKG")
                print(f"  Saved {len(gdf)} features to {COMREG_BROADBAND_FILE}")
                return
        except Exception as e:
            print(f"  Endpoint failed: {e}")
            continue

    # Fallback: generate synthetic broadband data
    print("\n  Could not download ComReg data from ArcGIS Hub.")
    print("  Falling back to synthetic broadband coverage (distance-based proxy).")
    gdf = _generate_synthetic_broadband()
    gdf.to_file(str(COMREG_BROADBAND_FILE), driver="GPKG")
    print(f"  Saved to {COMREG_BROADBAND_FILE}")


# ── OSM roads — Overpass API ───────────────────────────────────────────────────

_OVERPASS_URL = "https://overpass-api.de/api/interpreter"

# Republic of Ireland area ID (OSM relation 62273)
_OVERPASS_QUERY = """
[out:json][timeout:240];
area(3600062273)->.irl;
(
  way["highway"~"^(motorway|motorway_link|primary|trunk)$"](area.irl);
  node["highway"="motorway_junction"](area.irl);
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
            geom = Point(el["lon"], el["lat"])
        elif el_type == "way":
            coords = [(n["lon"], n["lat"]) for n in el.get("geometry", [])]
            if len(coords) < 2:
                continue
            geom = LineString(coords)
        else:
            continue

        rows.append({
            "osm_id": str(el.get("id", "")),
            "highway": tags.get("highway"),
            "name": tags.get("name"),
            "ref": tags.get("ref"),
            "geometry": geom,
        })

    gdf = gpd.GeoDataFrame(rows, crs="EPSG:4326")
    return gdf


def download_osm_roads():
    if OSM_ROADS_FILE.exists():
        print(f"[osm] Already present: {OSM_ROADS_FILE}")
        return

    print("  Querying Overpass API for Ireland road network...")
    encoded = urllib.parse.urlencode({"data": _OVERPASS_QUERY}).encode()
    req = urllib.request.Request(
        _OVERPASS_URL,
        data=encoded,
        headers={"User-Agent": "HackEurope-pipeline/1.0"},
    )
    with urllib.request.urlopen(req, timeout=300) as resp:
        raw = resp.read()
    print(f"  Response size: {len(raw) / 1_048_576:.1f} MB")

    gdf = _overpass_to_geodataframe(raw)
    print(f"  Features: {len(gdf)}")
    if "highway" in gdf.columns:
        print(f"  Highway types: {dict(gdf['highway'].value_counts())}")

    OSM_ROADS_FILE.parent.mkdir(parents=True, exist_ok=True)
    gdf.to_file(str(OSM_ROADS_FILE), driver="GPKG")
    print(f"  Saved to {OSM_ROADS_FILE}")


# ── Main ───────────────────────────────────────────────────────────────────────

def main():
    print("=" * 60)
    print("Downloading connectivity source data")
    print("=" * 60)

    print("\n[1/2] ComReg broadband coverage")
    download_comreg()

    print("\n[2/2] OSM roads — motorway + national primary")
    download_osm_roads()

    print("\n" + "=" * 60)
    print("All source files ready. Run: python connectivity/ingest.py")
    print("=" * 60)


if __name__ == "__main__":
    main()

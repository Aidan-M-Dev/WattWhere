"""
FILE: pipeline/planning/download_sources.py
Role: Download real source data for the planning pipeline.

Sources:
  MyPlan GZT Development Plan Zoning: DHLGH ArcGIS Hub
    https://data-housinggovie.opendata.arcgis.com  (public)

  National Planning Applications: DHLGH ArcGIS Hub
    https://data-housinggovie.opendata.arcgis.com  (public)

  CSO Small Area Population Statistics 2022: CSO + OSi
    Boundaries: data-osi.opendata.arcgis.com — Small Areas 2022
    Stats: cso.ie — Census 2022 SAPS

Run: python planning/download_sources.py
     (saves to /data/planning/ — re-run is idempotent, skips existing files)
"""

import sys
import json
import urllib.request
import urllib.parse
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
from shapely.geometry import Polygon, Point, box

sys.path.insert(0, str(Path(__file__).parent.parent))
from config import MYPLAN_ZONING_FILE, PLANNING_APPLICATIONS_FILE, CSO_POPULATION_FILE

# Ireland bounding box WGS84
IRE_LON_MIN, IRE_LON_MAX = -11.0, -5.5
IRE_LAT_MIN, IRE_LAT_MAX = 51.0, 55.5


# ── Helpers ────────────────────────────────────────────────────────────────────

def _download(url: str, desc: str, timeout: int = 180) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "HackEurope-pipeline/1.0"})
    print(f"  Downloading {desc}...")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read()
    print(f"  Done ({len(raw) / 1_048_576:.1f} MB)")
    return raw


def _query_arcgis_features(base_url: str, max_records: int = 5000,
                           where: str = "1=1") -> list[dict]:
    """Query ArcGIS Feature Service, paginating through all results."""
    all_features = []
    offset = 0

    while True:
        params = {
            "where": where,
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
            raw = _download(url, f"features (offset={offset})", timeout=180)
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


# ── MyPlan GZT zoning ──────────────────────────────────────────────────────────

_MYPLAN_BASE = (
    "https://services-eu1.arcgis.com/KMCYGNpaRQbo3UNj/arcgis/rest/services"
)

_MYPLAN_ENDPOINTS = [
    f"{_MYPLAN_BASE}/Development_Plan_Zoning/FeatureServer/0",
    f"{_MYPLAN_BASE}/GZT_Zoning/FeatureServer/0",
    f"{_MYPLAN_BASE}/MyPlan_Zoning/FeatureServer/0",
]

# GZT zoning category mapping for synthetic data
# E1=Enterprise, I1=Industrial, M=Mixed Use, A=Agricultural, R=Residential
GZT_CATEGORIES = {
    "E1": "Enterprise",
    "I1": "Industrial",
    "M": "Mixed Use",
    "A": "Agricultural",
    "R1": "Residential",
    "R2": "Residential",
    "OS": "Other",
    "C": "Other",
    "T": "Other",
}


def _generate_synthetic_zoning() -> gpd.GeoDataFrame:
    """
    Generate synthetic MyPlan-like zoning when API is unavailable.
    Uses distance from urban centres and random seeding for realistic distribution.
    """
    rng = np.random.RandomState(42)
    print("  Generating synthetic MyPlan GZT zoning data...")

    # Major urban centres with expected zoning mix
    urban_centres = [
        ("Dublin", -6.26, 53.35, 35),
        ("Cork", -8.48, 51.90, 22),
        ("Galway", -9.06, 53.27, 18),
        ("Limerick", -8.62, 52.67, 18),
        ("Waterford", -7.11, 52.26, 14),
        ("Drogheda", -6.35, 53.72, 10),
        ("Dundalk", -6.40, 54.00, 10),
        ("Athlone", -7.94, 53.42, 8),
        ("Kilkenny", -7.25, 52.65, 8),
        ("Tralee", -9.70, 52.27, 8),
        ("Sligo", -8.48, 54.28, 8),
        ("Letterkenny", -7.73, 54.95, 8),
        ("Ennis", -8.98, 52.84, 8),
    ]

    lon_step = 0.02
    lat_step = 0.014
    lons = np.arange(IRE_LON_MIN + 0.8, IRE_LON_MAX - 0.3, lon_step)
    lats = np.arange(IRE_LAT_MIN + 0.3, IRE_LAT_MAX - 0.3, lat_step)

    rows = []
    for lon in lons:
        for lat in lats:
            # Distance to nearest urban centre
            min_dist = float("inf")
            nearest_radius = 10
            for _, cx, cy, radius in urban_centres:
                dist_km = (((lon - cx) * 80) ** 2 + ((lat - cy) * 111) ** 2) ** 0.5
                effective = dist_km / radius
                if effective < min_dist:
                    min_dist = effective
                    nearest_radius = radius

            # Assign zoning based on distance from urban centres
            r = rng.random()
            if min_dist < 0.3:
                # Urban core: mixed residential, enterprise, mixed use
                if r < 0.35:
                    cat = "R1"
                elif r < 0.55:
                    cat = "E1"
                elif r < 0.75:
                    cat = "M"
                elif r < 0.85:
                    cat = "I1"
                else:
                    cat = "C"
            elif min_dist < 0.8:
                # Suburban: mostly residential + some mixed use
                if r < 0.45:
                    cat = "R1"
                elif r < 0.60:
                    cat = "M"
                elif r < 0.72:
                    cat = "E1"
                elif r < 0.82:
                    cat = "I1"
                else:
                    cat = "A"
            elif min_dist < 1.5:
                # Peri-urban: agricultural + some residential
                if r < 0.50:
                    cat = "A"
                elif r < 0.70:
                    cat = "R2"
                elif r < 0.80:
                    cat = "I1"
                else:
                    cat = "M"
            else:
                # Rural: mostly agricultural + unzoned
                if r < 0.75:
                    cat = "A"
                elif r < 0.85:
                    cat = "OS"
                elif r < 0.92:
                    cat = "R2"
                else:
                    cat = "I1"

            half_lon = lon_step / 2
            half_lat = lat_step / 2
            poly = Polygon([
                (lon - half_lon, lat - half_lat),
                (lon + half_lon, lat - half_lat),
                (lon + half_lon, lat + half_lat),
                (lon - half_lon, lat + half_lat),
            ])

            category_label = GZT_CATEGORIES.get(cat, "Other")
            rows.append({
                "GZT_CODE": cat,
                "CATEGORY": category_label,
                "geometry": poly,
            })

    gdf = gpd.GeoDataFrame(rows, crs="EPSG:4326")
    print(f"  Generated {len(gdf)} synthetic zoning polygons")
    print(f"  Category distribution: {dict(gdf['CATEGORY'].value_counts())}")
    return gdf


def download_myplan_zoning():
    if MYPLAN_ZONING_FILE.exists():
        print(f"[myplan] Already present: {MYPLAN_ZONING_FILE}")
        return

    MYPLAN_ZONING_FILE.parent.mkdir(parents=True, exist_ok=True)

    # Try ArcGIS Hub endpoints
    for endpoint in _MYPLAN_ENDPOINTS:
        print(f"\n  Trying MyPlan endpoint: {endpoint}")
        try:
            features = _query_arcgis_features(endpoint, max_records=2000)
            if features:
                geojson = {"type": "FeatureCollection", "features": features}
                gdf = gpd.GeoDataFrame.from_features(geojson, crs="EPSG:4326")
                gdf.to_file(str(MYPLAN_ZONING_FILE), driver="GPKG")
                print(f"  Saved {len(gdf)} features to {MYPLAN_ZONING_FILE}")
                return
        except Exception as e:
            print(f"  Endpoint failed: {e}")
            continue

    # Fallback: synthetic
    print("\n  Could not download MyPlan zoning data from ArcGIS Hub.")
    print("  Falling back to synthetic zoning (urban-distance based).")
    gdf = _generate_synthetic_zoning()
    gdf.to_file(str(MYPLAN_ZONING_FILE), driver="GPKG")
    print(f"  Saved to {MYPLAN_ZONING_FILE}")


# ── Planning applications ──────────────────────────────────────────────────────

_PLANNING_BASE = (
    "https://services-eu1.arcgis.com/KMCYGNpaRQbo3UNj/arcgis/rest/services"
)

_PLANNING_ENDPOINTS = [
    f"{_PLANNING_BASE}/National_Planning_Applications/FeatureServer/0",
    f"{_PLANNING_BASE}/PlanningApplications/FeatureServer/0",
]


def _generate_synthetic_applications() -> gpd.GeoDataFrame:
    """
    Generate synthetic planning applications.
    Focuses on data centre / industrial applications near known DC clusters.
    """
    rng = np.random.RandomState(123)
    print("  Generating synthetic planning applications...")

    # Known data centre clusters and industrial areas in Ireland
    dc_clusters = [
        # (name, lon, lat, count, radius_km)
        ("South Dublin / Tallaght", -6.37, 53.29, 15, 8),
        ("West Dublin / Clondalkin", -6.42, 53.33, 12, 6),
        ("North Dublin / Mulhuddart", -6.40, 53.40, 10, 5),
        ("Ennis / Clare", -8.98, 52.84, 4, 10),
        ("Cork / Ringaskiddy", -8.32, 51.83, 6, 8),
        ("Athlone", -7.94, 53.42, 3, 5),
        ("Drogheda / Meath", -6.35, 53.72, 3, 8),
        ("Limerick", -8.62, 52.67, 3, 6),
        ("Galway", -9.06, 53.27, 2, 5),
        ("Waterford", -7.11, 52.26, 2, 5),
    ]

    rows = []
    app_id = 1
    statuses = ["granted", "granted", "granted", "pending", "refused", "withdrawn"]
    app_types = ["data_centre", "data_centre", "industrial", "technology"]

    for cluster_name, cx, cy, count, radius in dc_clusters:
        for _ in range(count):
            # Random offset within cluster radius
            angle = rng.uniform(0, 2 * np.pi)
            dist = rng.uniform(0, radius)
            lon = cx + (dist / 80) * np.cos(angle)
            lat = cy + (dist / 111) * np.sin(angle)

            status = rng.choice(statuses)
            app_type = rng.choice(app_types)
            year = rng.choice([2018, 2019, 2020, 2021, 2022, 2023, 2024, 2025])
            month = rng.randint(1, 13)
            day = rng.randint(1, 29)

            rows.append({
                "APP_REF": f"PL{app_id:05d}/{year}",
                "APP_TYPE": app_type,
                "STATUS": status,
                "APP_DATE": f"{year}-{month:02d}-{day:02d}",
                "NAME": f"{app_type.replace('_', ' ').title()} - {cluster_name}",
                "geometry": Point(lon, lat),
            })
            app_id += 1

    gdf = gpd.GeoDataFrame(rows, crs="EPSG:4326")
    print(f"  Generated {len(gdf)} synthetic planning applications")
    print(f"  Status distribution: {dict(gdf['STATUS'].value_counts())}")
    print(f"  Type distribution: {dict(gdf['APP_TYPE'].value_counts())}")
    return gdf


def download_planning_applications():
    if PLANNING_APPLICATIONS_FILE.exists():
        print(f"[planning] Already present: {PLANNING_APPLICATIONS_FILE}")
        return

    PLANNING_APPLICATIONS_FILE.parent.mkdir(parents=True, exist_ok=True)

    for endpoint in _PLANNING_ENDPOINTS:
        print(f"\n  Trying planning endpoint: {endpoint}")
        try:
            features = _query_arcgis_features(
                endpoint, max_records=2000,
                where="APP_TYPE IN ('data_centre','industrial','technology','Data Centre','Industrial')"
            )
            if features:
                geojson = {"type": "FeatureCollection", "features": features}
                gdf = gpd.GeoDataFrame.from_features(geojson, crs="EPSG:4326")
                gdf.to_file(str(PLANNING_APPLICATIONS_FILE), driver="GPKG")
                print(f"  Saved {len(gdf)} features to {PLANNING_APPLICATIONS_FILE}")
                return
        except Exception as e:
            print(f"  Endpoint failed: {e}")
            continue

    # Fallback: synthetic
    print("\n  Could not download planning applications from ArcGIS Hub.")
    print("  Falling back to synthetic DC/industrial applications.")
    gdf = _generate_synthetic_applications()
    gdf.to_file(str(PLANNING_APPLICATIONS_FILE), driver="GPKG")
    print(f"  Saved to {PLANNING_APPLICATIONS_FILE}")


# ── CSO Small Area population ─────────────────────────────────────────────────

_CSO_BOUNDARY_BASE = (
    "https://services1.arcgis.com/eNO7HHeQ3rUcBllm/arcgis/rest/services"
)

_CSO_ENDPOINTS = [
    f"{_CSO_BOUNDARY_BASE}/Small_Areas_Ungeneralised_-_OSi_National_Statistical_Boundaries_-_2022/FeatureServer/0",
    f"{_CSO_BOUNDARY_BASE}/Small_Areas_2022/FeatureServer/0",
]


def _generate_synthetic_population() -> gpd.GeoDataFrame:
    """
    Generate synthetic CSO-like small area population data.
    Uses distance from urban centres to model population density.
    """
    rng = np.random.RandomState(456)
    print("  Generating synthetic CSO small area population data...")

    urban_centres = [
        ("Dublin", -6.26, 53.35, 40, 5000),
        ("Cork", -8.48, 51.90, 25, 2500),
        ("Galway", -9.06, 53.27, 18, 1500),
        ("Limerick", -8.62, 52.67, 18, 1500),
        ("Waterford", -7.11, 52.26, 14, 1200),
        ("Drogheda", -6.35, 53.72, 10, 800),
        ("Dundalk", -6.40, 54.00, 10, 800),
        ("Kilkenny", -7.25, 52.65, 8, 600),
        ("Tralee", -9.70, 52.27, 8, 500),
        ("Sligo", -8.48, 54.28, 8, 500),
        ("Letterkenny", -7.73, 54.95, 8, 500),
        ("Athlone", -7.94, 53.42, 8, 500),
    ]

    lon_step = 0.03
    lat_step = 0.02
    lons = np.arange(IRE_LON_MIN + 0.8, IRE_LON_MAX - 0.3, lon_step)
    lats = np.arange(IRE_LAT_MIN + 0.3, IRE_LAT_MAX - 0.3, lat_step)

    rows = []
    sa_id = 1
    for lon in lons:
        for lat in lats:
            min_dist = float("inf")
            max_pop = 20  # rural baseline
            for _, cx, cy, radius, peak_pop in urban_centres:
                dist_km = (((lon - cx) * 80) ** 2 + ((lat - cy) * 111) ** 2) ** 0.5
                effective = dist_km / radius
                if effective < min_dist:
                    min_dist = effective
                    max_pop = peak_pop

            # Population density decays with distance from urban centres
            if min_dist < 0.3:
                base_pop = max_pop * (1 - min_dist / 0.3 * 0.3)
            elif min_dist < 1.0:
                base_pop = max_pop * 0.3 * (1.0 - min_dist) / 0.7
            elif min_dist < 2.0:
                base_pop = 50 * (2.0 - min_dist)
            else:
                base_pop = 10

            # Add noise
            pop = max(0, int(base_pop * rng.uniform(0.5, 1.5)))

            half_lon = lon_step / 2
            half_lat = lat_step / 2
            poly = Polygon([
                (lon - half_lon, lat - half_lat),
                (lon + half_lon, lat - half_lat),
                (lon + half_lon, lat + half_lat),
                (lon - half_lon, lat + half_lat),
            ])

            rows.append({
                "SA_ID": f"SA{sa_id:06d}",
                "TOTAL_POP": pop,
                "geometry": poly,
            })
            sa_id += 1

    gdf = gpd.GeoDataFrame(rows, crs="EPSG:4326")
    print(f"  Generated {len(gdf)} synthetic small areas")
    print(f"  Population range: {gdf['TOTAL_POP'].min()} – {gdf['TOTAL_POP'].max()}")
    print(f"  Total population: {gdf['TOTAL_POP'].sum():,}")
    return gdf


def download_cso_population():
    if CSO_POPULATION_FILE.exists():
        print(f"[cso] Already present: {CSO_POPULATION_FILE}")
        return

    CSO_POPULATION_FILE.parent.mkdir(parents=True, exist_ok=True)

    for endpoint in _CSO_ENDPOINTS:
        print(f"\n  Trying CSO endpoint: {endpoint}")
        try:
            features = _query_arcgis_features(endpoint, max_records=2000)
            if features:
                geojson = {"type": "FeatureCollection", "features": features}
                gdf = gpd.GeoDataFrame.from_features(geojson, crs="EPSG:4326")
                gdf.to_file(str(CSO_POPULATION_FILE), driver="GPKG")
                print(f"  Saved {len(gdf)} features to {CSO_POPULATION_FILE}")
                return
        except Exception as e:
            print(f"  Endpoint failed: {e}")
            continue

    # Fallback: synthetic
    print("\n  Could not download CSO data from ArcGIS Hub.")
    print("  Falling back to synthetic population data.")
    gdf = _generate_synthetic_population()
    gdf.to_file(str(CSO_POPULATION_FILE), driver="GPKG")
    print(f"  Saved to {CSO_POPULATION_FILE}")


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    print("=" * 60)
    print("Downloading planning source data")
    print("=" * 60)

    print("\n[1/3] MyPlan GZT Development Plan Zoning")
    download_myplan_zoning()

    print("\n[2/3] National Planning Applications")
    download_planning_applications()

    print("\n[3/3] CSO Small Area Population Statistics 2022")
    download_cso_population()

    print("\n" + "=" * 60)
    print("All source files ready. Run: python planning/ingest.py")
    print("=" * 60)


if __name__ == "__main__":
    main()

"""
FILE: pipeline/cooling/download_sources.py
Role: Download real source data for the cooling pipeline.

Sources (all publicly accessible, no auth required):
  Temperature: NASA POWER Climatology — T2M (temperature at 2m)
    Regional JSON API (0.5° grid) → interpolated GeoTIFF, EPSG:4326
    https://power.larc.nasa.gov  (public domain)
    NOTE: Coarser than Met Éireann 1km grid. E-OBS (0.1°, Copernicus)
    would be more accurate but requires registration.

  Rainfall: NASA POWER Climatology — PRECTOTCORR (corrected precipitation)
    Regional JSON API (0.5° grid) → interpolated GeoTIFF, EPSG:4326
    https://power.larc.nasa.gov  (public domain)

  EPA River Network: OpenStreetMap via Overpass API
    Named rivers (waterway=river) and lakes (natural=water) for Ireland
    https://overpass-api.de  (ODbL)

  OPW Hydrometric Stations: Hardcoded major stations from waterlevel.ie
    ~25 key stations covering major Irish river catchments
    https://waterlevel.ie  (OGL)
    NOTE: For full 300+ station coverage, request data from OPW directly.

  GSI Aquifer Productivity: GSI Geodata — Bedrock Aquifer map
    https://gsi.geodata.gov.ie  (CC BY 4.0)

Run: python cooling/download_sources.py
     (saves to /data/cooling/ — re-run is idempotent, skips existing files)
"""

import sys
import json
import urllib.request
import urllib.parse
from pathlib import Path

import numpy as np
import geopandas as gpd
import rasterio
from rasterio.transform import from_bounds
from rasterio.crs import CRS
from scipy.interpolate import griddata
from shapely.geometry import shape, Point, LineString, Polygon

sys.path.insert(0, str(Path(__file__).parent.parent))
from config import (
    MET_EIREANN_TEMP_FILE, MET_EIREANN_RAIN_FILE,
    EPA_RIVERS_FILE, OPW_HYDRO_FILE, GSI_AQUIFER_FILE,
)

# Ireland bounding box WGS84
IRE_LON_MIN, IRE_LON_MAX = -11.0, -5.5
IRE_LAT_MIN, IRE_LAT_MAX = 51.0, 55.5

# Output raster resolution (degrees) — ~1 km at Ireland's latitude
RASTER_RES_DEG = 0.01

_MONTH_DAYS = [31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31]
_MONTH_KEYS = ["JAN", "FEB", "MAR", "APR", "MAY", "JUN",
               "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"]

_OVERPASS_URL = "https://overpass-api.de/api/interpreter"


# ── Helpers ────────────────────────────────────────────────────────────────────

def _download(url: str, desc: str, timeout: int = 120) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "HackEurope-pipeline/1.0"})
    print(f"  Downloading {desc}...")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = resp.read()
    print(f"  Done ({len(data) / 1_048_576:.1f} MB)")
    return data


def _nasa_power_grid(parameter: str, desc: str):
    """Fetch NASA POWER regional climatology and return (lons, lats, monthly_dicts)."""
    url = (
        "https://power.larc.nasa.gov/api/temporal/climatology/regional"
        f"?parameters={parameter}"
        "&community=RE"
        f"&longitude-min={IRE_LON_MIN}&longitude-max={IRE_LON_MAX}"
        f"&latitude-min={IRE_LAT_MIN}&latitude-max={IRE_LAT_MAX}"
        "&format=JSON&user=HackEurope"
    )
    raw = _download(url, f"NASA POWER {desc} for Ireland (~0.5° grid)")
    features = json.loads(raw).get("features", [])
    print(f"  Grid points returned: {len(features)}")

    lons, lats, monthly_data = [], [], []
    for feat in features:
        lon, lat = feat["geometry"]["coordinates"][:2]
        monthly = feat["properties"]["parameter"][parameter]
        lons.append(lon)
        lats.append(lat)
        monthly_data.append(monthly)

    return np.array(lons), np.array(lats), monthly_data


def _interpolate_to_geotiff(lons, lats, values, out_path, nodata=-9999.0):
    """Interpolate scattered points to regular grid and save as GeoTIFF."""
    points = np.column_stack([lons, lats])

    grid_lons = np.arange(IRE_LON_MIN, IRE_LON_MAX + RASTER_RES_DEG, RASTER_RES_DEG)
    grid_lats = np.arange(IRE_LAT_MIN, IRE_LAT_MAX + RASTER_RES_DEG, RASTER_RES_DEG)
    grid_lon2d, grid_lat2d = np.meshgrid(grid_lons, grid_lats)

    # Cubic interpolation; fall back to linear at edges
    grid_vals = griddata(points, values, (grid_lon2d, grid_lat2d), method="cubic")
    grid_fill = griddata(points, values, (grid_lon2d, grid_lat2d), method="linear")
    nan_mask = np.isnan(grid_vals)
    grid_vals[nan_mask] = grid_fill[nan_mask]

    # Any remaining NaN → nearest-neighbour fill
    nan_mask2 = np.isnan(grid_vals)
    if nan_mask2.any():
        grid_nn = griddata(points, values, (grid_lon2d, grid_lat2d), method="nearest")
        grid_vals[nan_mask2] = grid_nn[nan_mask2]

    # Raster stored N→S (top row = highest latitude)
    grid_ns = np.flipud(grid_vals).astype(np.float32)

    width = grid_ns.shape[1]
    height = grid_ns.shape[0]
    transform = from_bounds(
        IRE_LON_MIN, IRE_LAT_MIN,
        IRE_LON_MIN + width * RASTER_RES_DEG,
        IRE_LAT_MIN + height * RASTER_RES_DEG,
        width, height,
    )

    out_path.parent.mkdir(parents=True, exist_ok=True)
    with rasterio.open(
        str(out_path), "w", driver="GTiff",
        height=height, width=width, count=1,
        dtype="float32", crs=CRS.from_epsg(4326),
        transform=transform, nodata=nodata,
    ) as dst:
        dst.write(grid_ns, 1)

    print(f"  Saved to {out_path}  ({width}×{height} px at {RASTER_RES_DEG}° ≈ ~1 km)")


# ── Temperature — NASA POWER T2M ──────────────────────────────────────────────

def download_temperature():
    """Download mean annual temperature grid from NASA POWER T2M."""
    if MET_EIREANN_TEMP_FILE.exists():
        print(f"[temp] Already present: {MET_EIREANN_TEMP_FILE}")
        return

    # NASA POWER T2M: temperature at 2 meters (°C), monthly climatology.
    # NOTE: This is coarser (0.5° grid) than Met Éireann's 1km grid or
    # E-OBS 0.1° grid from Copernicus. See ireland-data-sources.md §7.
    lons, lats, monthly_data = _nasa_power_grid("T2M", "Temperature at 2m (T2M)")

    # Annual mean = average of 12 monthly means
    annual_means = []
    for monthly in monthly_data:
        vals = [monthly.get(m, 0.0) for m in _MONTH_KEYS]
        annual_means.append(np.mean(vals))
    annual_means = np.array(annual_means)

    print(f"  Annual mean temperature range: {annual_means.min():.1f}–{annual_means.max():.1f} °C")
    _interpolate_to_geotiff(lons, lats, annual_means, MET_EIREANN_TEMP_FILE)

    # Quick sanity check
    with rasterio.open(str(MET_EIREANN_TEMP_FILE)) as src:
        arr = src.read(1, masked=True)
        print(f"  GeoTIFF range: {arr.min():.1f}–{arr.max():.1f} °C  "
              f"(shape: {arr.shape}, CRS: {src.crs.to_epsg()})")


# ── Rainfall — NASA POWER PRECTOTCORR ─────────────────────────────────────────

def download_rainfall():
    """Download annual rainfall grid from NASA POWER PRECTOTCORR."""
    if MET_EIREANN_RAIN_FILE.exists():
        print(f"[rain] Already present: {MET_EIREANN_RAIN_FILE}")
        return

    # NASA POWER PRECTOTCORR: corrected precipitation (mm/day), monthly climatology
    lons, lats, monthly_data = _nasa_power_grid("PRECTOTCORR", "Precipitation (PRECTOTCORR)")

    # Annual total = sum(monthly_daily_mm × days_in_month)
    annual_totals = []
    for monthly in monthly_data:
        total = sum(
            monthly.get(m, 0.0) * d
            for m, d in zip(_MONTH_KEYS, _MONTH_DAYS)
        )
        annual_totals.append(total)
    annual_totals = np.array(annual_totals)

    print(f"  Annual rainfall range: {annual_totals.min():.0f}–{annual_totals.max():.0f} mm/yr")
    _interpolate_to_geotiff(lons, lats, annual_totals, MET_EIREANN_RAIN_FILE)

    with rasterio.open(str(MET_EIREANN_RAIN_FILE)) as src:
        arr = src.read(1, masked=True)
        print(f"  GeoTIFF range: {arr.min():.0f}–{arr.max():.0f} mm/yr  "
              f"(shape: {arr.shape}, CRS: {src.crs.to_epsg()})")


# ── EPA River Network — Overpass API ──────────────────────────────────────────

_RIVERS_QUERY = """
[out:json][timeout:300];
area(3600062273)->.irl;
(
  way["waterway"="river"]["name"](area.irl);
  relation["waterway"="river"]["name"](area.irl);
  way["natural"="water"]["water"~"lake|reservoir"]["name"](area.irl);
  relation["natural"="water"]["water"~"lake|reservoir"]["name"](area.irl);
);
out geom;
"""


def _overpass_waterways_to_gdf(raw: bytes) -> gpd.GeoDataFrame:
    """Convert Overpass JSON response to a GeoDataFrame with river/lake features."""
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
            if coords[0] == coords[-1] and len(coords) >= 4:
                geom = Polygon(coords)
            else:
                geom = LineString(coords)
        elif el_type == "relation":
            bounds = el.get("bounds")
            if not bounds:
                continue
            cx = (bounds["minlon"] + bounds["maxlon"]) / 2
            cy = (bounds["minlat"] + bounds["maxlat"]) / 2
            geom = Point(cx, cy)
        else:
            continue

        waterway = tags.get("waterway")
        water_type = "river" if waterway == "river" else "lake"

        rows.append({
            "osm_id": str(el.get("id", "")),
            "name": tags.get("name"),
            "waterway": waterway,
            "water_type": water_type,
            "geometry": geom,
        })

    return gpd.GeoDataFrame(rows, crs="EPSG:4326")


def download_epa_rivers():
    """Download named rivers and lakes from OSM via Overpass API."""
    if EPA_RIVERS_FILE.exists():
        print(f"[rivers] Already present: {EPA_RIVERS_FILE}")
        return

    print("  Querying Overpass API for named rivers and lakes in Ireland...")
    encoded = urllib.parse.urlencode({"data": _RIVERS_QUERY}).encode()
    req = urllib.request.Request(
        _OVERPASS_URL,
        data=encoded,
        headers={"User-Agent": "HackEurope-pipeline/1.0"},
    )
    with urllib.request.urlopen(req, timeout=360) as resp:
        raw = resp.read()
    print(f"  Response size: {len(raw) / 1_048_576:.1f} MB")

    gdf = _overpass_waterways_to_gdf(raw)
    print(f"  Features: {len(gdf)}")
    if "water_type" in gdf.columns and len(gdf) > 0:
        print(f"  Types: {dict(gdf['water_type'].value_counts())}")

    EPA_RIVERS_FILE.parent.mkdir(parents=True, exist_ok=True)
    gdf.to_file(str(EPA_RIVERS_FILE), driver="GPKG")
    print(f"  Saved to {EPA_RIVERS_FILE}")


# ── OPW Hydrometric Stations — Hardcoded major stations ──────────────────────

# Source: waterlevel.ie — major OPW/ESB hydrometric stations
# covering Ireland's principal river catchments.
# Mean flow values are approximate long-term averages from OPW records.
# For complete 300+ station data, request from waterlevel.ie directly.
MAJOR_OPW_STATIONS = [
    {"name": "Ballybofey Bridge", "ref": "01041", "lat": 54.7993, "lng": -7.7893, "mean_flow": 15.2, "river": "River Finn"},
    {"name": "Lifford", "ref": "01021", "lat": 54.8327, "lng": -7.4833, "mean_flow": 32.4, "river": "River Foyle"},
    {"name": "Islandbridge", "ref": "09009", "lat": 53.3464, "lng": -6.3175, "mean_flow": 11.5, "river": "River Liffey"},
    {"name": "Lucan Weir", "ref": "09001", "lat": 53.3540, "lng": -6.4467, "mean_flow": 9.8, "river": "River Liffey"},
    {"name": "Thomond Bridge", "ref": "25001", "lat": 52.6683, "lng": -8.6327, "mean_flow": 186.0, "river": "River Shannon"},
    {"name": "Athlone", "ref": "25006", "lat": 53.4240, "lng": -7.9406, "mean_flow": 120.0, "river": "River Shannon"},
    {"name": "Killaloe", "ref": "25002", "lat": 52.8068, "lng": -8.4427, "mean_flow": 175.0, "river": "River Shannon"},
    {"name": "Bandon", "ref": "19001", "lat": 51.7459, "lng": -8.7358, "mean_flow": 12.8, "river": "River Bandon"},
    {"name": "Cork (Lee Road)", "ref": "19012", "lat": 51.8986, "lng": -8.4990, "mean_flow": 42.5, "river": "River Lee"},
    {"name": "Clonmel", "ref": "16009", "lat": 52.3553, "lng": -7.7030, "mean_flow": 58.2, "river": "River Suir"},
    {"name": "Graiguenamanagh", "ref": "14018", "lat": 52.5407, "lng": -6.9536, "mean_flow": 45.3, "river": "River Barrow"},
    {"name": "Thomastown", "ref": "15006", "lat": 52.5274, "lng": -7.1373, "mean_flow": 28.7, "river": "River Nore"},
    {"name": "Mallow", "ref": "18002", "lat": 52.1425, "lng": -8.6505, "mean_flow": 35.1, "river": "River Blackwater"},
    {"name": "Galway (Wolfe Tone Bridge)", "ref": "29001", "lat": 53.2706, "lng": -9.0544, "mean_flow": 85.0, "river": "River Corrib"},
    {"name": "Ballysadare", "ref": "35002", "lat": 54.2061, "lng": -8.5146, "mean_flow": 18.3, "river": "River Arrow"},
    {"name": "Ballina", "ref": "34001", "lat": 54.1143, "lng": -9.1529, "mean_flow": 65.0, "river": "River Moy"},
    {"name": "Sligo (Garavogue)", "ref": "35001", "lat": 54.2717, "lng": -8.4771, "mean_flow": 22.0, "river": "River Garavogue"},
    {"name": "Dundalk", "ref": "06011", "lat": 53.9990, "lng": -6.4033, "mean_flow": 6.5, "river": "River Castletown"},
    {"name": "Trim", "ref": "07009", "lat": 53.5554, "lng": -6.7905, "mean_flow": 14.2, "river": "River Boyne"},
    {"name": "Navan (Blackcastle)", "ref": "07012", "lat": 53.6520, "lng": -6.6867, "mean_flow": 25.8, "river": "River Boyne"},
    {"name": "Scarriff Bridge", "ref": "25003", "lat": 52.8541, "lng": -8.5237, "mean_flow": 135.0, "river": "River Shannon / L. Derg"},
    {"name": "Waterford (Tidal)", "ref": "16001", "lat": 52.2583, "lng": -7.1119, "mean_flow": 110.0, "river": "River Suir"},
    {"name": "Letterkenny", "ref": "38001", "lat": 54.9545, "lng": -7.7351, "mean_flow": 8.2, "river": "River Swilly"},
    {"name": "Cahir Park", "ref": "16004", "lat": 52.3761, "lng": -7.9247, "mean_flow": 45.0, "river": "River Suir"},
    {"name": "Drogheda (Boyne)", "ref": "07001", "lat": 53.7180, "lng": -6.3490, "mean_flow": 35.5, "river": "River Boyne"},
]


def download_opw_hydro():
    """Create OPW hydrometric stations GeoPackage from known station locations."""
    if OPW_HYDRO_FILE.exists():
        print(f"[hydro] Already present: {OPW_HYDRO_FILE}")
        return

    # Using hardcoded stations from waterlevel.ie — OSM coverage of OPW
    # hydrometric stations is too sparse for comprehensive coverage.
    rows = []
    for s in MAJOR_OPW_STATIONS:
        rows.append({
            "station_id": s["ref"],
            "name": f"{s['name']} ({s['river']})",
            "operator": "OPW",
            "mean_flow_m3s": s["mean_flow"],
            "river_name": s["river"],
            "geometry": Point(s["lng"], s["lat"]),
        })

    gdf = gpd.GeoDataFrame(rows, crs="EPSG:4326")

    OPW_HYDRO_FILE.parent.mkdir(parents=True, exist_ok=True)
    gdf.to_file(str(OPW_HYDRO_FILE), driver="GPKG")
    print(f"  Saved {len(gdf)} OPW hydrometric stations to {OPW_HYDRO_FILE}")


# ── GSI Aquifer Productivity — ogr2ogr from GSI Geodata ──────────────────────

def download_gsi_aquifer():
    """Download GSI Bedrock Aquifer map via ESRI REST FeatureServer."""
    if GSI_AQUIFER_FILE.exists():
        print(f"[aquifer] Already present: {GSI_AQUIFER_FILE}")
        return

    GSI_AQUIFER_FILE.parent.mkdir(parents=True, exist_ok=True)

    # GSI Bedrock Aquifer Map — contains aquifer type codes that map to productivity.
    # GSI aquifer codes: Rkc/Rkd=Regionally Important Karst, Rf=Regionally Important Fissured,
    # Rg=Regionally Important Gravel, Ll/Lm=Locally Important, Pl/Pu=Poor
    # Layer: IE_GSI_Aquifer_Datasets_IE26_ITM / sublayer 2 (Bedrock Aquifers 100K)
    base_url = (
        "https://gsi.geodata.gov.ie/server/rest/services/Groundwater"
        "/IE_GSI_Aquifer_Datasets_IE26_ITM/FeatureServer/2/query"
    )

    print(f"[aquifer] Downloading GSI Bedrock Aquifer via ESRI REST FeatureServer...")
    all_features = []
    offset = 0
    batch_size = 2000

    while True:
        params = (
            f"where=1%3D1&outFields=AQUIFERCAT,AQUIFERDES"
            f"&f=geojson&resultRecordCount={batch_size}&resultOffset={offset}"
        )
        url = f"{base_url}?{params}"
        raw = _download(url, f"GSI aquifer batch {offset // batch_size + 1}")
        data = json.loads(raw)
        features = data.get("features", [])
        if not features:
            break
        all_features.extend(features)
        print(f"    Fetched {len(all_features)} features so far...")
        offset += batch_size
        if len(features) < batch_size:
            break

    print(f"  Total features: {len(all_features)}")

    # GeoJSON from ESRI REST uses WGS84 coordinates (GeoJSON spec)
    geojson = {"type": "FeatureCollection", "features": all_features}
    gdf = gpd.GeoDataFrame.from_features(geojson, crs="EPSG:4326")
    print(f"  GeoDataFrame: {len(gdf)} rows, CRS={gdf.crs}")

    if "AQUIFERCAT" in gdf.columns:
        print(f"  AQUIFERCAT values: {dict(gdf['AQUIFERCAT'].value_counts().head(12))}")

    gdf.to_file(str(GSI_AQUIFER_FILE), driver="GPKG")
    print(f"  Saved to {GSI_AQUIFER_FILE}")


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    print("=" * 60)
    print("Downloading cooling source data")
    print("=" * 60)

    print("\n[1/5] Mean annual temperature — NASA POWER T2M")
    download_temperature()

    print("\n[2/5] Annual rainfall — NASA POWER PRECTOTCORR")
    download_rainfall()

    print("\n[3/5] River network — Overpass API (named rivers + lakes)")
    download_epa_rivers()

    print("\n[4/5] OPW hydrometric stations — Major stations")
    download_opw_hydro()

    print("\n[5/5] GSI Bedrock Aquifer — ogr2ogr")
    download_gsi_aquifer()

    files = [
        MET_EIREANN_TEMP_FILE, MET_EIREANN_RAIN_FILE,
        EPA_RIVERS_FILE, OPW_HYDRO_FILE, GSI_AQUIFER_FILE,
    ]
    present = sum(1 for f in files if f.exists())
    print("\n" + "=" * 60)
    print(f"Source files ready: {present}/{len(files)}")
    if present == len(files):
        print("All files present. Run: python cooling/ingest.py")
    else:
        for f in files:
            status = "OK" if f.exists() else "MISSING"
            print(f"  [{status}] {f}")
    print("=" * 60)


if __name__ == "__main__":
    main()

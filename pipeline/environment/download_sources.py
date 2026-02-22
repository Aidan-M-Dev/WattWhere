"""
FILE: pipeline/environment/download_sources.py
Role: Download real source data for the environment pipeline.

Sources (all verified working URLs as of 2026-02):
  NPWS SAC boundary:  npws.ie — SAC_ITM_2026_01.zip (34 MB), CC BY 4.0
  NPWS SPA boundary:  npws.ie — SPA_ITM_2026_02.zip (10 MB), CC BY 4.0
  NPWS NHA boundary:  npws.ie — NHA_ITM_2019_06.zip (2 MB) + pNHA_ITM_2015_11.zip (20 MB), CC BY 4.0
  OPW NIFM Current:   OPW S3 — nifm_ext_f_c.zip, CC BY-NC-ND (non-commercial only)
  OPW NIFM Future:    OPW S3 — nifm_ext_f_m.zip (Mid-Range +20% rainfall), CC BY-NC-ND
  GSI Landslide:      ArcGIS REST — IE_GSI_Landslide_Susceptibility_Classification_50K
                      Key field: LSSUSCLASS, EPSG:2157

Run: python environment/download_sources.py
     (saves to /data/environment/ — re-run is idempotent, skips existing files)
"""

import sys
import io
import shutil
import zipfile
import urllib.request
from pathlib import Path

import geopandas as gpd
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent.parent))
from config import (
    NPWS_SAC_FILE, NPWS_SPA_FILE, NPWS_NHA_FILE,
    OPW_FLOOD_CURRENT_FILE, OPW_FLOOD_FUTURE_FILE, GSI_LANDSLIDE_FILE,
)

# ── Verified direct download URLs ────────────────────────────────────────────────

NPWS_BASE = "https://www.npws.ie/sites/default/files/files"
OPW_S3    = "https://s3.eu-west-1.amazonaws.com/catalogue.floodinfo.opw/nifm"
GSI_REST  = (
    "https://gsi.geodata.gov.ie/server/rest/services/Geohazards"
    "/IE_GSI_Landslide_Susceptibility_Classification_50K_IE26_ITM/MapServer/0"
)

URLS = {
    "sac":           f"{NPWS_BASE}/SAC_ITM_2026_01.zip",
    "spa":           f"{NPWS_BASE}/SPA_ITM_2026_02.zip",
    "nha":           f"{NPWS_BASE}/NHA_ITM_2019_06(2).zip",
    "pnha":          f"{NPWS_BASE}/pNHA_ITM_2015_11.zip",
    "flood_current": f"{OPW_S3}/nifm_ext_f_c.zip",
    "flood_future":  f"{OPW_S3}/nifm_ext_f_m.zip",
}


# ── Helpers ─────────────────────────────────────────────────────────────────────

def _get(url: str, timeout: int = 300) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "HackEurope-pipeline/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        total = int(resp.headers.get("Content-Length", 0))
        data = b""
        chunk = 65536
        while True:
            block = resp.read(chunk)
            if not block:
                break
            data += block
            if total:
                pct = 100 * len(data) / total
                print(f"\r    {len(data)//1_048_576} / {total//1_048_576} MB ({pct:.0f}%)  ", end="", flush=True)
    print()
    return data


def _zip_to_gpkg(data: bytes, out_path: Path) -> gpd.GeoDataFrame:
    """Extract the first .shp from a ZIP and return as a GeoDataFrame."""
    extract_dir = out_path.parent / "_tmp_extract"
    extract_dir.mkdir(parents=True, exist_ok=True)
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            shp_names = [n for n in zf.namelist() if n.lower().endswith(".shp")]
            if not shp_names:
                raise RuntimeError("No .shp file found in ZIP")
            zf.extractall(extract_dir)

        shp_file = extract_dir / shp_names[0]
        gdf = gpd.read_file(str(shp_file))
        return gdf
    finally:
        shutil.rmtree(extract_dir, ignore_errors=True)



# ── Individual dataset download functions ────────────────────────────────────────

def download_npws_sac() -> None:
    if NPWS_SAC_FILE.exists():
        print(f"[sac] Already present: {NPWS_SAC_FILE}")
        return
    print(f"[sac] Downloading NPWS SAC boundaries...")
    data = _get(URLS["sac"])
    gdf = _zip_to_gpkg(data, NPWS_SAC_FILE)
    print(f"    {len(gdf)} SAC polygons  CRS={gdf.crs}")
    NPWS_SAC_FILE.parent.mkdir(parents=True, exist_ok=True)
    gdf.to_file(str(NPWS_SAC_FILE), driver="GPKG")
    print(f"    Saved to {NPWS_SAC_FILE}")


def download_npws_spa() -> None:
    if NPWS_SPA_FILE.exists():
        print(f"[spa] Already present: {NPWS_SPA_FILE}")
        return
    print(f"[spa] Downloading NPWS SPA boundaries...")
    data = _get(URLS["spa"])
    gdf = _zip_to_gpkg(data, NPWS_SPA_FILE)
    print(f"    {len(gdf)} SPA polygons  CRS={gdf.crs}")
    NPWS_SPA_FILE.parent.mkdir(parents=True, exist_ok=True)
    gdf.to_file(str(NPWS_SPA_FILE), driver="GPKG")
    print(f"    Saved to {NPWS_SPA_FILE}")


def download_npws_nha() -> None:
    """Download NHA and pNHA, merge them into a single GeoPackage with SITE_TYPE column."""
    if NPWS_NHA_FILE.exists():
        print(f"[nha] Already present: {NPWS_NHA_FILE}")
        return

    print(f"[nha] Downloading NPWS NHA boundaries...")
    nha_data = _get(URLS["nha"])
    nha_gdf = _zip_to_gpkg(nha_data, NPWS_NHA_FILE)
    nha_gdf["SITE_TYPE"] = "NHA"
    print(f"    {len(nha_gdf)} NHA polygons  CRS={nha_gdf.crs}")

    print(f"[nha] Downloading NPWS pNHA boundaries...")
    pnha_data = _get(URLS["pnha"])
    pnha_gdf = _zip_to_gpkg(pnha_data, NPWS_NHA_FILE)
    pnha_gdf["SITE_TYPE"] = "pNHA"
    print(f"    {len(pnha_gdf)} pNHA polygons  CRS={pnha_gdf.crs}")

    # Align columns before concat
    all_cols = list(dict.fromkeys(list(nha_gdf.columns) + list(pnha_gdf.columns)))
    for col in all_cols:
        if col not in nha_gdf.columns:
            nha_gdf[col] = None
        if col not in pnha_gdf.columns:
            pnha_gdf[col] = None

    combined = gpd.GeoDataFrame(
        pd.concat([nha_gdf, pnha_gdf], ignore_index=True),
        crs=nha_gdf.crs,
    )
    print(f"    Combined: {len(combined)} NHA+pNHA polygons")
    NPWS_NHA_FILE.parent.mkdir(parents=True, exist_ok=True)
    combined.to_file(str(NPWS_NHA_FILE), driver="GPKG")
    print(f"    Saved to {NPWS_NHA_FILE}")


def download_opw_flood_current() -> None:
    # OPW NIFM data: CC BY-NC-ND licence — non-commercial use only.
    # UI licence notice is displayed in SidebarEnvironment.vue.
    if OPW_FLOOD_CURRENT_FILE.exists():
        print(f"[flood_current] Already present: {OPW_FLOOD_CURRENT_FILE}")
        return
    print(f"[flood_current] Downloading OPW NIFM current flood extents (CC BY-NC-ND)...")
    data = _get(URLS["flood_current"])
    gdf = _zip_to_gpkg(data, OPW_FLOOD_CURRENT_FILE)
    print(f"    {len(gdf)} current flood polygons  CRS={gdf.crs}")
    OPW_FLOOD_CURRENT_FILE.parent.mkdir(parents=True, exist_ok=True)
    gdf.to_file(str(OPW_FLOOD_CURRENT_FILE), driver="GPKG")
    print(f"    Saved to {OPW_FLOOD_CURRENT_FILE}")


def download_opw_flood_future() -> None:
    # OPW NIFM data: CC BY-NC-ND licence — non-commercial use only.
    # Mid-Range Future Scenario (+20% rainfall).
    if OPW_FLOOD_FUTURE_FILE.exists():
        print(f"[flood_future] Already present: {OPW_FLOOD_FUTURE_FILE}")
        return
    print(f"[flood_future] Downloading OPW NIFM future flood extents (CC BY-NC-ND)...")
    data = _get(URLS["flood_future"])
    gdf = _zip_to_gpkg(data, OPW_FLOOD_FUTURE_FILE)
    print(f"    {len(gdf)} future flood polygons  CRS={gdf.crs}")
    OPW_FLOOD_FUTURE_FILE.parent.mkdir(parents=True, exist_ok=True)
    gdf.to_file(str(OPW_FLOOD_FUTURE_FILE), driver="GPKG")
    print(f"    Saved to {OPW_FLOOD_FUTURE_FILE}")


def download_gsi_landslide() -> None:
    """
    Download GSI Landslide Susceptibility via ogr2ogr using GDAL /vsizip//vsicurl/.
    The ZIP uses a compression method Python's zipfile cannot handle; GDAL handles it fine.
    Key field: LSSUSCLASS (1=Low, 2=Medium, 3=High). Source: gsi.geodata.gov.ie
    Layer: IE_GSI_Landslide_Susceptibility_Classification_50K_IE26_ITM
    """
    import subprocess
    if GSI_LANDSLIDE_FILE.exists():
        print(f"[landslide] Already present: {GSI_LANDSLIDE_FILE}")
        return
    GSI_LANDSLIDE_FILE.parent.mkdir(parents=True, exist_ok=True)
    print(f"[landslide] Downloading GSI Landslide Susceptibility via ogr2ogr /vsizip//vsicurl/...")
    zip_url = "https://gsi.geodata.gov.ie/downloads/Geohazards/Data/IE_GSI_Landslide_Data_IE32_ITM.zip"
    layer = "IE_GSI_Landslide_Susceptibility_Classification_50K_IE26_ITM"
    result = subprocess.run(
        [
            "ogr2ogr",
            "-f", "GPKG",
            str(GSI_LANDSLIDE_FILE),
            f"/vsizip//vsicurl/{zip_url}",
            layer,
            "-select", "LSSUSCLASS,LSSUSDESC",
            "-progress",
        ],
        capture_output=False,
        timeout=1800,  # 30 min max
    )
    if result.returncode != 0:
        raise RuntimeError(f"ogr2ogr failed (exit {result.returncode})")
    gdf = gpd.read_file(str(GSI_LANDSLIDE_FILE))
    print(f"    {len(gdf)} landslide polygons  CRS={gdf.crs}")
    if "LSSUSCLASS" in gdf.columns:
        print(f"    LSSUSCLASS values: {gdf['LSSUSCLASS'].value_counts().to_dict()}")
    print(f"    Saved to {GSI_LANDSLIDE_FILE}")


# ── Main ─────────────────────────────────────────────────────────────────────────

def main() -> None:
    print("=" * 60)
    print("Downloading environment source data")
    print("=" * 60)

    print("\n[1/6] NPWS SAC (Special Areas of Conservation)")
    download_npws_sac()

    print("\n[2/6] NPWS SPA (Special Protection Areas)")
    download_npws_spa()

    print("\n[3/6] NPWS NHA + pNHA (Natural Heritage Areas)")
    download_npws_nha()

    print("\n[4/6] OPW NIFM Current Flood Extents  [CC BY-NC-ND]")
    download_opw_flood_current()

    print("\n[5/6] OPW NIFM Future Flood Extents  [CC BY-NC-ND]")
    download_opw_flood_future()

    print("\n[6/6] GSI Landslide Susceptibility")
    download_gsi_landslide()

    files = [
        NPWS_SAC_FILE, NPWS_SPA_FILE, NPWS_NHA_FILE,
        OPW_FLOOD_CURRENT_FILE, OPW_FLOOD_FUTURE_FILE, GSI_LANDSLIDE_FILE,
    ]
    present = sum(1 for f in files if f.exists())
    print("\n" + "=" * 60)
    print(f"Source files ready: {present}/{len(files)}")
    if present == len(files):
        print("All files present. Run: python environment/ingest.py")
    else:
        for f in files:
            status = "OK" if f.exists() else "MISSING"
            print(f"  [{status}] {f}")
    print("=" * 60)


if __name__ == "__main__":
    main()

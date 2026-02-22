"""
FILE: pipeline/grid/download_boundaries.py
Role: Download Ireland national boundary and county boundary files from GADM.
      Saves to /data/grid/ so generate_grid.py can use them (Option A).

Data source: GADM 4.1 (gadm.org) — CC BY 4.0 for non-commercial use.
  IRL_0 = national boundary (Republic of Ireland)
  IRL_1 = county boundaries (26 counties of the Republic)

Run: python grid/download_boundaries.py
"""

import sys
import io
import zipfile
import urllib.request
from pathlib import Path

import geopandas as gpd

sys.path.insert(0, str(Path(__file__).parent.parent))
from config import IRELAND_BOUNDARY_FILE, IRELAND_COUNTIES_FILE

GADM_BASE = "https://geodata.ucdavis.edu/gadm/gadm4.1/json"
GADM_IRL_0 = f"{GADM_BASE}/gadm41_IRL_0.json.zip"  # national boundary
GADM_IRL_1 = f"{GADM_BASE}/gadm41_IRL_1.json.zip"  # county boundaries


# GADM 4.1 data quality: Cork's NAME_1 is stored as 'NA'.
# Map ISO_1 codes to correct names for any affected counties.
_GADM_ISO_FIXES: dict[str, str] = {
    "IE-CO": "Cork",
}


def _fix_gadm_county_names(gdf: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """Patch GADM NAME_1 values that read as 'NA' using ISO_1 lookup."""
    if "NAME_1" not in gdf.columns or "ISO_1" not in gdf.columns:
        return gdf
    gdf = gdf.copy()
    bad_mask = gdf["NAME_1"].astype(str) == "NA"
    if bad_mask.any():
        for idx in gdf.index[bad_mask]:
            iso = str(gdf.at[idx, "ISO_1"])
            if iso in _GADM_ISO_FIXES:
                gdf.at[idx, "NAME_1"] = _GADM_ISO_FIXES[iso]
                print(f"  Patched NAME_1: '{iso}' → '{_GADM_ISO_FIXES[iso]}'")
    return gdf


def _download_gadm(url: str, dest: Path) -> gpd.GeoDataFrame:
    """Download a GADM GeoJSON zip, parse it, save as GeoPackage."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    print(f"  Downloading {url} ...")
    with urllib.request.urlopen(url, timeout=60) as resp:
        data = resp.read()
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        json_name = next(n for n in zf.namelist() if n.endswith(".json"))
        with zf.open(json_name) as f:
            gdf = gpd.read_file(f)
    gdf = _fix_gadm_county_names(gdf)
    gdf.to_file(dest, driver="GPKG")
    print(f"  Saved to {dest} ({len(gdf)} feature(s))")
    return gdf


def main():
    print("=" * 60)
    print("Downloading Ireland boundary files from GADM 4.1")
    print("=" * 60)

    if IRELAND_BOUNDARY_FILE.exists():
        print(f"\n[1/2] National boundary already present: {IRELAND_BOUNDARY_FILE}")
    else:
        print("\n[1/2] Downloading national boundary (IRL_0)...")
        _download_gadm(GADM_IRL_0, IRELAND_BOUNDARY_FILE)

    if IRELAND_COUNTIES_FILE.exists():
        print(f"\n[2/2] County boundaries already present: {IRELAND_COUNTIES_FILE}")
    else:
        print("\n[2/2] Downloading county boundaries (IRL_1)...")
        gdf = _download_gadm(GADM_IRL_1, IRELAND_COUNTIES_FILE)
        # Report county name column and names found
        name_col_candidates = [c for c in gdf.columns
                                if "county" in c.lower() or c in ("NAME", "NAME_1")]
        if name_col_candidates:
            name_col = name_col_candidates[0]
            print(f"  County name column: '{name_col}'")
            print(f"  Counties: {sorted(gdf[name_col].tolist())}")

    print("\nDone. Run generate_grid.py next.")


if __name__ == "__main__":
    main()

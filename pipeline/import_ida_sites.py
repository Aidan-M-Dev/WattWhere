"""
FILE: pipeline/import_ida_sites.py
Role: Import IDA Ireland industrial site locations from CSV into ida_sites table.
      Safe to re-run — uses TRUNCATE + INSERT (replaces all rows each run).
Data: Manually geocoded from IDA Ireland website (idaireland.com).
      No GIS download exists (ARCHITECTURE.md §11 D3).
      Review CSV periodically — IDA site portfolio changes.
Run:  python import_ida_sites.py
"""
import csv
import sys
from pathlib import Path

import sqlalchemy
from sqlalchemy import text

sys.path.insert(0, str(Path(__file__).parent))
from config import DB_URL

IDA_CSV = Path(__file__).parent / "data" / "ida_sites.csv"


def import_ida_sites(engine: sqlalchemy.Engine) -> int:
    with open(IDA_CSV, newline="") as f:
        rows = list(csv.DictReader(f))
    with engine.begin() as conn:
        conn.execute(text("TRUNCATE ida_sites CASCADE"))
        for row in rows:
            conn.execute(text("""
                INSERT INTO ida_sites (name, county, geom, site_type, address)
                VALUES (
                    :name, :county,
                    ST_SetSRID(ST_MakePoint(CAST(:lng AS float), CAST(:lat AS float)), 4326),
                    :site_type, :address
                )
            """), row)
        # Assign tile_id for sites that fall within a tile
        conn.execute(text("""
            UPDATE ida_sites i
            SET tile_id = (
                SELECT tile_id FROM tiles t
                WHERE ST_Within(i.geom, t.geom)
                LIMIT 1
            )
            WHERE tile_id IS NULL
        """))
    return len(rows)


if __name__ == "__main__":
    engine = sqlalchemy.create_engine(DB_URL)
    n = import_ida_sites(engine)
    print(f"Imported {n} IDA sites")
    # Re-run planning/ingest.py and overall/compute_composite.py to update nearest_ida_site_km
    # Then restart Martin: docker compose restart martin

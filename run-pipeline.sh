#!/usr/bin/env bash
set -euo pipefail

# ── Development pipeline runner ──────────────────────────────────────────────
# Automates: DB schema → grid → downloads → all sort pipelines → composite → Martin restart
#
# Usage:
#   ./run-pipeline.sh              # full run (everything)
#   ./run-pipeline.sh --skip-download  # skip source data downloads (already have files)
#   ./run-pipeline.sh --skip-schema    # skip DB schema + grid (tiles already exist)
#   ./run-pipeline.sh --only energy    # run only energy download + ingest
#   ./run-pipeline.sh --only cooling --skip-download  # re-run cooling ingest only
#   ./run-pipeline.sh --from ingest    # skip schema + grid + downloads, run all ingests

PIPELINE="docker compose --profile pipeline run --rm pipeline"
DB_EXEC="docker compose exec -T db psql -U hackeurope -d hackeurope"

SKIP_DOWNLOAD=false
SKIP_SCHEMA=false
ONLY=""
FROM=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --skip-download) SKIP_DOWNLOAD=true; shift ;;
    --skip-schema)   SKIP_SCHEMA=true; shift ;;
    --only)          ONLY="$2"; shift 2 ;;
    --from)          FROM="$2"; shift 2 ;;
    -h|--help)
      echo "Usage: ./run-pipeline.sh [OPTIONS]"
      echo ""
      echo "Options:"
      echo "  --skip-download   Skip downloading source data files"
      echo "  --skip-schema     Skip DB schema application and grid generation"
      echo "  --only SORT       Run only one sort pipeline (energy|environment|cooling|connectivity|planning)"
      echo "  --from STAGE      Start from stage: schema|grid|download|ingest|composite"
      echo "  -h, --help        Show this help"
      exit 0 ;;
    *) echo "Unknown option: $1"; exit 1 ;;
  esac
done

# If --from is set, derive skip flags
case "$FROM" in
  grid)      SKIP_SCHEMA=false ;;
  download)  SKIP_SCHEMA=true ;;
  ingest)    SKIP_SCHEMA=true; SKIP_DOWNLOAD=true ;;
  composite) SKIP_SCHEMA=true; SKIP_DOWNLOAD=true; ONLY="__composite_only__" ;;
  "") ;;  # no --from
  *) echo "Unknown --from stage: $FROM (use: schema|grid|download|ingest|composite)"; exit 1 ;;
esac

SORTS=(energy environment cooling connectivity planning)
SORTS_WITH_DOWNLOAD=(energy environment cooling)

elapsed() { echo "  (${SECONDS}s elapsed)"; }

echo "============================================================"
echo " HackEurope Pipeline Runner"
echo "============================================================"
echo ""
SECONDS=0

# ── 1. Ensure stack is running ────────────────────────────────────────────────
echo "[0] Checking Docker stack..."
if ! docker compose ps --status running | grep -q db; then
  echo "  Starting Docker stack..."
  docker compose up -d
  echo "  Waiting for DB to be healthy..."
  sleep 5
fi
echo "  Stack is running."
elapsed

# ── 2. Apply DB schema ───────────────────────────────────────────────────────
if [[ "$SKIP_SCHEMA" == false && "$ONLY" == "" ]]; then
  echo ""
  echo "[1] Applying DB schema..."
  $DB_EXEC -f /docker-entrypoint-initdb.d/tables.sql 2>&1 | tail -3
  $DB_EXEC -f /docker-entrypoint-initdb.d/indexes.sql 2>&1 | tail -3
  $DB_EXEC -f /docker-entrypoint-initdb.d/functions.sql 2>&1 | tail -3
  echo "  Schema applied."
  elapsed

  # ── 3. Generate grid ──────────────────────────────────────────────────────
  echo ""
  echo "[2] Downloading boundaries + generating grid..."
  $PIPELINE python grid/download_boundaries.py
  $PIPELINE python grid/generate_grid.py
  TILE_COUNT=$($DB_EXEC -t -c "SELECT COUNT(*) FROM tiles;" | tr -d ' ')
  echo "  Grid: $TILE_COUNT tiles"
  elapsed
else
  echo ""
  echo "[1-2] Skipping schema + grid (--skip-schema or --only)"
fi

# ── 4. Download source data ──────────────────────────────────────────────────
if [[ "$SKIP_DOWNLOAD" == false && "$ONLY" != "__composite_only__" ]]; then
  echo ""
  echo "[3] Downloading source data..."

  if [[ -n "$ONLY" && "$ONLY" != "__composite_only__" ]]; then
    # Download for one sort only
    case "$ONLY" in
      energy|environment|cooling)
        echo "  Downloading $ONLY sources..."
        $PIPELINE python "$ONLY/download_sources.py"
        ;;
      connectivity|planning)
        echo "  No download script for $ONLY (uses config.py constants or manual data)"
        ;;
    esac
  else
    for sort in "${SORTS_WITH_DOWNLOAD[@]}"; do
      echo ""
      echo "  ── $sort ──"
      $PIPELINE python "$sort/download_sources.py"
    done
  fi
  elapsed
else
  echo ""
  echo "[3] Skipping downloads"
fi

# ── 5. Run sort pipelines ────────────────────────────────────────────────────
if [[ "$ONLY" != "__composite_only__" ]]; then
  echo ""
  echo "[4] Running sort pipelines..."

  if [[ -n "$ONLY" ]]; then
    # Single sort
    echo ""
    echo "  ── $ONLY ingest ──"
    $PIPELINE python "$ONLY/ingest.py"
  else
    for sort in "${SORTS[@]}"; do
      echo ""
      echo "  ══════════════════════════════════════════"
      echo "  ── $sort ingest ──"
      echo "  ══════════════════════════════════════════"
      if $PIPELINE python "$sort/ingest.py"; then
        echo "  $sort: OK"
      else
        echo "  $sort: FAILED (continuing with next sort)"
      fi
    done
  fi
  elapsed
fi

# ── 6. Overall composite ─────────────────────────────────────────────────────
echo ""
echo "[5] Running overall composite..."
if $PIPELINE python overall/compute_composite.py; then
  echo "  Overall composite: OK"
else
  echo "  Overall composite: FAILED (may need all 5 sort tables populated)"
fi
elapsed

# ── 7. Restart Martin ────────────────────────────────────────────────────────
echo ""
echo "[6] Restarting Martin tile server..."
docker compose restart martin
echo "  Martin restarted."

# ── 8. Summary ────────────────────────────────────────────────────────────────
echo ""
echo "============================================================"
echo " Pipeline complete (${SECONDS}s total)"
echo "============================================================"
echo ""
echo "Quick checks:"
$DB_EXEC -c "
  SELECT 'tiles' AS tbl, COUNT(*) FROM tiles
  UNION ALL SELECT 'energy_scores', COUNT(*) FROM energy_scores
  UNION ALL SELECT 'environment_scores', COUNT(*) FROM environment_scores
  UNION ALL SELECT 'cooling_scores', COUNT(*) FROM cooling_scores
  UNION ALL SELECT 'connectivity_scores', COUNT(*) FROM connectivity_scores
  UNION ALL SELECT 'planning_scores', COUNT(*) FROM planning_scores
  UNION ALL SELECT 'overall_scores', COUNT(*) FROM overall_scores
  ORDER BY 1;
" 2>/dev/null || echo "  (some tables may not exist yet)"
echo ""
echo "Metric ranges:"
$DB_EXEC -c "SELECT sort, metric, min_val, max_val, unit FROM metric_ranges ORDER BY sort, metric;" 2>/dev/null || true

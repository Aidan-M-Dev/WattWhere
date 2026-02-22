# WattWhere

A geospatial decision-support tool for scoring and ranking locations across Ireland for data centre suitability. WattWhere renders an interactive choropleth map of Ireland (~14,000 grid tiles, each ~5 km²), scored across six categories: **Overall**, **Energy**, **Environment**, **Cooling**, **Connectivity**, and **Planning**. Users can click tiles to see detailed metrics, adjust scoring weights, and get AI-generated summaries.

## Architecture

```text
                     ┌──────────┐
                     │  nginx   │ :80
                     └────┬─────┘
               ┌──────────┼──────────┐
               ▼          ▼          ▼
          ┌────────┐ ┌────────┐ ┌────────┐
          │Frontend│ │  API   │ │ Martin │
          │Vue 3   │ │FastAPI │ │ MVT    │
          │:5173   │ │:8000   │ │:3000   │
          └────────┘ └───┬────┘ └───┬────┘
                         │          │
                    ┌────┴──────────┴────┐
                    │   PostgreSQL 16    │
                    │   + PostGIS 3.4    │
                    │       :5432        │
                    └────────────────────┘
```

| Service      | Role                                                               |
| ------------ | ------------------------------------------------------------------ |
| **db**       | PostgreSQL + PostGIS — stores tile grid, scores, pins              |
| **martin**   | Martin tile server — serves MVT vector tiles via a SQL function    |
| **api**      | FastAPI — JSON API for sorts, tiles, weights, AI summaries         |
| **frontend** | Vue 3 + Vite + MapLibre GL — interactive map UI                    |
| **nginx**    | Reverse proxy — routes `/api/`, `/tiles/`, and `/`                 |
| **pipeline** | Python ETL — downloads and ingests geospatial data (run on demand) |

## Prerequisites

- [Docker Desktop](https://www.docker.com/products/docker-desktop/) (with Compose v2)
- Git
- Bash (Git Bash or WSL on Windows)
- An [Anthropic API key](https://console.anthropic.com/) (for the AI summary feature)

All services run inside Docker containers — no local Node.js or Python installation required.

## Quick Start

```bash
# 1. Clone the repository
git clone <repo-url>
cd HackEurope-Project

# 2. Configure environment variables
cp .env.example .env
# Edit .env and fill in:
#   DB_PASSWORD      — a strong password for PostgreSQL
#   ADMIN_KEY        — secret key for admin API endpoints
#   ANTHROPIC_API_KEY — your Anthropic API key (sk-ant-...)

# 3. Start all services
docker compose up --build -d

# 4. Verify everything is running
docker compose ps
curl http://localhost/api/sorts     # should return JSON with 6 sort categories
```

The app is accessible at `http://localhost`. After first boot the database has the schema but no tile data — see [Data Pipeline](#data-pipeline) below to populate it.

## Environment Variables

Copy `.env.example` to `.env` before starting. Required values:

| Variable             | Description                                                                       |
| -------------------- | --------------------------------------------------------------------------------- |
| `DB_PASSWORD`        | PostgreSQL password (used by all services)                                        |
| `ADMIN_KEY`          | Secret for admin endpoints (`PUT /api/weights`, `POST /api/admin/restart-martin`) |
| `ANTHROPIC_API_KEY`  | Anthropic API key for the AI summary endpoint                                     |
| `MARTIN_PORT`        | Martin tile server host port (default: `3000`)                                    |
| `API_PORT`           | FastAPI host port (default: `8000`)                                               |

## Database

PostgreSQL 16 + PostGIS 3.4 initialises automatically on first startup. Three SQL files are applied in order:

1. `sql/tables.sql` — all tables (tiles, score tables, pins, weights, etc.)
2. `sql/indexes.sql` — spatial GIST indexes and regular indexes
3. `sql/functions.sql` — the `tile_heatmap` function used by Martin for MVT tiles

To reset the database completely:

```bash
docker compose down -v    # destroys the data volume
docker compose up -d      # re-creates and re-initialises the schema
```

## Data Pipeline

The pipeline downloads Irish geospatial data, processes it, and writes scores to the database. It runs inside Docker with a full GDAL/GEOS stack.

### Full Pipeline Run

```bash
./run-pipeline.sh
```

This runs the entire pipeline end-to-end:

1. Apply DB schema
2. Download Ireland boundary and generate the ~14,000 tile grid
3. Download source data for all 5 categories (in parallel)
4. Run all 5 ingest pipelines (energy, environment, cooling, connectivity, planning)
5. Compute the overall composite scores
6. Restart Martin to serve the new tiles

### Pipeline Options

```bash
./run-pipeline.sh --skip-download       # skip downloads (already have source files)
./run-pipeline.sh --skip-schema         # skip schema + grid (tiles already exist)
./run-pipeline.sh --only energy         # run only one category
./run-pipeline.sh --from ingest         # skip to ingest stage
./run-pipeline.sh --from composite      # re-compute overall scores only
./run-pipeline.sh --serial              # disable parallelism (lower resource usage)
```

### Synthetic Data (for Development)

If you don't have access to the real geospatial source data, you can seed the database with synthetic data for development:

```bash
# Generate the tile grid (requires Ireland boundary download)
docker compose --profile pipeline run --rm pipeline python grid/download_boundaries.py
docker compose --profile pipeline run --rm pipeline python grid/generate_grid.py

# Seed all score tables with synthetic data
docker compose --profile pipeline run --rm pipeline python seed_synthetic.py

# Restart Martin to pick up the new data
docker compose restart martin
```

### Pipeline Categories

Each category has a `download_sources.py` (fetches data) and an `ingest.py` (processes into scores):

| Category         | Data Sources                                                         | Scores Table          |
| ---------------- | -------------------------------------------------------------------- | --------------------- |
| **Energy**       | Wind atlas, solar atlas, OSM power grid, SEAI wind farms             | `energy_scores`       |
| **Environment**  | NPWS protected areas, OPW flood zones, GSI landslide data            | `environment_scores`  |
| **Cooling**      | Met Eireann temp/rainfall, EPA rivers, OPW hydrometric, GSI aquifers | `cooling_scores`      |
| **Connectivity** | ComReg broadband, OSM roads, INEX IXP locations                      | `connectivity_scores` |
| **Planning**     | MyPlan zoning, planning apps, CSO population, PPR land prices        | `planning_scores`     |
| **Overall**      | Weighted composite of the above 5 categories                         | `overall_scores`      |

Source data files are stored in a Docker volume (`pipeline_data`) and are not committed to the repo.

## API Endpoints

| Method | Endpoint                                         | Description                                              |
| ------ | ------------------------------------------------ | -------------------------------------------------------- |
| `GET`  | `/api/sorts`                                     | List all 6 sort categories with their metrics            |
| `GET`  | `/api/tile/{tile_id}?sort={sort}`                | Detailed metrics for a single tile                       |
| `GET`  | `/api/pins?sort={sort}`                          | GeoJSON pin features for a sort category                 |
| `GET`  | `/api/metric-range?sort={sort}&metric={metric}`  | Min/max values for colour normalisation                  |
| `GET`  | `/api/weights`                                   | Current composite weights                                |
| `PUT`  | `/api/weights`                                   | Update composite weights (requires `X-Admin-Key` header) |
| `GET`  | `/api/summary`                                   | AI-generated summary (uses Anthropic API)                |

## Frontend

Vue 3 + TypeScript with Vite, Pinia state management, and MapLibre GL for the map.

```bash
cd frontend
npm install
npm run dev          # Vite dev server on http://localhost:5173
npm run build        # production build
npm run type-check   # TypeScript check
npm run test         # Vitest unit tests
```

In Docker, the Vite dev server proxies `/api` to the API container and `/tiles` to Martin. Running the frontend standalone requires the API and Martin containers to be up.

## Running Tests

```bash
# Backend (API) tests
cd api
"/c/ProgramData/anaconda3/python.exe" -m pytest tests/

# Frontend tests
cd frontend
npm run test
```

## Verification Commands

```bash
# Check containers are running
docker compose ps

# Test the API
curl http://localhost/api/sorts

# Test tile serving
curl -o /dev/null -w "%{http_code}" \
  "http://localhost/tiles/tile_heatmap/6/31/21?sort=overall&metric=score"

# Check database row counts
docker compose exec db psql -U hackeurope -d hackeurope -c "
  SELECT 'tiles' AS tbl, COUNT(*) FROM tiles
  UNION ALL SELECT 'energy_scores', COUNT(*) FROM energy_scores
  UNION ALL SELECT 'environment_scores', COUNT(*) FROM environment_scores
  UNION ALL SELECT 'cooling_scores', COUNT(*) FROM cooling_scores
  UNION ALL SELECT 'connectivity_scores', COUNT(*) FROM connectivity_scores
  UNION ALL SELECT 'planning_scores', COUNT(*) FROM planning_scores
  UNION ALL SELECT 'overall_scores', COUNT(*) FROM overall_scores
  ORDER BY 1;"
```

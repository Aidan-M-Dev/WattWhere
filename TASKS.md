# HackEurope — Implementation Task Roadmap (Phase 2)

> **How to use this document**
> Phase 1 is complete — the stack runs, synthetic data is seeded, all API routes work,
> and the frontend renders Ireland with interactive choropleth tiles and sidebar panels.
> Each Phase 2 task can be handed verbatim to a separate Claude Code instance.
> They are sized for a single focused session. Dependencies are explicit.

---

## Dependency graph

```
[Phase 1 complete — all services healthy, synthetic data in DB]

Grid (tiles table populated)
    ├── P2-01 Energy pipeline      ─────┐
    ├── P2-02 Environment pipeline ─────┤
    ├── P2-03 Cooling pipeline     ─────┤──► P2-05 (overall/compute_composite.py, runs last)
    ├── P2-04 Connectivity pipeline─────┤
    └── P2-05 Planning pipeline    ─────┘

P2-06 Real grid boundary
    └── regenerates tiles → re-run P2-01 through P2-05 after

P2-07 API caching         (independent)
P2-08 /api/tile/{id}/all  (independent)

Frontend (all independent of pipeline):
    P2-09 Mobile layout
    P2-10 Pin icons + popups
    P2-11 Admin weights panel
    P2-12 Onboarding tooltip
    P2-13 Error boundary + offline
    P2-14 Map style polish
    P2-15 Figma design

Deployment (after frontend is polished):
    P2-16 Production Docker build
    P2-17 Martin cache invalidation
    P2-18 IDA sites data entry

Data & Metrics:
    P2-20 Real estate / land pricing   ──► integrates into Planning sort
    P2-22 Metric reorganisation         ──► moves metrics between sorts, re-run all pipelines
    P2-23 Renewable energy penetration  ──► new sub-metric in Energy sort

Frontend (requires pipeline data):
    P2-21 Custom combination builder    ──► depends on P2-08 (/api/tile/{id}/all)
```

P2-01 through P2-05 are independent of each other and can run in parallel once
the tiles table exists. `overall/compute_composite.py` (part of P2-05) runs last,
after ALL five sort pipelines complete.

---

## Phase 2 — Post-MVP Improvements

*Each task is self-contained and can be given to a separate agent session.*
*All pipeline tasks require the tiles table to be populated (Phase 1 complete).*

---

## Pipeline


---

## Frontend

---

### P2-10 · Pin Icons + Popups

**Goal**: Replace placeholder circle pins with sort-specific Lucide SVG icons loaded
as a MapLibre sprite. Clicking a pin opens a styled popup showing the pin's name,
type, and a key metric.

#### Dependencies

Add to `frontend/package.json` devDependencies:
```json
"@mapbox/spritezero-cli": "^7.0.0"
```
(CLI tool for generating MapLibre sprite sheets from SVG files.)

#### Read first
- `ARCHITECTURE.md` §6.2 (pin layer implementation notes — circle vs symbol)
- `frontend/src/components/MapView.vue` — `setupLayers()`, `pins-unclustered` layer
- `frontend/src/types/index.ts` — pin type strings used per sort

#### Deliverables

**1. Export Lucide SVGs for each pin type**

Required icons (from ARCHITECTURE.md §5.1–§5.6):
- Overall: `server`, `building`
- Energy: `wind`, `zap`, `circle-dot`
- Environment: `shield`, `bird`, `tree-pine`, `droplets`
- Cooling: `gauge`, `droplet`, `thermometer`
- Connectivity: `globe`, `circle-dot`, `wifi`
- Planning: `factory`, `file-text`

Copy the SVG source for each icon from the `lucide-static` package
(install: `npm install -D lucide-static`). Save each as
`frontend/public/sprites/svg/{type}.svg` where `{type}` matches the pin's
`type` property value (e.g. `data_centre.svg`, `wind_farm.svg`).

**2. Generate the sprite sheet**

```bash
cd frontend/public/sprites
npx @mapbox/spritezero-cli sprite svg/
# Outputs: sprite.json, sprite.png, sprite@2x.png
```

**3. Add sprite to the MapLibre style**

In `MapView.vue` `initMap()`, after fetching the base style, inject the sprite:
```typescript
const styleRes = await fetch(BASE_STYLE_URL)
const style = await styleRes.json()
style.sprite = `${window.location.origin}/sprites/sprite`
map = new maplibregl.Map({ style, center: [...], zoom: 6.5 })
```

If the base style already defines a sprite (Liberty/Positron do), use
`map.addImage()` after `map.on('load')` to add each custom icon individually —
this avoids overwriting the base style's icon set:
```typescript
const img = await map.loadImage(`/sprites/svg/${type}.svg`)
map.addImage(type, img.data)
```
Prefer `addImage()` — it is more reliable with third-party base styles.

**4. Replace `type: "circle"` with `type: "symbol"` for unclustered pins**

In `setupLayers()`:
```typescript
map.addLayer({
  id: 'pins-unclustered',
  type: 'symbol',
  source: 'pins',
  filter: ['!', ['has', 'point_count']],
  layout: {
    'icon-image': ['get', 'type'],   // must match addImage() name
    'icon-size': 0.75,
    'icon-allow-overlap': true,
    'icon-anchor': 'bottom',
  }
})
```

**5. Implement pin click popup**

```typescript
map.on('click', 'pins-unclustered', (e) => {
  const feat = e.features![0]
  const coords = (feat.geometry as GeoJSON.Point).coordinates as [number, number]
  const { name, type, ...props } = feat.properties!
  new maplibregl.Popup({ closeButton: true, maxWidth: '260px' })
    .setLngLat(coords)
    .setHTML(`
      <div class="pin-popup">
        <strong>${name ?? type.replace(/_/g, ' ')}</strong>
        <div class="pin-popup__type">${type.replace(/_/g, ' ')}</div>
        ${buildPopupExtra(type, props)}
      </div>
    `)
    .addTo(map)
})
map.on('mouseenter', 'pins-unclustered', () => { map.getCanvas().style.cursor = 'pointer' })
map.on('mouseleave', 'pins-unclustered', () => { map.getCanvas().style.cursor = '' })
```

Implement `buildPopupExtra(type, props)` returning type-specific HTML
(e.g. wind speed for `wind_farm`, voltage for `substation`, flow rate for `hydrometric_station`).

**6. Style the popup**

Add to `frontend/src/assets/main.css`:
```css
.maplibregl-popup-content { background: #1e2030; color: #e0e0e0; border-radius: 8px; }
.pin-popup strong        { font-size: 13px; display: block; margin-bottom: 4px; }
.pin-popup__type         { font-size: 11px; opacity: 0.7; text-transform: capitalize; }
```

#### Tests / acceptance criteria

```bash
# Sprites must be present
ls frontend/public/sprites/
# Expect: sprite.json  sprite.png  sprite@2x.png  svg/

# Manual visual test:
# 1. Zoom to level 8+ → pins render as icons, not filled circles
# 2. Hover over pin → cursor changes to pointer
# 3. Click pin → popup appears with name, type, and metric info
# 4. At zoom < 10 → cluster circles appear (unchanged from Phase 1)
# 5. Click cluster → map zooms in to expand it
```

---

### P2-12 · Onboarding Tooltip

**Goal**: First-time visitors see a brief introductory tooltip explaining the platform,
the 5 km² tile resolution limitation, and the data centre siting use case. The tooltip
is shown once per browser (localStorage flag) and is dismissible.

#### Dependencies

No new packages.

#### Read first
- `ARCHITECTURE.md` §11 D2 — tile resolution limitation (must be acknowledged in UI)
- `frontend/src/components/MapView.vue` — where tooltip renders above

#### Deliverables

**1. Create `frontend/src/components/OnboardingTooltip.vue`** (new file)

Show condition:
```typescript
const hasSeenTooltip = ref(localStorage.getItem('onboarding_seen') === 'true')
function dismiss() {
  localStorage.setItem('onboarding_seen', 'true')
  hasSeenTooltip.value = true
}
```

Content:
- Title: "Ireland Data Centre Suitability"
- Three bullet points:
  1. "Score ~14,000 grid tiles across 5 thematic sorts — switch sorts to compare factors."
  2. "Tiles are ~5 km² — designed for regional analysis, not site-level precision."
  3. "Click any tile to see a detailed breakdown in the sidebar."
- "Got it" dismiss button.

Design: semi-transparent dark overlay (`rgba(0,0,0,0.5)`) behind a centered card
(max-width 440px). Fade-in animation (200ms CSS transition on opacity).

**2. Mount in `App.vue`**

```html
<OnboardingTooltip />
<!-- Component handles its own show/hide via localStorage -->
```

#### Tests / acceptance criteria

```bash
# Manual test:
# 1. Clear localStorage (DevTools → Application → Storage → Clear All)
# 2. Reload http://localhost:5173
# 3. Onboarding tooltip appears on load
# 4. Click "Got it" → tooltip dismisses
# 5. Reload → tooltip does NOT appear again
# 6. DevTools → localStorage → 'onboarding_seen': 'true'
```

---

### P2-16 · Production Docker Build

**Goal**: Switch the frontend from Vite dev server to a compiled nginx production
build. Configure static asset caching headers. Verify the full production stack
works end-to-end via nginx reverse proxy.

#### Dependencies

No new packages. Uses the existing `prod` stage in `frontend/Dockerfile` and the
existing `nginx/nginx.conf`.

#### Read first
- `frontend/Dockerfile` — `dev`, `build`, `prod` stages
- `docker-compose.yml` — frontend service `target:` field and volume mounts
- `nginx/nginx.conf` — existing reverse proxy config
- `frontend/vite.config.ts` — chunk splitting, build output directory

#### Deliverables

**1. Switch `docker-compose.yml` frontend service to `prod` target**

```yaml
frontend:
  build:
    context: ./frontend
    target: prod          # was: dev
  # Remove the hot-reload volume mount (./frontend:/app) — not needed in prod
  # Remove ports: 5173 — nginx handles all public traffic on port 80
```

**2. Verify `frontend/Dockerfile` prod stage**

The prod stage should already exist. Confirm it:
1. Uses `node:22-alpine` to run `npm ci && npm run build` (outputs to `dist/`)
2. Copies `dist/` into an `nginx:alpine` image at `/usr/share/nginx/html`

If the prod stage is missing or incomplete:
```dockerfile
FROM node:22-alpine AS build
WORKDIR /app
COPY package*.json ./
RUN npm ci
COPY . .
RUN npm run build

FROM nginx:alpine AS prod
COPY --from=build /app/dist /usr/share/nginx/html
COPY nginx-prod.conf /etc/nginx/conf.d/default.conf
EXPOSE 80
```

**3. Create `frontend/nginx-prod.conf`**

```nginx
server {
  listen 80;
  root /usr/share/nginx/html;
  index index.html;

  # JS/CSS/fonts/images — hashed filenames, 1-year immutable cache
  location ~* \.(js|css|woff2?|png|jpg|svg|ico)$ {
    expires 1y;
    add_header Cache-Control "public, immutable";
  }

  # SPA fallback — all non-file routes serve index.html
  location / {
    try_files $uri $uri/ /index.html;
  }
}
```

**4. Verify top-level `nginx/nginx.conf` upstreams**

The nginx reverse proxy must reach the prod frontend container (port 80, not 5173):
```nginx
upstream frontend { server frontend:80; }  # was: frontend:5173
```
Verify the `api` and `martin` upstreams are still correct (port 8000, 3000).

**5. SSL via Let's Encrypt (for real deployment only)**

Only needed for public deployment, not local/demo. If required:
- Add a `certbot` service to `docker-compose.yml`
- Add HTTPS server block to `nginx/nginx.conf` with `ssl_certificate` paths
- Add HTTP → HTTPS redirect block

Skip for hackathon/demo environments.

#### Tests / acceptance criteria

```bash
docker compose build frontend
docker compose up -d

# Frontend serves compiled SPA
curl -s -o /dev/null -w "%{http_code}" http://localhost
# Expect: 200

# JS assets have correct cache headers
JS_FILE=$(ls frontend/dist/assets/*.js 2>/dev/null | head -1 | xargs basename)
curl -s -I "http://localhost/assets/$JS_FILE" | grep -i cache-control
# Expect: max-age=31536000, immutable

# API and Martin still reachable through nginx
curl -s http://localhost/api/sorts | python3 -c "import sys,json; print(len(json.load(sys.stdin)), 'sorts')"
# Expect: 6 sorts

curl -s -o /dev/null -w "%{http_code}" \
  "http://localhost/tiles/tile_heatmap/6/31/21?sort=overall&metric=score"
# Expect: 200 or 204 (empty tiles is OK)

# SPA routing works (direct URL navigation)
curl -s -o /dev/null -w "%{http_code}" http://localhost/some/spa/route
# Expect: 200 (served index.html, not 404)
```

#### Common issues to pre-empt
- `npm run build` fails with `Cannot find module` if the Docker build stage doesn't
  run `npm ci` before `COPY . .`. The Dockerfile must install dependencies before copying source.
- Vite generates hashed filenames (`index.A1b2C3.js`). The nginx `location ~*` regex
  matches all `.js` files — this is correct.
- If the frontend container is on port 80 but nginx was proxying to `:5173`, the
  nginx `upstream` block must be updated before `docker compose up`.

---

### P2-17 · Martin Cache Invalidation

**Goal**: After any pipeline re-run, stale Martin tile caches must be flushed.
Add an admin API endpoint that restarts the Martin container, and document the
complete post-pipeline invalidation workflow.

#### Dependencies

The API container needs Docker CLI access. Mount the Docker socket:
```yaml
# docker-compose.yml — api service volumes
volumes:
  - ./api:/app
  - /var/run/docker.sock:/var/run/docker.sock
```

Install `docker` CLI in the API Dockerfile if not already present:
```dockerfile
RUN apt-get install -y docker-cli   # or: apk add docker-cli (alpine)
```

Alternatively, use the `docker` Python SDK: add `docker>=7.0.0` to `api/requirements.txt`
and use `DockerClient` instead of `subprocess`. The Python SDK is more robust.

#### Read first
- `ARCHITECTURE.md` §7 (Martin cache note — "restart Martin or flush tile cache")
- `api/routes/weights.py` — `_check_admin_key` pattern to reuse
- `docker-compose.yml` — Martin service name

#### Deliverables

**1. Mount Docker socket in `docker-compose.yml`**

```yaml
api:
  volumes:
    - ./api:/app
    - /var/run/docker.sock:/var/run/docker.sock
```

Add a security comment: mounting the Docker socket grants root-equivalent access
inside the API container. Acceptable for hackathon; flag for any production hardening.

**2. Add `docker` SDK to `api/requirements.txt`**

```
docker>=7.0.0
```

**3. Create `api/routes/admin.py`** (if not created in P2-07; extend if it was)

```python
import docker
from fastapi import APIRouter, Header, HTTPException

router = APIRouter()

@router.post("/admin/restart-martin")
async def restart_martin(admin_key: str = Header(alias="X-Admin-Key")):
    from api.routes.weights import _check_admin_key   # reuse the guard
    await _check_admin_key(admin_key)
    try:
        client = docker.from_env()
        # Container name: check with `docker ps --format '{{.Names}}'`
        # Typically: hackeurope-martin-1 or hackeurope_martin_1
        container = client.containers.get("hackeurope-martin-1")
        container.restart(timeout=10)
    except docker.errors.NotFound:
        raise HTTPException(404, "Martin container not found — check container name")
    except Exception as e:
        raise HTTPException(500, detail=str(e))
    return {"status": "martin restarting"}
```

Check the actual container name at runtime with `docker ps` and hard-code it,
or pass it as an environment variable `MARTIN_CONTAINER_NAME` in `.env`.

**4. Create `pipeline/PIPELINE_WORKFLOW.md`** (new file)

Document the complete post-pipeline workflow:

```markdown
## Post-pipeline cache invalidation workflow

After any pipeline re-run, tile caches must be flushed:

1. Run sort pipelines (independently, in any order):
   docker compose --profile pipeline run --rm pipeline python energy/ingest.py
   docker compose --profile pipeline run --rm pipeline python environment/ingest.py
   docker compose --profile pipeline run --rm pipeline python cooling/ingest.py
   docker compose --profile pipeline run --rm pipeline python connectivity/ingest.py
   docker compose --profile pipeline run --rm pipeline python planning/ingest.py

2. Run overall composite (after ALL 5 sort pipelines complete):
   docker compose --profile pipeline run --rm pipeline python overall/compute_composite.py

3. Invalidate Martin tile cache:
   curl -X POST -H "X-Admin-Key: $ADMIN_KEY" http://localhost/api/admin/restart-martin
   # Or manually: docker compose restart martin

4. Invalidate FastAPI response cache (if P2-07 is implemented):
   curl -X POST -H "X-Admin-Key: $ADMIN_KEY" http://localhost/api/admin/invalidate-cache
```

**5. Add tests to `api/tests/test_routes.py`**

Mock `docker.from_env()` using `unittest.mock.patch`:
- `POST /api/admin/restart-martin` without key → 401
- `POST /api/admin/restart-martin` with correct key → mock restart succeeds → 200
- `POST /api/admin/restart-martin` with correct key → container not found → 404

#### Tests / acceptance criteria

```bash
# Without key → 401
curl -s -o /dev/null -w "%{http_code}" -X POST \
  http://localhost/api/admin/restart-martin
# Expect: 401

# With key → 200
curl -s -X POST -H "X-Admin-Key: devkey" \
  http://localhost/api/admin/restart-martin
# Expect: {"status":"martin restarting"}

# Martin back up within 10 seconds
sleep 10
curl -s -o /dev/null -w "%{http_code}" \
  "http://localhost/tiles/tile_heatmap/6/31/21?sort=overall&metric=score"
# Expect: 200
```

---

### P2-18 · IDA Sites Data Entry

**Goal**: Populate the `ida_sites` table with real IDA Ireland industrial site
locations. IDA provides no GIS download — sites must be manually geocoded.
Provide a CSV import script and at least 10 key site entries.

#### Dependencies

Add to `pipeline/requirements.txt` (if not already present):
```
sqlalchemy>=2.0.0
psycopg2-binary>=2.9.0
```
No additional packages needed — the import uses standard library `csv` and sqlalchemy.

#### Read first
- `ARCHITECTURE.md` §11 D3 — IDA data is manual-entry only (no GIS download)
- `sql/tables.sql` — `ida_sites` schema: `site_id`, `name`, `county`, `geom`, `type`,
  `tile_id` (nullable FK to tiles)
- `pipeline/seed_synthetic.py` — 3 sample IDA sites already inserted (will be replaced)

#### Deliverables

**1. Create `pipeline/data/ida_sites.csv`** (new file)

Manually geocode IDA industrial sites from idaireland.com (site list under "Locations"
or "Properties"). Use Google Maps, OSM Nominatim, or Eircode for coordinates.
Minimum 10 sites covering major IDA regions:

```csv
name,county,lat,lng,type,description
IDA Business & Technology Park Athlone,Westmeath,53.4239,-7.9408,industrial,IDA business park
IDA Technology Park Cork,Cork,51.8961,-8.4899,industrial,Cork technology campus
Letterkenny Business Park,Donegal,54.9545,-7.7122,industrial,Donegal IDA park
IDA Industrial Estate Waterford,Waterford,52.2583,-7.1119,industrial,Waterford IDA estate
Galway Technology Park,Galway,53.2814,-9.0494,industrial,Galway technology campus
Shannon Free Zone,Clare,52.7035,-8.9193,industrial,Shannon free zone enterprise area
IDA Business Park Limerick,Limerick,52.6630,-8.6328,industrial,Limerick city campus
Carlow Technology Park,Carlow,52.8400,-6.9259,industrial,Carlow IDA park
Dundalk Technology Park,Louth,53.9976,-6.4003,industrial,Dundalk technology park
Sandyford Business District,Dublin,53.2811,-6.2218,enterprise,Dublin south enterprise zone
```

Add more sites as available from the IDA website. Review and update periodically.

**2. Create `pipeline/import_ida_sites.py`** (new file)

```python
"""
FILE: pipeline/import_ida_sites.py
Role: Import IDA Ireland industrial site locations from CSV into ida_sites table.
      Safe to re-run — uses ON CONFLICT (name) DO UPDATE.
Data: Manually geocoded from IDA Ireland website (idaireland.com).
      No GIS download exists (ARCHITECTURE.md §11 D3).
      Review CSV periodically — IDA site portfolio changes.
Run:  python import_ida_sites.py
"""
import csv
from pathlib import Path
import sqlalchemy
from sqlalchemy import text
from config import DB_URL

IDA_CSV = Path(__file__).parent / "data" / "ida_sites.csv"

def import_ida_sites(engine: sqlalchemy.Engine) -> int:
    with open(IDA_CSV, newline="") as f:
        rows = list(csv.DictReader(f))
    with engine.begin() as conn:
        for row in rows:
            conn.execute(text("""
                INSERT INTO ida_sites (name, county, geom, type, description)
                VALUES (
                    :name, :county,
                    ST_SetSRID(ST_MakePoint(:lng, :lat), 4326),
                    :type, :description
                )
                ON CONFLICT (name) DO UPDATE SET
                    county = EXCLUDED.county,
                    geom   = EXCLUDED.geom,
                    type   = EXCLUDED.type
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
    print("Re-run planning/ingest.py and overall/compute_composite.py to update nearest_ida_site_km")
    print("Then restart Martin: docker compose restart martin")
```

**3. Ensure downstream pipelines are refreshed after import**

After adding IDA sites, these downstream steps must be re-run:
- `pipeline/planning/ingest.py` — recomputes `nearest_ida_site_km` in `planning_scores`
- `pipeline/overall/compute_composite.py` — recomputes `nearest_data_centre_km`
  in `overall_scores` (if any IDA sites are also in `pins_overall`)
- Restart Martin to flush tile cache

Add reminder comments to the end of `import_ida_sites.py` (already in the script above).

#### Tests / acceptance criteria

```bash
docker compose --profile pipeline run --rm pipeline python import_ida_sites.py
# Expect: "Imported N IDA sites" (N >= 10)

docker compose exec db psql -U hackeurope -d hackeurope -c "
  SELECT name, county, tile_id IS NOT NULL AS has_tile FROM ida_sites ORDER BY name;"
# Expect: >= 10 rows; most have tile_id (coastal sites may be NULL)

# Planning sidebar should now show nearest IDA distance
curl -s "http://localhost:8000/api/tile/1?sort=planning" | python3 -c "
import sys, json
data = json.load(sys.stdin)
print('nearest_ida_site_km:', data.get('nearest_ida_site_km'))
"
# Expect: a float distance value (not null) after re-running planning ingest
```

---

## AI Features

---

### P2-19 · AI Executive Summary

**Goal**: Add an "AI Summary" button to the bottom of the sidebar. Clicking it calls a new
`POST /api/tile/{tile_id}/summary?sort={sort}` endpoint that passes the tile's existing
metric data to Claude claude-sonnet-4-6 and returns a 2–3 sentence plain-text executive
summary scoped to the active sort. Summaries are generated on demand (not on tile load)
to avoid unnecessary API calls.

#### Dependencies

Add `anthropic>=0.40.0` to `api/requirements.txt`. Set `ANTHROPIC_API_KEY` as an env var
in the `api` service in `docker-compose.yml` and in `.env.example`.

#### Read first
- `api/routes/tile.py` — the `_get_*` helpers; the summary endpoint reuses them to fetch tile data
- `api/main.py` — router registration pattern; also add `"POST"` to CORS `allow_methods`
- `frontend/src/components/Sidebar.vue` — where to add the button; accesses `store` directly

#### Deliverables

**1. Create `api/routes/summary.py`** — `POST /api/tile/{tile_id}/summary?sort={sort}`.
Reuse the existing `_get_*` helper for the active sort to fetch tile data, then call
`claude-sonnet-4-6` with a prompt instructing it to write 2–3 plain-text sentences for a
non-technical executive audience. Return `{"summary": "<text>"}`.

**2. Register the router in `api/main.py`** — import and `include_router` the new summary
router following the same pattern as the existing routes. Also add `"POST"` to
`allow_methods` in the `CORSMiddleware` config (currently only `GET` and `PUT` are listed,
which would block the frontend fetch).

**3. Add the button to `Sidebar.vue`** — add a `Sparkles`-icon "AI Summary" button at the
bottom of the `sidebar__body` block (inside the `v-else-if="store.selectedTileData"` div,
after the sort-specific component). Wire it to `POST /api/tile/{store.selectedTileId}/summary?sort={store.activeSort}`,
show a "Generating…" disabled state while loading, render the returned text below the button,
and clear the summary whenever `store.selectedTileId` or `store.activeSort` changes.

#### Tests / acceptance criteria

```bash
curl -X POST "http://localhost:8000/api/tile/1/summary?sort=energy"
# Expect: {"summary": "<2-3 sentences, no markdown>"}

cd frontend && npm run type-check
# Expect: 0 errors
```

Manual: click a tile → "AI Summary" button visible → click → summary appears; switching
sort or tile clears the summary immediately.

---

## Data & Metrics

---

### P2-20 · Real Estate / Land Pricing

**Goal**: Add land and commercial property pricing data as a new metric layer. Ireland's
land cost varies dramatically by region and is a critical factor in data centre siting
decisions. This task adds pricing data to the pipeline, creates a heatmap sub-metric,
and displays pricing info in the sidebar.

#### Approach — integrate into Planning sort (not a new sort)

Land pricing is most naturally a **Planning** consideration (alongside zoning, IDA proximity,
and planning precedent). Adding it to Planning avoids fragmenting the 5-sort model.

#### Dependencies

- Tiles table populated (Phase 1)
- Planning pipeline (P2-05) structure exists

#### Read first
- `sql/tables.sql` — `planning_scores` schema
- `api/routes/sorts.py` — Planning metric definitions
- `api/routes/tile.py` — `_get_planning()` sidebar response
- `pipeline/planning/ingest.py` — planning scoring logic
- `frontend/src/components/SidebarPlanning.vue` — planning sidebar display

#### Deliverables

**1. Source land pricing data**

Options (investigate in order of preference):
- **CSO (Central Statistics Office)** — residential property price register (csv.cso.ie)
- **DAFT.ie / MyHome.ie** — commercial property listings (may need scraping or API)
- **Property Services Regulatory Authority (PSRA)** — commercial lease register
- **Manual county-level estimates** — fallback: create a CSV with avg commercial land
  price per county from industry reports (CBRE, JLL, Savills Ireland market reports)

Create `pipeline/data/land_pricing.csv`:
```csv
county,avg_price_per_acre_eur,avg_commercial_rent_per_sqm_eur,source,year
Dublin,500000,350,CBRE_2024,2024
Cork,120000,180,CBRE_2024,2024
Galway,95000,150,CBRE_2024,2024
...
```

**2. Extend `planning_scores` table**

Add columns to `sql/tables.sql`:
```sql
ALTER TABLE planning_scores ADD COLUMN IF NOT EXISTS
  land_price_score SMALLINT CHECK (land_price_score BETWEEN 0 AND 100);
ALTER TABLE planning_scores ADD COLUMN IF NOT EXISTS
  avg_price_per_acre_eur REAL;
ALTER TABLE planning_scores ADD COLUMN IF NOT EXISTS
  avg_commercial_rent_per_sqm_eur REAL;
```

Add to `metric_ranges`:
```sql
INSERT INTO metric_ranges (sort, metric, min_val, max_val)
VALUES ('planning', 'avg_price_per_acre_eur', 0, 0)
ON CONFLICT (sort, metric) DO NOTHING;
```

**3. Update planning pipeline** (`pipeline/planning/ingest.py`)

- Join tiles to counties, look up land pricing by county
- Normalise price to 0–100 (INVERTED — lower price = higher score for siting)
- Incorporate `land_price_score` into the planning composite formula
- Update `metric_ranges` with actual min/max after ingest

**4. Update API**

- `api/routes/sorts.py` — add `land_price` metric to Planning sort:
  ```python
  MetricMeta(key="land_price", label="Land price score", unit="0–100", is_default=False)
  ```
  Also add `avg_price_per_acre_eur` as a raw sub-metric (needs metric_ranges for legend).
- `api/routes/tile.py` — add `land_price_score`, `avg_price_per_acre_eur`,
  `avg_commercial_rent_per_sqm_eur` to `_get_planning()` response.

**5. Update Martin tile function**

- `sql/functions.sql` — add `land_price` WHEN clause to `tile_heatmap` for the
  planning sort, reading from `planning_scores.land_price_score`.
- Add `avg_price_per_acre_eur` raw metric support with `metric_ranges` normalisation.

**6. Update frontend sidebar**

- `SidebarPlanning.vue` — add a "Land Pricing" section showing:
  - Land price score (0–100 bar)
  - Avg price per acre (formatted as €XXX,XXX)
  - Avg commercial rent per sqm (formatted as €XXX/m²)

#### Tests / acceptance criteria

```bash
# Pipeline runs without error
docker compose --profile pipeline run --rm pipeline python planning/ingest.py

# Land pricing data populated
docker compose exec db psql -U hackeurope -d hackeurope -c "
  SELECT COUNT(*) FROM planning_scores WHERE land_price_score IS NOT NULL;"
# Expect: > 0

# API returns pricing fields
curl -s "http://localhost:8000/api/tile/1?sort=planning" | python3 -c "
import sys, json; d = json.load(sys.stdin)
print('land_price_score:', d.get('land_price_score'))
print('avg_price_per_acre_eur:', d.get('avg_price_per_acre_eur'))
"

# Martin serves land_price metric
curl -s -o /dev/null -w "%{http_code}" \
  "http://localhost/tiles/tile_heatmap/6/31/21?sort=planning&metric=land_price"
# Expect: 200 or 204
```

---

### P2-21 · Custom Combination Builder (Frontend)

**Goal**: Add a "Custom" tab at the bottom of the DataBar that opens a metric selection
panel on the right side of the screen. Users can search across all metrics from all sorts,
select the ones they care about, assign custom weightings, and see a live-updating custom
heatmap. This enables ad-hoc multi-factor analysis beyond the fixed 5 sorts.

#### Dependencies

- All sort pipelines complete (P2-01 through P2-05) — needs real metric data
- `GET /api/tile/{id}/all` endpoint (P2-08) — fetches all sort data for a single tile
- Martin MVT tiles for individual sub-metrics

#### Read first
- `frontend/src/stores/suitability.ts` — store state, `setActiveSort`, `martinTileUrl`
- `frontend/src/components/DataBar.vue` — sort tab rendering, metric pills
- `api/routes/sorts.py` — metric definitions (drives the searchable metric catalogue)
- `sql/functions.sql` — `tile_heatmap` function (may need a custom composite mode)

#### Deliverables

**1. Add "Custom" pseudo-sort to the DataBar**

In `DataBar.vue`, after the sort tabs loop, add a fixed "Custom" tab:
```html
<button
  :class="['sort-tab', { active: store.activeSort === 'custom' }]"
  @click="store.setActiveSort('custom')"
>
  <SlidersHorizontal :size="16" />
  Custom
</button>
```

When "Custom" is active, the metric pills row is hidden (replaced by the builder panel).

**2. Create `frontend/src/components/CustomBuilder.vue`** (new file)

A right-side panel (similar width to Sidebar, ~380px) containing:

- **Search bar** — text input that filters all available metrics across all sorts.
  Metric catalogue comes from `store.sortsMeta` (already fetched from `/api/sorts`).
  Each searchable item shows: `{sortLabel} → {metricLabel}` (e.g. "Energy → Wind speed at 100m").

- **Selected metrics list** — draggable/reorderable cards, each showing:
  - Sort icon + metric label
  - Weight slider (0–100) or numeric input
  - Remove button (×)
  - Weights auto-normalise to sum to 100% (display the effective %)

- **"Apply" button** — triggers the custom heatmap computation.

- **Presets** (stretch goal) — save/load named custom combinations to localStorage.

**3. Extend the store** (`suitability.ts`)

```typescript
// New state
customMetrics: [] as Array<{ sort: string; metric: string; weight: number }>,
customBuilderOpen: boolean = false,

// New actions
addCustomMetric(sort: string, metric: string): void
removeCustomMetric(sort: string, metric: string): void
setCustomWeight(sort: string, metric: string, weight: number): void
```

When `activeSort === 'custom'`:
- `martinTileUrl` points to a new custom tile endpoint (see below)
- Sidebar shows tile detail aggregated from all selected metrics
- `customBuilderOpen` controls the builder panel visibility

**4. Backend: Custom composite tile endpoint**

Option A — **Client-side blending** (simpler, recommended for hackathon):
- Frontend fetches multiple Martin tile layers (one per selected metric)
- Blends them client-side using `map.queryRenderedFeatures()` to read values
  per tile and compute a weighted average
- Pros: no backend changes. Cons: only works for visible tiles.

Option B — **Server-side custom composite** (more robust):
- New API endpoint: `POST /api/custom-tiles` with body `{metrics: [{sort, metric, weight}]}`
- Returns a temporary Martin-compatible tile URL or pre-computed tile data
- Requires dynamic SQL in the tile function (security concern — use parameterised queries)

**Recommend Option A** for the hackathon — it avoids dynamic SQL and works immediately
with existing Martin tile layers.

**5. Client-side tile blending implementation**

When custom sort is active with N selected metrics:
1. Add N invisible Martin tile sources to the map (one per metric)
2. On `map.on('idle')`, iterate visible tiles, `querySourceFeatures()` each source
3. Compute weighted average `value` per `tile_id`
4. Update a single visible fill layer's `fill-color` using a match expression
   built from the computed values

This is a MapLibre data-join pattern — performant for ~14k tiles at zoom levels 6–10.

**6. Custom tile click → sidebar**

When a tile is clicked in custom mode:
- Call `GET /api/tile/{id}/all` to fetch all sort data
- Display a custom sidebar showing only the selected metrics with their values,
  individual scores, and the computed weighted composite

#### Tests / acceptance criteria

```bash
cd frontend && npm run type-check
# Expect: 0 errors
```

Manual:
1. Click "Custom" tab in DataBar → builder panel slides in from right
2. Search "wind" → "Energy → Wind speed at 100m" appears in results
3. Select 3 metrics → they appear in the selected list with weight sliders
4. Adjust weights → effective percentages update in real time
5. Click "Apply" → map updates to show custom blended heatmap
6. Click a tile → sidebar shows the selected metrics and weighted composite
7. Switch back to a standard sort → builder panel closes, map reverts to normal

---

### P2-22 · Metric Reorganisation

**Goal**: Reorganise which metrics sit under which sort to better reflect their logical
grouping. Some metrics are currently in sorts where they don't obviously belong. This
task moves metrics, updates all affected layers (DB schema, pipeline, API, Martin,
frontend sidebar).

#### Current pain points to address

Review and propose moves for these candidates:

| Metric | Current sort | Proposed sort | Rationale |
|---|---|---|---|
| `flood_risk` | Environment | Planning | Flooding is a planning/regulatory concern |
| `landslide_risk` | Environment | Planning | Same as above — geotechnical constraint |
| `aquifer_productivity` | Cooling | Environment | Aquifer is an environmental resource |
| `water_proximity` | Cooling | Environment | Water bodies are environmental features |
| `grid_proximity` | Energy | Connectivity | Grid access is infrastructure/connectivity |
| `population_density` | Planning | (sidebar-only, keep) | Already sidebar-only |

> **Important**: This list is a starting proposal. Confirm with the team before
> implementing — metric moves affect scoring formulas, so sort composite scores
> will change.

#### Dependencies

- All sort pipelines complete (P2-01 through P2-05)
- Must re-run all affected pipelines + overall composite after moves

#### Read first
- `sql/tables.sql` — all score table schemas
- `api/routes/sorts.py` — metric definitions per sort
- `api/routes/tile.py` — `_get_*()` helpers per sort
- `pipeline/*/ingest.py` — scoring formulas
- `sql/functions.sql` — `tile_heatmap` function CASE branches
- `frontend/src/components/Sidebar*.vue` — sort-specific sidebar components

#### Deliverables (per metric move)

For each metric being moved from Sort A → Sort B:

**1. Database migration**
- Add column to Sort B's score table
- Drop column from Sort A's score table (after data migration)
- Update `metric_ranges` rows (change `sort` value)

**2. Pipeline update**
- Move the metric computation from Sort A's `ingest.py` to Sort B's `ingest.py`
- Update both sorts' composite score formulas (rebalance weights)
- Update `metric_ranges` writes

**3. API update**
- `sorts.py` — move `MetricMeta` entry from Sort A to Sort B
- `tile.py` — move the field from `_get_sort_a()` to `_get_sort_b()` response

**4. Martin update**
- `functions.sql` — move the CASE branch from Sort A to Sort B in `tile_heatmap`

**5. Frontend update**
- Move the display section from `SidebarSortA.vue` to `SidebarSortB.vue`

**6. Re-run pipelines**
```bash
# Re-run both affected sort pipelines
docker compose --profile pipeline run --rm pipeline python {sort_a}/ingest.py
docker compose --profile pipeline run --rm pipeline python {sort_b}/ingest.py
# Re-run overall composite
docker compose --profile pipeline run --rm pipeline python overall/compute_composite.py
# Restart Martin
docker compose restart martin
```

#### Tests / acceptance criteria

```bash
# All sort scores still valid (no NULLs in score column)
for sort in energy environment cooling connectivity planning; do
  echo "$sort:"
  docker compose exec db psql -U hackeurope -d hackeurope -c "
    SELECT COUNT(*) as total,
           COUNT(score) as scored,
           COUNT(*) - COUNT(score) as missing
    FROM ${sort}_scores;"
done

# API returns moved metrics under new sort
curl -s "http://localhost:8000/api/sorts" | python3 -c "
import sys, json
for s in json.load(sys.stdin):
    print(f\"{s['key']}: {[m['key'] for m in s['metrics']]}\")
"

# Frontend type-check
cd frontend && npm run type-check
# Expect: 0 errors
```

Manual: verify each moved metric appears in the correct sort's sidebar panel
and renders correctly as a heatmap sub-metric on the map.

---

### P2-23 · Renewable Energy Penetration Metric

**Goal**: Add a "renewability" metric to the Energy sort that scores how renewable the
electricity supply is in each grid tile's area. Data centres increasingly need to
demonstrate they're powered by renewable sources — this metric quantifies the local
renewable energy mix, giving high scores to areas dominated by wind, solar, and hydro
generation and low scores to areas reliant on gas/peat/oil.

#### Approach — new sub-metric within Energy sort

This fits naturally into **Energy** alongside wind speed, solar GHI, and grid proximity.
It captures a distinct concern: not just whether renewable *potential* exists (wind/solar),
but whether the *actual grid supply* in the area is renewable.

#### Data sources (investigate in order)

1. **EirGrid / SEMO** — generation unit data by fuel type and location. EirGrid publishes
   installed capacity (MW) per generator with grid coordinates. Aggregate wind + solar +
   hydro capacity vs total capacity within a radius of each tile.
   - URL: eirgridgroup.com → "Generation Capacity Statement"
   - Also: SEMO (sem-o.com) publishes unit-level generation data

2. **SEAI (Sustainable Energy Authority of Ireland)** — county-level renewable energy
   statistics (energy balances, renewable share by county/region).
   - URL: seai.ie → "Energy Statistics" → "Renewable Energy in Ireland"
   - Provides % renewable electricity by region

3. **CSO** — energy statistics tables (statbank.cso.ie), including fuel mix by region.

4. **Wind Energy Ireland** — wind farm locations and capacities (CSV/map).
   - URL: windenergyireland.com → "Wind Farms Map"

5. **Fallback: county-level estimates** — if granular data is unavailable, use SEAI
   county-level renewable % from their annual report and assign to all tiles in that county.

#### Dependencies

- Tiles table populated (Phase 1)
- Energy pipeline (P2-01) structure exists

#### Read first
- `sql/tables.sql` — `energy_scores` schema
- `api/routes/sorts.py` — Energy metric definitions
- `api/routes/tile.py` — `_get_energy()` sidebar response
- `pipeline/energy/ingest.py` — energy scoring logic
- `frontend/src/components/SidebarEnergy.vue` — energy sidebar display

#### Deliverables

**1. Source and prepare renewable generation data**

Create `pipeline/data/renewable_generation.csv` — either:

*Option A: Generator-level (preferred)*
```csv
name,type,capacity_mw,lat,lng,fuel
Arklow Bank Wind Park,wind,520,52.7910,-5.9460,wind
Lisheen Solar Farm,solar,30,52.6410,-7.8120,solar
Ardnacrusha,hydro,86,52.7080,-8.6130,hydro
Moneypoint,thermal,915,52.6180,-9.4420,coal
...
```

*Option B: County-level (fallback)*
```csv
county,renewable_pct,total_capacity_mw,renewable_capacity_mw,source,year
Dublin,12,1800,216,SEAI_2024,2024
Cork,45,950,428,SEAI_2024,2024
Kerry,78,320,250,SEAI_2024,2024
Donegal,82,280,230,SEAI_2024,2024
...
```

**2. Extend `energy_scores` table**

```sql
ALTER TABLE energy_scores ADD COLUMN IF NOT EXISTS
  renewable_pct REAL;
ALTER TABLE energy_scores ADD COLUMN IF NOT EXISTS
  renewable_score SMALLINT CHECK (renewable_score BETWEEN 0 AND 100);
ALTER TABLE energy_scores ADD COLUMN IF NOT EXISTS
  renewable_capacity_mw REAL;
ALTER TABLE energy_scores ADD COLUMN IF NOT EXISTS
  fossil_capacity_mw REAL;
```

Add to `metric_ranges`:
```sql
INSERT INTO metric_ranges (sort, metric, min_val, max_val)
VALUES ('energy', 'renewable_pct', 0, 100)
ON CONFLICT (sort, metric) DO NOTHING;
```

**3. Update energy pipeline** (`pipeline/energy/ingest.py`)

*If using generator-level data (Option A):*
- For each tile, find all generators within a configurable radius (e.g. 25 km)
- Sum renewable capacity (wind + solar + hydro + biomass) and total capacity
- `renewable_pct = renewable_capacity / total_capacity * 100`
- `renewable_score = renewable_pct` (linear — 100% renewable = score 100)
- Store `renewable_capacity_mw` and `fossil_capacity_mw` for sidebar display

*If using county-level data (Option B):*
- Join tiles to counties, look up `renewable_pct`
- `renewable_score = renewable_pct`

**Update the energy composite formula** — incorporate `renewable_score`:
```python
# Current: 0.35 * wind + 0.30 * solar + 0.35 * grid_proximity
# New:     0.30 * wind + 0.25 * solar + 0.25 * grid_proximity + 0.20 * renewable
```
(Weights are a starting proposal — adjust as needed.)

Update `metric_ranges` with actual min/max after ingest.

**4. Update API**

- `api/routes/sorts.py` — add metrics to Energy sort:
  ```python
  MetricMeta(key="renewable", label="Renewable energy score", unit="0–100", is_default=False),
  MetricMeta(key="renewable_pct", label="Renewable energy %", unit="%", is_default=False),
  ```
- `api/routes/tile.py` — add to `_get_energy()` response:
  `renewable_score`, `renewable_pct`, `renewable_capacity_mw`, `fossil_capacity_mw`

**5. Update Martin tile function**

`sql/functions.sql` — add CASE branches to `tile_heatmap` for the energy sort:
```sql
WHEN metric = 'renewable' THEN es.renewable_score
WHEN metric = 'renewable_pct' THEN  -- raw metric, use metric_ranges normalisation
  CASE WHEN mr.min_val IS NOT NULL AND mr.max_val > mr.min_val
    THEN ((es.renewable_pct - mr.min_val) / (mr.max_val - mr.min_val) * 100)::int
    ELSE 0 END
```

**6. Update frontend sidebar** (`SidebarEnergy.vue`)

Add a "Renewable Energy" section showing:
- Renewable energy score (0–100 bar, matching existing metric bar style)
- Renewable % of local grid supply (e.g. "72% renewable")
- Breakdown: renewable capacity vs fossil capacity (MW)
- Optional: small stacked bar showing wind/solar/hydro/fossil proportions

**7. Update DataBar metric pill**

The new `renewable` and `renewable_pct` metrics will automatically appear as selectable
pills in the DataBar (since it's data-driven from `/api/sorts`). No manual DataBar
changes needed.

#### Tests / acceptance criteria

```bash
# Pipeline runs without error
docker compose --profile pipeline run --rm pipeline python energy/ingest.py

# Renewable data populated
docker compose exec db psql -U hackeurope -d hackeurope -c "
  SELECT COUNT(*) as total,
         COUNT(renewable_score) as has_renewable,
         ROUND(AVG(renewable_pct)::numeric, 1) as avg_renewable_pct
  FROM energy_scores;"
# Expect: has_renewable > 0, avg_renewable_pct is a reasonable value (30-60%)

# API returns renewable fields
curl -s "http://localhost:8000/api/tile/1?sort=energy" | python3 -c "
import sys, json; d = json.load(sys.stdin)
print('renewable_score:', d.get('renewable_score'))
print('renewable_pct:', d.get('renewable_pct'))
print('renewable_capacity_mw:', d.get('renewable_capacity_mw'))
"

# Martin serves renewable metric
curl -s -o /dev/null -w "%{http_code}" \
  "http://localhost/tiles/tile_heatmap/6/31/21?sort=energy&metric=renewable"
# Expect: 200 or 204

# Energy composite formula still produces valid scores
docker compose exec db psql -U hackeurope -d hackeurope -c "
  SELECT MIN(score), MAX(score), ROUND(AVG(score)::numeric, 1) FROM energy_scores;"
# Expect: min >= 0, max <= 100

# Frontend type-check
cd frontend && npm run type-check
# Expect: 0 errors
```

Manual:
1. Select Energy sort → "Renewable energy score" pill appears in DataBar
2. Click it → map shows renewable heatmap (green in west/northwest, lower in east)
3. Click a tile → sidebar shows renewable %, capacity breakdown
4. Switch to "Renewable energy %" pill → map shows raw % with legend min/max

<!--
  FILE: frontend/src/components/MapView.vue
  Role: MapLibre GL JS map instance — choropleth tiles, pins, hover/select interactions.
        This component NEVER fetches data directly — reads from useSuitabilityStore.
  Agent boundary: Frontend — MapView (§6.2, §10)
  Dependencies:
    - useSuitabilityStore (martinTileUrl, pins, selectedTileId, activeSort)
    - Martin tile server running (MVT tiles at /tiles/tile_heatmap/{z}/{x}/{y})
    - vue-maplibre-gl installed
  Output: Interactive map; dispatches setSelectedTile(), clearSelection() to store
  How to test: Run dev server; map should render Ireland with green tile choropleth

  Layer stack (bottom → top):
    1. base-map       — muted OSM raster
    2. tiles-fill     — choropleth fill (Martin MVT, score-driven colour)
    3. tiles-border   — tile grid strokes
    4. pins-clusters  — clustered pin circles (zoom < 10)
    5. pins-labels    — cluster count labels
    6. pins-unclustered — individual pin icons (zoom >= 10)
    7. tiles-hover    — hover highlight (white stroke, no fill)
    8. tiles-selected — selected tile highlight (white fill 0.15 + white stroke)

  Martin tile URL format:
    /tiles/tile_heatmap/{z}/{x}/{y}?sort={activeSort}&metric={activeMetric}
  Both params required. Rebuilt reactively via store.martinTileUrl.

  IMPORTANT: Do not use MapLibre type:heatmap — this is a choropleth (type:fill).
-->
<template>
  <div class="map-container" ref="mapContainer">
    <!-- MapLibre map renders into this div -->
    <div ref="mapEl" class="map" />

    <!-- Map Legend: positioned bottom-left inside map viewport -->
    <MapLegend class="map-legend" />

    <!-- Toast notification for tile load errors -->
    <div v-if="tileError" class="map-toast">
      Map data unavailable — check server
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, watch, onMounted, onUnmounted, computed } from 'vue'
import maplibregl from 'maplibre-gl'
import { useSuitabilityStore } from '@/stores/suitability'
import { COLOR_RAMPS, TEMPERATURE_RAMP } from '@/types'
import MapLegend from '@/components/MapLegend.vue'

const store = useSuitabilityStore()

const mapEl = ref<HTMLDivElement | null>(null)
const tileError = ref(false)
let map: maplibregl.Map | null = null
let hoveredTileId: number | null = null

// ── Map initialisation ────────────────────────────────────────

onMounted(() => {
  if (!mapEl.value) return

  map = new maplibregl.Map({
    container: mapEl.value,
    style: 'https://tiles.openfreemap.org/styles/liberty',
    center: [-7.6, 53.4],  // Ireland centroid
    zoom: 6.5,
    minZoom: 5,
    maxZoom: 18,
    // OpenFreeMap's glyph server at tiles.openfreemap.org/fonts/ returns 404.
    // Redirect glyph requests to the MapLibre demo tiles font CDN instead.
    transformRequest: (url, resourceType) => {
      if (resourceType === 'Glyphs' && url.startsWith('https://tiles.openfreemap.org/fonts/')) {
        return { url: url.replace('https://tiles.openfreemap.org/fonts/', 'https://demotiles.maplibre.org/font/') }
      }
    },
  })

  map.addControl(new maplibregl.NavigationControl(), 'top-right')
  map.addControl(new maplibregl.ScaleControl({ unit: 'metric' }), 'bottom-right')

  map.on('load', () => {
    setupSources()
    setupLayers()
    setupInteractions()
  })
})

onUnmounted(() => {
  map?.remove()
})

// ── Source + layer setup ──────────────────────────────────────

function setupSources() {
  if (!map) return

  // Martin MVT vector tile source — choropleth tiles
  map.addSource('tiles-mvt', {
    type: 'vector',
    tiles: [store.martinTileUrl],
    minzoom: 0,
    maxzoom: 14,
  })

  // Pins GeoJSON source (with clustering)
  map.addSource('pins', {
    type: 'geojson',
    data: store.pins,
    cluster: true,
    clusterMaxZoom: 10,  // cluster below zoom 10 (ARCHITECTURE.md §10 rule 6)
    clusterRadius: 50,
  })
}

function setupLayers() {
  if (!map) return

  // Find the first symbol layer in the basemap style so we can insert our
  // choropleth fill BEFORE it. This puts the heatmap above land/water fill
  // but underneath text labels and road symbols.
  const firstSymbolId = map.getStyle().layers.find(l => l.type === 'symbol')?.id

  // Layer 2: Choropleth fill — score-driven colour from Martin MVT tiles
  map.addLayer({
    id: 'tiles-fill',
    type: 'fill',
    source: 'tiles-mvt',
    'source-layer': 'tile_heatmap',
    paint: {
      'fill-color': buildColorExpression(),
      'fill-opacity': 0.7,
    },
  }, firstSymbolId)

  // Layer 3: Tile grid borders — subtle white lines between tiles
  map.addLayer({
    id: 'tiles-border',
    type: 'line',
    source: 'tiles-mvt',
    'source-layer': 'tile_heatmap',
    paint: {
      'line-color': '#ffffff',
      'line-opacity': 0.25,
      'line-width': 0.5,
    },
  }, firstSymbolId)

  // Layer 4: Clustered pin circles — visible when multiple pins overlap at low zoom
  // filter: ['has', 'point_count'] means "only show features that MapLibre has clustered"
  // (MapLibre automatically adds point_count to clustered features from GeoJSON sources)
  map.addLayer({
    id: 'pins-clusters',
    type: 'circle',
    source: 'pins',
    filter: ['has', 'point_count'],
    paint: {
      // step() expression: radius 18 for <10 pins, 22 for <50, 28 for 50+
      'circle-radius': ['step', ['get', 'point_count'], 18, 10, 22, 50, 28],
      'circle-color': 'rgba(255, 255, 255, 0.15)',
      'circle-stroke-width': 1,
      'circle-stroke-color': '#ffffff',
    },
  })

  // Layer 5: Cluster count labels — the number shown inside each cluster circle
  map.addLayer({
    id: 'pins-labels',
    type: 'symbol',
    source: 'pins',
    filter: ['has', 'point_count'],
    layout: {
      // point_count_abbreviated is auto-generated by MapLibre (e.g. "12" or "1.2k")
      'text-field': '{point_count_abbreviated}',
      'text-size': 12,
    },
    paint: {
      'text-color': '#ffffff',
    },
  })

  // Layer 6: Unclustered individual pins — visible at higher zoom levels
  // filter: ['!', ['has', 'point_count']] means "not a cluster, a real single pin"
  // Colour varies by pin type using a match expression
  map.addLayer({
    id: 'pins-unclustered',
    type: 'circle',
    source: 'pins',
    filter: ['!', ['has', 'point_count']],
    paint: {
      'circle-radius': 6,
      'circle-color': [
        'match', ['get', 'type'],
        // overall sort pins
        'data_centre', '#ff6b6b',
        'ida_site', '#ffd93d',
        // energy sort pins
        'wind_farm', '#74c476',
        'transmission_node', '#fd8d3c',
        'substation', '#e6550d',
        // environment sort pins
        'sac', '#d73027',
        'spa', '#fc8d59',
        'nha', '#fee08b',
        'pnha', '#d9ef8b',
        'flood_zone', '#4575b4',
        // cooling sort pins
        'hydrometric_station', '#6baed6',
        'waterbody', '#08519c',
        'met_station', '#9ecae1',
        // connectivity sort pins
        'internet_exchange', '#9e9ac8',
        'motorway_junction', '#bcbddc',
        'broadband_area', '#dadaeb',
        // planning sort pins
        'zoning_parcel', '#fd8d3c',
        'planning_application', '#fdae6b',
        // fallback colour for any unrecognised type
        '#cccccc',
      ] as maplibregl.ExpressionSpecification,
      'circle-stroke-width': 1,
      'circle-stroke-color': '#ffffff',
    },
  })

  // Layer 7: Hover highlight — 2px white border appears around the hovered tile
  // Uses a line layer (not fill) so we get a visible stroke without filling the tile
  map.addLayer({
    id: 'tiles-hover',
    type: 'line',
    source: 'tiles-mvt',
    'source-layer': 'tile_heatmap',
    paint: {
      'line-color': '#ffffff',
      'line-width': 2,
      'line-opacity': 0.8,
    },
    filter: ['==', ['get', 'tile_id'], -1],  // -1 = no tile matches initially
  })

  // Layer 8: Selected tile highlight — white semi-transparent fill + white border
  map.addLayer({
    id: 'tiles-selected',
    type: 'fill',
    source: 'tiles-mvt',
    'source-layer': 'tile_heatmap',
    paint: {
      'fill-color': 'rgba(255, 255, 255, 0.15)',
      'fill-outline-color': 'rgba(255, 255, 255, 0.85)',
    },
    filter: ['==', ['get', 'tile_id'], -1],  // updated when tile selected
  })
}

function setupInteractions() {
  if (!map) return

  // Hover interaction
  map.on('mousemove', 'tiles-fill', (e) => {
    if (!e.features?.length) return
    const tileId = e.features[0].properties?.tile_id
    if (tileId === hoveredTileId) return
    hoveredTileId = tileId
    map!.getCanvas().style.cursor = 'pointer'
    // Update hover filter to highlight this tile
    if (!store.selectedTileId) {
      map!.setFilter('tiles-hover', ['==', ['get', 'tile_id'], tileId])
    }
  })

  map.on('mouseleave', 'tiles-fill', () => {
    hoveredTileId = null
    map!.getCanvas().style.cursor = ''
    map!.setFilter('tiles-hover', ['==', ['get', 'tile_id'], -1])
  })

  // Click: tile selection
  map.on('click', 'tiles-fill', async (e) => {
    if (!e.features?.length) return
    const tileId = e.features[0].properties?.tile_id as number
    if (!tileId) return

    // Update selected tile filter
    map!.setFilter('tiles-selected', ['==', ['get', 'tile_id'], tileId])

    // Dispatch to store — fetches tile detail + opens sidebar
    await store.setSelectedTile(tileId)

    // Shift map viewport left so the selected tile isn't hidden behind the sidebar
    map!.easeTo({ padding: { left: 0, top: 0, bottom: 0, right: 380 }, duration: 300 })
  })

  // Click empty area: clear selection
  map.on('click', (e) => {
    const features = map!.queryRenderedFeatures(e.point, { layers: ['tiles-fill'] })
    if (!features.length) {
      store.clearSelection()
      map!.setFilter('tiles-selected', ['==', ['get', 'tile_id'], -1])
    }
  })

  // Error handling for tile source.
  // MapLibre's ErrorEvent type doesn't include sourceId in its type definition,
  // but the runtime event object does include it for source-related errors.
  // We use 'in' to safely check for the property before accessing it — this
  // narrows the type and avoids an unsafe 'any' cast.
  map.on('error', (e) => {
    if ('sourceId' in e && (e as any).sourceId === 'tiles-mvt') {
      tileError.value = true
      setTimeout(() => { tileError.value = false }, 5000)
    }
  })
}

// ── Colour expression builder ─────────────────────────────────

/**
 * Builds a MapLibre interpolate expression for the active sort's colour ramp.
 * The 'value' property from Martin is 0–100 (normalised).
 * Temperature sub-metric is already inverted in the SQL function.
 */
function buildColorExpression(): maplibregl.ExpressionSpecification {
  const ramp = COLOR_RAMPS[store.activeSort]
  // MapLibre interpolate expression: linearly blend between colour stops based on 'value' property.
  // 'value' is a 0–100 normalised score emitted by Martin's tile_heatmap SQL function.
  // coalesce provides a fallback of 0 if 'value' is missing (e.g. tile has no score data).
  // flatMap converts [[0,'#aaa'],[50,'#bbb'],[100,'#ccc']] → [0,'#aaa',50,'#bbb',100,'#ccc']
  // which is the format MapLibre expects: stop, color, stop, color, ...
  // to-number converts 'value' to a number and returns the fallback (0) if
  // the property is missing or null — handles tiles with no score data.
  // Preferred over coalesce here because to-number resolves to type 'number'
  // at compile time; coalesce(get(...), 0) resolves to 'value' (any) in
  // MapLibre v4's type system and causes a runtime null error in interpolate.
  return [
    'interpolate', ['linear'], ['to-number', ['get', 'value'], 0],
    ...ramp.stops.flatMap(([stop, color]) => [stop, color]),
  ] as maplibregl.ExpressionSpecification
}

// ── Reactive watchers ─────────────────────────────────────────

// When martinTileUrl changes (sort or metric switch), tell the tile source to fetch new tiles.
// martinTileUrl is a computed that rebuilds whenever activeSort or activeMetric changes.
// setTiles() tells MapLibre "the URL template changed, re-request all visible tiles".
// We also update the fill-color paint property because different sorts use different colour ramps.
watch(() => store.martinTileUrl, (newUrl) => {
  if (!map || !map.isStyleLoaded()) return
  const source = map.getSource('tiles-mvt') as maplibregl.VectorTileSource
  source.setTiles([newUrl])
  map.setPaintProperty('tiles-fill', 'fill-color', buildColorExpression())
})

// When pins data changes (after a sort switch), update the GeoJSON source with new pin features.
// The store fetches new pins whenever setActiveSort() is called. This watcher feeds that new
// GeoJSON into the map's 'pins' source, which automatically updates the cluster/unclustered layers.
watch(() => store.pins, (newPins) => {
  if (!map || !map.isStyleLoaded()) return
  const source = map.getSource('pins') as maplibregl.GeoJSONSource
  source.setData(newPins)
})

// When sidebar closes, clear selected tile highlight and reset map padding.
// The filter ['==', ['get', 'tile_id'], -1] matches no tiles (no tile has id -1),
// which effectively hides the highlight layer. Padding reset undoes the rightward shift.
watch(() => store.sidebarOpen, (isOpen) => {
  if (!map?.isStyleLoaded()) return
  if (!isOpen) {
    map.setFilter('tiles-selected', ['==', ['get', 'tile_id'], -1])
    map.easeTo({ padding: { left: 0, top: 0, bottom: 0, right: 0 }, duration: 300 })
  }
})
</script>

<style scoped>
.map-container {
  position: relative;
  width: 100%;
  height: 100%;
}

.map {
  width: 100%;
  height: 100%;
}

.map-legend {
  position: absolute;
  bottom: 36px;  /* above MapLibre attribution bar */
  left: 12px;
  z-index: 10;
}

.map-toast {
  position: absolute;
  top: 12px;
  left: 50%;
  transform: translateX(-50%);
  background: rgba(220, 53, 69, 0.9);
  color: white;
  padding: 8px 16px;
  border-radius: 6px;
  font-size: 13px;
  z-index: 50;
}
</style>

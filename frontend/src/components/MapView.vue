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
    // TODO: replace with a production tile style (OSM or Ordnance Survey Ireland)
    style: 'https://demotiles.maplibre.org/style.json',
    center: [-7.6, 53.4],  // Ireland centroid
    zoom: 6.5,
    minZoom: 5,
    maxZoom: 18,
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

  // TODO: implement full layer setup
  // Layer 2: Choropleth fill
  map.addLayer({
    id: 'tiles-fill',
    type: 'fill',
    source: 'tiles-mvt',
    'source-layer': 'tile_heatmap',
    paint: {
      'fill-color': buildColorExpression(),
      'fill-opacity': 0.65,
    },
  })

  // Layer 3: Tile grid borders
  map.addLayer({
    id: 'tiles-border',
    type: 'line',
    source: 'tiles-mvt',
    'source-layer': 'tile_heatmap',
    paint: {
      'line-color': '#ffffff',
      'line-opacity': 0.3,
      'line-width': 1,
    },
  })

  // Layer 4–6: Pin clustering
  // TODO: implement pin cluster layers (circles + labels + unclustered icons)

  // Layer 7: Hover highlight
  map.addLayer({
    id: 'tiles-hover',
    type: 'fill',
    source: 'tiles-mvt',
    'source-layer': 'tile_heatmap',
    paint: {
      'fill-color': 'transparent',
      'fill-outline-color': '#ffffff',
      'fill-opacity': 0,  // activated by filter + opacity paint expression
    },
    filter: ['==', ['get', 'tile_id'], -1],  // no tile hovered initially
  })

  // Layer 8: Selected tile highlight (white fill 0.15 + 1.5px white stroke)
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

    // TODO: if selected tile is behind sidebar, easeTo to shift view
  })

  // Click empty area: clear selection
  map.on('click', (e) => {
    const features = map!.queryRenderedFeatures(e.point, { layers: ['tiles-fill'] })
    if (!features.length) {
      store.clearSelection()
      map!.setFilter('tiles-selected', ['==', ['get', 'tile_id'], -1])
    }
  })

  // Error handling for tile source
  map.on('error', (e) => {
    if (e.sourceId === 'tiles-mvt') {
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
  // TODO: implement full interpolate expression from ramp.stops
  return [
    'interpolate', ['linear'], ['coalesce', ['get', 'value'], 0],
    ...ramp.stops.flatMap(([stop, color]) => [stop, color]),
  ] as maplibregl.ExpressionSpecification
}

// ── Reactive watchers ─────────────────────────────────────────

// When martinTileUrl changes (sort or metric switch), rebuild tile source
watch(() => store.martinTileUrl, (newUrl) => {
  if (!map || !map.isStyleLoaded()) return
  // TODO: implement — update tiles source URL and trigger re-render
  // map.getSource('tiles-mvt')?.setTiles([newUrl])
  // map.setPaintProperty('tiles-fill', 'fill-color', buildColorExpression())
})

// When pins data changes (after sort switch), update GeoJSON source
watch(() => store.pins, (newPins) => {
  if (!map || !map.isStyleLoaded()) return
  // TODO: implement
  // (map.getSource('pins') as maplibregl.GeoJSONSource)?.setData(newPins)
})

// When sidebar closes, clear selected tile highlight
watch(() => store.sidebarOpen, (isOpen) => {
  if (!isOpen && map?.isStyleLoaded()) {
    map.setFilter('tiles-selected', ['==', ['get', 'tile_id'], -1])
  }
})

// When active sort changes, update colour ramp
watch(() => store.activeSort, () => {
  if (!map || !map.isStyleLoaded()) return
  map.setPaintProperty('tiles-fill', 'fill-color', buildColorExpression())
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

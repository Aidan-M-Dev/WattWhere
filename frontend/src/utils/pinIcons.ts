/**
 * FILE: frontend/src/utils/pinIcons.ts
 * Role: Generates colored SVG pin icons for each pin type and loads them as
 *       HTMLImageElements for use with map.addImage() in MapView.vue.
 *
 * Each icon is a colored circle with a white Lucide-style icon inside.
 * Icons are generated programmatically — no sprite sheet or extra packages needed.
 */

// ── Pin type → background colour (matches previous circle colours) ─────────

export const PIN_COLORS: Record<string, string> = {
  // overall
  data_centre:           '#ff6b6b',
  ida_site:              '#ffd93d',
  // energy
  wind_farm:             '#74c476',
  transmission_node:     '#fd8d3c',
  substation:            '#e6550d',
  // environment
  sac:                   '#d73027',
  spa:                   '#fc8d59',
  nha:                   '#fee08b',
  pnha:                  '#d9ef8b',
  flood_zone:            '#4575b4',
  // cooling
  hydrometric_station:   '#6baed6',
  waterbody:             '#08519c',
  met_station:           '#9ecae1',
  // connectivity
  internet_exchange:     '#9e9ac8',
  motorway_junction:     '#bcbddc',
  broadband_area:        '#dadaeb',
  // planning
  zoning_parcel:         '#fd8d3c',
  planning_application:  '#fdae6b',
}

// ── Lucide icon inner SVG paths (24×24 coordinate space, white stroke) ──────

const ICON_PATHS: Record<string, string> = {
  server: `
    <rect width="20" height="8" x="2" y="2" rx="2"/>
    <rect width="20" height="8" x="2" y="14" rx="2"/>
    <line x1="6" x2="6.01" y1="6" y2="6"/>
    <line x1="6" x2="6.01" y1="18" y2="18"/>`,
  building: `
    <rect width="16" height="20" x="4" y="2" rx="2"/>
    <path d="M9 22v-4h6v4"/>
    <path d="M8 6h.01"/><path d="M16 6h.01"/><path d="M12 6h.01"/>
    <path d="M12 10h.01"/><path d="M8 10h.01"/>`,
  wind: `
    <path d="M17.7 7.7a2.5 2.5 0 1 1 1.8 4.3H2"/>
    <path d="M9.6 4.6A2 2 0 1 1 11 8H2"/>
    <path d="M12.6 19.4A2 2 0 1 0 14 16H2"/>`,
  zap: `<polygon points="13 2 3 14 12 14 11 22 21 10 12 10 13 2"/>`,
  circle_dot: `<circle cx="12" cy="12" r="10"/><circle cx="12" cy="12" r="1" fill="white"/>`,
  shield: `<path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/>`,
  feather: `
    <path d="M20.24 12.24a6 6 0 0 0-8.49-8.49L5 10.5V19h8.5z"/>
    <line x1="16" x2="2" y1="8" y2="22"/>
    <line x1="17.5" x2="9" y1="15" y2="15"/>`,
  tree_pine: `
    <path d="m17 14 3 3.3a1 1 0 0 1-.7 1.7H4.7a1 1 0 0 1-.7-1.7L7 14h-.3a1 1 0 0 1-.7-1.7L9 9h-.2A1 1 0 0 1 8 7.3L12 3l4 4.3A1 1 0 0 1 15.2 9H15l3 3.3a1 1 0 0 1-.7 1.7H17z"/>
    <line x1="12" x2="12" y1="22" y2="14"/>`,
  droplets: `
    <path d="M7 16.3c2.2 0 4-1.83 4-4.05 0-1.16-.57-2.26-1.71-3.19S7.29 6.75 7 5.3c-.29 1.45-1.14 2.84-2.29 3.76S3 11.1 3 12.25c0 2.22 1.8 4.05 4 4.05z"/>
    <path d="M12.56 6.6A10.97 10.97 0 0 0 14 3.02c.5 2.5 2 4.9 4 6.5s3 3.5 3 5.5a6.98 6.98 0 0 1-11.91 4.97"/>`,
  gauge: `
    <path d="m12 14 4-4"/>
    <path d="M3.34 19a10 10 0 1 1 17.32 0"/>`,
  droplet: `<path d="M12 22a7 7 0 0 0 7-7c0-2-1-3.9-3-5.5s-3.5-4-4-6.5c-.5 2.5-2 4.9-4 6.5C6 11.1 5 13 5 15a7 7 0 0 0 7 7z"/>`,
  thermometer: `<path d="M14 14.76V3.5a2.5 2.5 0 0 0-5 0v11.26a4.5 4.5 0 1 0 5 0z"/>`,
  globe: `
    <circle cx="12" cy="12" r="10"/>
    <line x1="2" x2="22" y1="12" y2="12"/>
    <path d="M12 2a15.3 15.3 0 0 1 4 10 15.3 15.3 0 0 1-4 10 15.3 15.3 0 0 1-4-10 15.3 15.3 0 0 1 4-10z"/>`,
  wifi: `
    <path d="M5 12.55a11 11 0 0 1 14.08 0"/>
    <path d="M1.42 9a16 16 0 0 1 21.16 0"/>
    <path d="M8.53 16.11a6 6 0 0 1 6.95 0"/>
    <line x1="12" x2="12.01" y1="20" y2="20"/>`,
  factory: `
    <path d="M2 20a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2V8l-7 5V8l-7 5V4a2 2 0 0 0-2-2H4a2 2 0 0 0-2 2Z"/>
    <path d="M17 18h1"/><path d="M12 18h1"/><path d="M7 18h1"/>`,
  file_text: `
    <path d="M14.5 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V7.5L14.5 2z"/>
    <polyline points="14 2 14 8 20 8"/>
    <line x1="16" x2="8" y1="13" y2="13"/>
    <line x1="16" x2="8" y1="17" y2="17"/>`,
}

// ── Pin type → icon name ─────────────────────────────────────────────────────

const PIN_TYPE_TO_ICON: Record<string, string> = {
  data_centre:          'server',
  ida_site:             'building',
  wind_farm:            'wind',
  transmission_node:    'zap',
  substation:           'circle_dot',
  sac:                  'shield',
  spa:                  'feather',
  nha:                  'tree_pine',
  pnha:                 'tree_pine',
  flood_zone:           'droplets',
  hydrometric_station:  'gauge',
  waterbody:            'droplet',
  met_station:          'thermometer',
  internet_exchange:    'globe',
  motorway_junction:    'circle_dot',
  broadband_area:       'wifi',
  zoning_parcel:        'factory',
  planning_application: 'file_text',
}

/** All known pin type strings — used to pre-load icons at map init. */
export const ALL_PIN_TYPES = Object.keys(PIN_TYPE_TO_ICON)

// ── SVG builder ──────────────────────────────────────────────────────────────

/**
 * Builds a complete SVG string for a pin type.
 * Output: a colored circle with a white Lucide icon centered inside.
 * @param pinType - pin type string (e.g. 'wind_farm')
 * @param size    - output pixel dimensions (default 32)
 */
export function buildPinSvg(pinType: string, size = 32): string {
  const color    = PIN_COLORS[pinType] ?? '#cccccc'
  const iconKey  = PIN_TYPE_TO_ICON[pinType] ?? 'circle_dot'
  const paths    = ICON_PATHS[iconKey] ?? ICON_PATHS['circle_dot']

  // The Lucide paths use a 24×24 coordinate space.
  // Scale and center them in half the icon canvas (size/2 × size/2).
  const iconAreaSize = size * 0.5   // 16px for size=32
  const offset       = (size - iconAreaSize) / 2  // 8px for size=32
  const scale        = iconAreaSize / 24

  return `<svg xmlns="http://www.w3.org/2000/svg" width="${size}" height="${size}" viewBox="0 0 ${size} ${size}">
  <circle cx="${size / 2}" cy="${size / 2}" r="${size / 2 - 1}" fill="${color}" opacity="0.92"/>
  <circle cx="${size / 2}" cy="${size / 2}" r="${size / 2 - 1}" fill="none" stroke="rgba(255,255,255,0.35)" stroke-width="1"/>
  <g transform="translate(${offset},${offset}) scale(${scale})"
     stroke="white" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" fill="none">
    ${paths}
  </g>
</svg>`
}

// ── Image loader ─────────────────────────────────────────────────────────────

/**
 * Converts an SVG string to an HTMLImageElement, ready for map.addImage().
 * Uses a data URL (more reliable than Blob URLs across all browser contexts).
 */
export function loadSvgAsImage(svgString: string, size: number): Promise<HTMLImageElement> {
  return new Promise((resolve, reject) => {
    const img = new Image(size, size)
    // encodeURIComponent handles special chars; data URLs avoid createObjectURL restrictions
    img.onload = () => resolve(img)
    img.onerror = (e) => reject(e)
    img.src = `data:image/svg+xml;charset=utf-8,${encodeURIComponent(svgString)}`
  })
}

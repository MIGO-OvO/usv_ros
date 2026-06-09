import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'

const root = resolve(import.meta.dirname, '..')
const mapSource = readFileSync(resolve(root, 'src/pages/Map.tsx'), 'utf8')
const labSource = readFileSync(resolve(root, 'src/pages/Lab.tsx'), 'utf8')
const settingsSource = readFileSync(resolve(root, 'src/pages/Settings.tsx'), 'utf8')

const requiredMapTokens = [
  'leaflet.heat',
  '/api/data/missions',
  '/api/data/mission/${selectedMission}/geojson',
  '/api/data/mission/${selectedMission}/surface',
  'surfacePayload.grid.map',
  'activeMeta?.valid_surface_point_count',
  'escapeHtml(',
  '/api/map/live?metric=${metric}&size=${idwSize}&power=${idwPower}',
  'live.surface',
  '实时污染面已生成',
  'download=true',
  'exportUrls.surface',
  'GeoJSON',
  'survey_status',
  'formatSurveyGateReason',
  '走航门控',
  '低质量/排除原因',
  'IDW size',
  'calibration_id',
  'fitToCurrentBounds',
  'tileLayerRef',
  '.redraw()',
  '适配范围',
]

const forbiddenMapTokens = [
  '/api/map/offline-mode',
  'offlineMode',
  '离线模式',
]

const requiredLabTokens = [
  'fitLabBounds',
  'lab-map-workspace',
  '适配范围',
  '110.412778',
  '25.314167',
]

const forbiddenLabTokens = [
  'max-w-7xl',
  'h-[360px]',
]

const requiredSettingsTokens = [
  'mapping_profile',
  'survey_min_distance_m',
  'survey_require_gps',
  'survey_require_valid_spectrometer',
  'saveMappingProfile',
  '走航门控配置',
]

for (const token of requiredMapTokens) {
  if (!mapSource.includes(token)) {
    throw new Error(`Map smoke failed: missing ${token}`)
  }
}

for (const token of forbiddenMapTokens) {
  if (mapSource.includes(token)) {
    throw new Error(`Map smoke failed: forbidden ${token}`)
  }
}

for (const token of requiredLabTokens) {
  if (!labSource.includes(token)) {
    throw new Error(`Lab smoke failed: missing ${token}`)
  }
}

for (const token of forbiddenLabTokens) {
  if (labSource.includes(token)) {
    throw new Error(`Lab smoke failed: forbidden ${token}`)
  }
}

for (const token of requiredSettingsTokens) {
  if (!settingsSource.includes(token)) {
    throw new Error(`Settings smoke failed: missing ${token}`)
  }
}

const fixture = {
  geojson: {
    type: 'FeatureCollection',
    meta: {
      metric_label: 'COD',
      unit: 'mg/L',
      pollutant_name: 'COD',
      calibration_id: 'cal-smoke',
      valid_surface_point_count: 3,
      excluded_reasons: { missing_gps: 1 },
      idw: { size: 50, power: 2 },
    },
    features: [
      { type: 'Feature', geometry: { type: 'LineString', coordinates: [[120, 30], [120.001, 30.001]] }, properties: { layer: 'route' } },
      { type: 'Feature', geometry: { type: 'Point', coordinates: [120, 30] }, properties: { layer: 'sample', value: 0.2, pollutant_name: 'COD', quality_flags: '', valid_for_surface: true } },
    ],
  },
  surface: {
    valid: true,
    metric: 'concentration',
    min: 0.2,
    max: 0.6,
    meta: {
      metric_label: 'COD',
      unit: 'mg/L',
      pollutant_name: 'COD',
      calibration_id: 'cal-smoke',
      valid_surface_point_count: 3,
      excluded_count: 1,
      excluded_reasons: { missing_gps: 1 },
      idw: { size: 50, power: 2 },
    },
    grid: [
      { lng: 120.0, lat: 30.0, value: 0.2 },
      { lng: 120.001, lat: 30.001, value: 0.6 },
    ],
  },
}

const samples = fixture.geojson.features.filter((feature) => feature.properties?.layer === 'sample')
if (samples.length === 0) {
  throw new Error('Map smoke failed: fixture has no samples')
}
if (!fixture.surface.valid || fixture.surface.grid.length < 2) {
  throw new Error('Map smoke failed: fixture surface is not renderable')
}
if (fixture.geojson.meta.calibration_id !== fixture.surface.meta.calibration_id) {
  throw new Error('Map smoke failed: fixture calibration metadata mismatch')
}
if (fixture.surface.meta.valid_surface_point_count < 3 || !fixture.surface.meta.idw.size) {
  throw new Error('Map smoke failed: fixture surface meta is incomplete')
}
if (fixture.surface.grid.some((point) => !Number.isFinite(point.lat) || !Number.isFinite(point.lng) || !Number.isFinite(point.value))) {
  throw new Error('Map smoke failed: fixture surface has non-finite grid point')
}

console.log('pollution map smoke ok')

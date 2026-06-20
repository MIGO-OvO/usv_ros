import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import L from 'leaflet'
import 'leaflet/dist/leaflet.css'
import 'leaflet.heat'
import { Activity, AlertTriangle, Database, Download, Layers, Loader2, MapPinned, Navigation, RefreshCw, Route, Trash2, Upload, Wifi, WifiOff, X } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { gcj02Input } from '@/lib/lab-coordinate-adapter'
import { cn } from '@/lib/utils'
import { useAppStore } from '@/store'

type MapMode = 'live' | 'history'
type MetricMode = 'auto' | 'concentration' | 'absorbance' | 'voltage'
type TileStyle = 'satellite' | 'annotation'

interface MapConfig {
  enabled: boolean
  provider: string
  tile_url: string
  styles: TileStyle[]
  default_style: TileStyle
  min_zoom: number
  max_zoom: number
  default_center: { lng: number; lat: number }
  default_zoom: number
  prewarm_zoom: { min: number; max: number }
}

interface PrewarmStatus {
  running: boolean
  total: number
  done: number
  failed: number
  zoom: number
  stopped: boolean
}

interface CacheStats {
  tiles: number
  bytes: number
}

interface MissionMeta {
  id: string
  name: string
  start_time: string
  point_count: number
  valid_surface_point_count?: number
  pollutant_name?: string | null
  unit?: string | null
  surface_ready?: boolean
}

interface GeoFeature {
  type: 'Feature'
  geometry: {
    type: 'Point' | 'LineString'
    coordinates: [number, number] | [number, number][]
  }
  properties: Record<string, unknown>
}

interface GeoJsonPayload {
  type: 'FeatureCollection'
  features: GeoFeature[]
  meta?: MapMeta
  properties?: Record<string, unknown>
}

interface SurfacePoint {
  lng: number
  lat: number
  value: number
}

interface SurfacePayload {
  valid: boolean
  reason: string
  metric: string
  grid: SurfacePoint[]
  min?: number
  max?: number
  point_count?: number
  excluded_count?: number
  excluded_reasons?: Record<string, number>
  metric_label?: string
  unit?: string
  size?: number
  power?: number
  meta?: MapMeta
  water_area?: {
    enabled: boolean
    polygon: Array<{ lat: number; lng: number }>
  }
}

interface MappingProfileConfig {
  survey_min_distance_m?: number
  survey_min_speed_mps?: number
  survey_max_speed_mps?: number
  survey_require_valid_spectrometer?: boolean
  survey_require_gps?: boolean
  survey_max_position_age_s?: number
}

interface SurveyGateStatus {
  status: string
  reason: string
  reason_label?: string
  skipped?: boolean
  received_at?: string | null
}

interface SurveyStatus {
  mission_status?: string
  surveying?: boolean
  trigger_status?: { status?: string; received_at?: string | null }
  last_gate?: SurveyGateStatus | null
  last_sample_done_at?: string | null
  mapping_profile?: MappingProfileConfig
}

interface MapMeta {
  metric?: string
  metric_label?: string
  unit?: string
  pollutant_name?: string | null
  calibration_id?: string | null
  valid_surface_point_count?: number
  excluded_count?: number
  excluded_reasons?: Record<string, number>
  include_lab?: boolean
  point_count?: number
  min?: number | null
  max?: number | null
  idw?: {
    size?: number
    power?: number
  }
}

interface MapCoordinate {
  lat: number
  lng: number
}

interface GeoPoint {
  gcj02: MapCoordinate
}

interface LiveSample extends GeoPoint {
  voltage?: number
  absorbance?: number
  concentration?: number | null
  metric_used?: string
  waypoint_seq?: number
}

interface LiveRouteWaypoint extends GeoPoint {
  seq?: number
}

interface LivePayload {
  position?: GeoPoint
  track_points?: GeoPoint[]
  route_waypoints?: LiveRouteWaypoint[]
  data_points?: LiveSample[]
  surface?: SurfacePayload | null
  survey_status?: SurveyStatus | null
  mapping_profile?: MappingProfileConfig
  mission_status?: string
  automation_running?: boolean
}

interface MissionDraftWaypoint {
  readonly gcj02: MapCoordinate
  readonly sample: boolean
}

type HeatLayer = L.Layer & {
  setLatLngs: (latlngs: Array<[number, number, number]>) => void
}

const metricLabels: Record<MetricMode, string> = {
  auto: '自动',
  concentration: '浓度',
  absorbance: '吸光度',
  voltage: '电压',
}

const sampleColors = ['#2f9e44', '#74b816', '#f59f00', '#f08c00', '#e03131']

const reasonLabels: Record<string, string> = {
  missing_gps: '缺少 GPS',
  gps_invalid: 'GPS 无效',
  spectrometer_invalid: '分光无效',
  below_min_valid: '低于量程',
  above_max_valid: '高于量程',
  lab_excluded: '实验点排除',
  non_finite_metric: '非有限值',
  missing_metric: '缺少指标',
}

const surveyGateReasonLabels: Record<string, string> = {
  no_gps: '缺少 GPS',
  gps_stale: 'GPS 过期',
  distance_too_short: '距离不足',
  speed_too_low: '速度过低',
  speed_too_high: '速度过高',
  spectrometer_invalid: '分光无效',
}

function numeric(value: unknown) {
  const n = Number(value)
  return Number.isFinite(n) ? n : null
}

function formatNumber(value: unknown, digits = 3) {
  const n = numeric(value)
  if (n === null) return '—'
  return n.toLocaleString(undefined, { maximumFractionDigits: digits })
}

function formatMetricValue(value: unknown, unit?: string | null) {
  const formatted = formatNumber(value, 4)
  return unit ? `${formatted} ${unit}` : formatted
}

function formatSurveyGateReason(gate?: SurveyGateStatus | null) {
  if (!gate) return ''
  return gate.reason_label || surveyGateReasonLabels[gate.reason] || gate.reason || gate.status
}

function escapeHtml(value: unknown) {
  return String(value ?? '').replace(/[&<>"']/g, (char) => {
    const entities: Record<string, string> = {
      '&': '&amp;',
      '<': '&lt;',
      '>': '&gt;',
      '"': '&quot;',
      "'": '&#39;',
    }
    return entities[char] || char
  })
}

function colorFor(value: number, min: number, max: number) {
  if (max <= min) return sampleColors[0]
  const idx = Math.max(0, Math.min(sampleColors.length - 1, Math.floor(((value - min) / (max - min)) * sampleColors.length)))
  return sampleColors[idx]
}

function sampleRange(features: GeoFeature[]) {
  const values = features
    .filter((f) => f.properties?.layer === 'sample')
    .map((f) => numeric(f.properties?.value))
    .filter((v): v is number => v !== null)
  if (values.length === 0) return { min: 0, max: 1 }
  return { min: Math.min(...values), max: Math.max(...values) }
}

export default function MapPage() {
  const containerRef = useRef<HTMLDivElement | null>(null)
  const mapRef = useRef<L.Map | null>(null)
  const tileLayerRef = useRef<L.TileLayer | null>(null)
  const overlaysRef = useRef<L.LayerGroup | null>(null)
  const draftLayerRef = useRef<L.LayerGroup | null>(null)
  const heatRef = useRef<HeatLayer | null>(null)
  const currentBoundsRef = useRef<L.LatLngBounds | null>(null)
  const initialFitDoneRef = useRef(false)
  const missionPlanEnabledRef = useRef(false)
  const draftSampleEnabledRef = useRef(true)
  const socket = useAppStore((state) => state.socket)
  const [mode, setMode] = useState<MapMode>(() => {
    if (typeof window === 'undefined') return 'live'
    return new URLSearchParams(window.location.search).get('mode') === 'history' ? 'history' : 'live'
  })
  const [metric, setMetric] = useState<MetricMode>('auto')
  const [mapConfig, setMapConfig] = useState<MapConfig | null>(null)
  const [mapError, setMapError] = useState('')
  const [loadingMap, setLoadingMap] = useState(false)
  const [missions, setMissions] = useState<MissionMeta[]>([])
  const [selectedMission, setSelectedMission] = useState('')
  const [includeLab, setIncludeLab] = useState(true)
  const [geojson, setGeojson] = useState<GeoJsonPayload | null>(null)
  const [surface, setSurface] = useState<SurfacePayload | null>(null)
  const [surveyStatus, setSurveyStatus] = useState<SurveyStatus | null>(null)
  const [idwSize, setIdwSize] = useState(50)
  const [idwPower, setIdwPower] = useState(2)
  const [statusText, setStatusText] = useState('等待地图数据')
  const [online, setOnline] = useState<boolean | null>(null)
  const [prewarm, setPrewarm] = useState<PrewarmStatus | null>(null)
  const [cacheStats, setCacheStats] = useState<CacheStats | null>(null)
  const [cacheMsg, setCacheMsg] = useState('')
  const [importing, setImporting] = useState(false)
  const [hasCurrentBounds, setHasCurrentBounds] = useState(false)
  const fileInputRef = useRef<HTMLInputElement | null>(null)
  const gotoMarkerRef = useRef<L.Marker | null>(null)
  const [gotoLat, setGotoLat] = useState('')
  const [gotoLng, setGotoLng] = useState('')
  const [gotoMsg, setGotoMsg] = useState('')
  const [missionPlanEnabled, setMissionPlanEnabled] = useState(false)
  const [draftSampleEnabled, setDraftSampleEnabled] = useState(true)
  const [sampleTimeoutS, setSampleTimeoutS] = useState(0)
  const [draftWaypoints, setDraftWaypoints] = useState<MissionDraftWaypoint[]>([])
  const [missionUploading, setMissionUploading] = useState(false)
  const [missionPlanMsg, setMissionPlanMsg] = useState('')

  const activeSamples = useMemo(
    () => geojson?.features.filter((f) => f.properties?.layer === 'sample') || [],
    [geojson],
  )
  const activeMeta = useMemo(() => surface?.meta || geojson?.meta || null, [geojson, surface])
  const activeRange = useMemo(() => {
    const sample = sampleRange(geojson?.features || [])
    return {
      min: numeric(activeMeta?.min) ?? numeric(surface?.min) ?? sample.min,
      max: numeric(activeMeta?.max) ?? numeric(surface?.max) ?? sample.max,
    }
  }, [activeMeta, geojson, surface])
  const excludedEntries = useMemo(
    () => Object.entries(activeMeta?.excluded_reasons || surface?.excluded_reasons || {})
      .filter(([, count]) => count > 0)
      .sort((a, b) => b[1] - a[1]),
    [activeMeta, surface],
  )
  const exportUrls = useMemo(() => {
    if (!selectedMission) return null
    const mission = encodeURIComponent(selectedMission)
    const metricParam = encodeURIComponent(metric)
    return {
      csv: `/api/data/mission/${mission}/csv`,
      geojson: `/api/data/mission/${mission}/geojson?metric=${metricParam}&include_lab=${includeLab}&download=true`,
      surface: `/api/data/mission/${mission}/surface?metric=${metricParam}&size=${idwSize}&power=${idwPower}&include_lab=${includeLab}&download=true`,
    }
  }, [idwPower, idwSize, metric, selectedMission, includeLab])

  useEffect(() => { missionPlanEnabledRef.current = missionPlanEnabled }, [missionPlanEnabled])
  useEffect(() => { draftSampleEnabledRef.current = draftSampleEnabled }, [draftSampleEnabled])

  const loadConfig = useCallback(async () => {
    const res = await fetch('/api/map/config')
    const json = await res.json()
    setMapConfig(json.data)
    if (!json.data?.enabled) setMapError('地图配置不可用')
  }, [])

  const loadCacheStats = useCallback(async () => {
    try {
      const res = await fetch('/api/map/cache/stats')
      const json = await res.json()
      if (json.success) {
        setCacheStats(json.data.cache)
        setPrewarm(json.data.prewarm)
      }
    } catch {
      /* 离线时忽略 */
    }
  }, [])

  const redrawMapTiles = useCallback(() => {
    tileLayerRef.current?.redraw()
  }, [])

  const fitToCurrentBounds = useCallback(() => {
    const map = mapRef.current
    const bounds = currentBoundsRef.current
    if (!map || !bounds?.isValid()) return
    map.fitBounds(bounds, { padding: [40, 40], maxZoom: mapConfig?.default_zoom ?? 15 })
    initialFitDoneRef.current = true
  }, [mapConfig])

  const flyToCoordinate = useCallback(() => {
    const map = mapRef.current
    if (!map) {
      setGotoMsg('地图尚未就绪')
      return
    }
    const lat = numeric(gotoLat)
    const lng = numeric(gotoLng)
    if (lat === null || lng === null || lat < -90 || lat > 90 || lng < -180 || lng > 180) {
      setGotoMsg('请输入有效经纬度 (纬度 -90~90, 经度 -180~180)')
      return
    }
    const target = { lat, lng }
    const zoom = Math.max(map.getZoom(), mapConfig?.default_zoom ?? 15)
    map.flyTo([target.lat, target.lng], zoom)
    if (gotoMarkerRef.current) {
      gotoMarkerRef.current.setLatLng([target.lat, target.lng])
    } else {
      gotoMarkerRef.current = L.marker([target.lat, target.lng], {
        icon: L.divIcon({
          className: 'usv-goto-icon',
          html: '<span>★</span>',
          iconAnchor: [0, 0],
        }),
      }).addTo(map)
    }
    gotoMarkerRef.current.bindPopup(`手动定位点<br/>${target.lat.toFixed(6)}, ${target.lng.toFixed(6)}`)
    setGotoMsg(`已跳转至 ${target.lat.toFixed(6)}, ${target.lng.toFixed(6)}`)
  }, [gotoLat, gotoLng, mapConfig])

  const importPack = useCallback(async (file: File) => {
    setImporting(true)
    setCacheMsg('')
    try {
      const form = new FormData()
      form.append('pack', file)
      const res = await fetch('/api/map/cache/import', { method: 'POST', body: form })
      const json = await res.json()
      if (json.success) {
        setCacheMsg(`导入完成: 新增 ${json.data.added} 张, 跳过 ${json.data.skipped} 张`)
        loadCacheStats()
        redrawMapTiles()
      } else {
        setCacheMsg(json.message || '导入失败')
      }
    } catch {
      setCacheMsg('导入请求失败')
    } finally {
      setImporting(false)
    }
  }, [loadCacheStats, redrawMapTiles])

  const probeOnline = useCallback(async () => {
    try {
      const res = await fetch('/api/map/ping')
      const json = await res.json()
      setOnline(Boolean(json.online))
    } catch {
      setOnline(false)
    }
  }, [])

  const loadMissions = useCallback(async () => {
    const res = await fetch('/api/data/missions')
    const json = await res.json()
    if (!json.success) return
    setMissions(json.data || [])
    if (!selectedMission && json.data?.length > 0) setSelectedMission(json.data[0].id)
  }, [selectedMission])

  const clearOverlays = useCallback(() => {
    overlaysRef.current?.clearLayers()
    heatRef.current?.setLatLngs([])
  }, [])

  const renderGeojson = useCallback((payload: GeoJsonPayload | null, surfacePayload: SurfacePayload | null) => {
    const map = mapRef.current
    const group = overlaysRef.current
    if (!map || !group || !payload) return

    clearOverlays()
    const range = sampleRange(payload.features)
    const bounds = L.latLngBounds([])

    payload.features.forEach((feature) => {
      const layer = feature.properties?.layer
      if (feature.geometry.type === 'LineString') {
        const path = feature.geometry.coordinates as [number, number][]
        const latlngs = path.map(([lng, lat]) => [lat, lng] as [number, number])
        const line = L.polyline(latlngs, {
          weight: layer === 'route' ? 4 : 3,
          color: layer === 'route' ? '#2563eb' : '#0f766e',
          opacity: layer === 'route' ? 0.75 : 0.55,
          lineJoin: 'round',
          lineCap: 'round',
        })
        line.addTo(group)
        latlngs.forEach((p) => bounds.extend(p))
        return
      }

      const [lng, lat] = feature.geometry.coordinates as [number, number]
      if (layer === 'position') {
        const marker = L.circleMarker([lat, lng], {
          radius: 9,
          fillColor: '#2563eb',
          fillOpacity: 0.95,
          color: '#ffffff',
          weight: 2,
        })
        marker.bindPopup('当前飞控定位')
        marker.addTo(group)
        bounds.extend([lat, lng])
        return
      }

      if (layer === 'waypoint') {
        const marker = L.marker([lat, lng], {
          icon: L.divIcon({
            className: 'usv-waypoint-icon',
            html: `<span>#${escapeHtml(feature.properties?.seq ?? '')}</span>`,
            iconAnchor: [0, 0],
          }),
        })
        marker.addTo(group)
        bounds.extend([lat, lng])
        return
      }

      const value = numeric(feature.properties?.value) ?? 0
      const marker = L.circleMarker([lat, lng], {
        radius: 8,
        fillColor: colorFor(value, range.min, range.max),
        fillOpacity: 0.9,
        color: '#ffffff',
        weight: 2,
      })
      const unit = String(feature.properties?.concentration_unit || payload.meta?.unit || surfacePayload?.meta?.unit || '')
      const pollutant = String(feature.properties?.pollutant_name || payload.meta?.pollutant_name || surfacePayload?.meta?.pollutant_name || metricLabels[metric])
      const qualityFlags = String(feature.properties?.quality_flags || '')
      const excludedReason = String(feature.properties?.excluded_reason || '')
      const validForSurface = feature.properties?.valid_for_surface !== false
      const surfaceStatus = validForSurface
        ? '参与'
        : `排除 ${reasonLabels[excludedReason] || excludedReason || '-'}`
      marker.bindPopup(
        `<div style="min-width:190px;font-size:12px;line-height:1.65">
          <b>${escapeHtml(pollutant)}</b><br/>
          值: ${escapeHtml(formatMetricValue(value, unit))}<br/>
          航点: ${escapeHtml(feature.properties?.waypoint_seq ?? '-')}<br/>
          质量: ${escapeHtml(qualityFlags || 'ok')}<br/>
          Surface: ${escapeHtml(surfaceStatus)}
        </div>`,
      )
      marker.addTo(group)
      bounds.extend([lat, lng])
    })

    if (surfacePayload?.valid && surfacePayload.grid.length > 0) {
      const max = surfacePayload.max || Math.max(...surfacePayload.grid.map((p) => p.value))
      const safeMax = max > 0 ? max : 1
      heatRef.current?.setLatLngs(
        surfacePayload.grid.map((p) => [p.lat, p.lng, p.value / safeMax] as [number, number, number]),
      )
    }

    if (surfacePayload?.water_area?.enabled && surfacePayload.water_area.polygon && surfacePayload.water_area.polygon.length >= 3) {
      const wpts: Array<[number, number]> = surfacePayload.water_area.polygon.map((p: { lat: number; lng: number }) => [p.lat, p.lng])
      L.polygon(wpts, {
        color: '#16a34a',
        fillColor: '#16a34a',
        fillOpacity: 0.1,
        weight: 1.5,
        dashArray: '4, 4',
      }).addTo(group)
    }

    if (bounds.isValid()) {
      currentBoundsRef.current = bounds
      setHasCurrentBounds(true)
      if (!initialFitDoneRef.current) {
        map.fitBounds(bounds, { padding: [40, 40], maxZoom: mapConfig?.default_zoom ?? 15 })
        initialFitDoneRef.current = true
      }
    } else {
      currentBoundsRef.current = null
      setHasCurrentBounds(false)
    }
  }, [clearOverlays, mapConfig, metric])

  const loadLive = useCallback(async () => {
    const res = await fetch(`/api/map/live?metric=${metric}&size=${idwSize}&power=${idwPower}&include_lab=${includeLab}`)
    const json = await res.json()
    const live = (json.data || {}) as LivePayload
    const liveSurface = live.surface || null
    const liveSurveyStatus = live.survey_status || null
    const features: GeoFeature[] = []
    if (live.position?.gcj02) {
      features.push({
        type: 'Feature',
        geometry: { type: 'Point', coordinates: [live.position.gcj02.lng, live.position.gcj02.lat] },
        properties: { layer: 'position' },
      })
    }
    const trackPoints = live.track_points || []
    if (trackPoints.length > 1) {
      const coordinates: [number, number][] = trackPoints.map((p) => [p.gcj02.lng, p.gcj02.lat])
      features.push({
        type: 'Feature',
        geometry: {
          type: 'LineString',
          coordinates,
        },
        properties: { layer: 'track' },
      })
    }
    live.route_waypoints?.forEach((wp) => {
      features.push({
        type: 'Feature',
        geometry: { type: 'Point', coordinates: [wp.gcj02.lng, wp.gcj02.lat] },
        properties: { layer: 'waypoint', seq: wp.seq },
      })
    })
    live.data_points?.forEach((point) => {
      if (!point.gcj02) return
      const value = metric === 'voltage'
        ? point.voltage
        : metric === 'absorbance'
          ? point.absorbance
          : point.concentration ?? point.absorbance
      if (!Number.isFinite(Number(value))) return
      features.push({
        type: 'Feature',
        geometry: { type: 'Point', coordinates: [point.gcj02.lng, point.gcj02.lat] },
        properties: { ...point, layer: 'sample', value },
      })
    })
    setGeojson({ type: 'FeatureCollection', features, meta: liveSurface?.meta })
    setSurface(liveSurface)
    setSurveyStatus(liveSurveyStatus)
    const gateText = formatSurveyGateReason(liveSurveyStatus?.last_gate)
    if (liveSurface?.valid) {
      setStatusText(gateText ? `实时污染面已生成 · 走航门控: ${gateText}` : '实时污染面已生成')
    } else if (liveSurface?.reason) {
      setStatusText(gateText ? `${liveSurface.reason} · 走航门控: ${gateText}` : liveSurface.reason)
    } else {
      const positionText = live.position ? `实时船位 ${live.position.gcj02.lat.toFixed(6)}, ${live.position.gcj02.lng.toFixed(6)}` : '等待 GPS 船位'
      setStatusText(gateText ? `${positionText} · 走航门控: ${gateText}` : positionText)
    }
  }, [idwPower, idwSize, metric, includeLab])

  const loadHistory = useCallback(async () => {
    if (!selectedMission) return
    const [geoRes, surfaceRes] = await Promise.all([
      fetch(`/api/data/mission/${selectedMission}/geojson?metric=${metric}&include_lab=${includeLab}`),
      fetch(`/api/data/mission/${selectedMission}/surface?metric=${metric}&size=${idwSize}&power=${idwPower}&include_lab=${includeLab}`),
    ])
    const geo = await geoRes.json()
    const surfaceJson = await surfaceRes.json()
    setGeojson(geo.data)
    setSurface(surfaceJson.data)
    setStatusText(surfaceJson.data?.valid ? '历史污染面已生成' : surfaceJson.data?.reason || '历史任务已加载')
  }, [idwPower, idwSize, metric, selectedMission, includeLab])

  const removeDraftWaypoint = useCallback((index: number) => {
    setDraftWaypoints((current) => current.filter((_, itemIndex) => itemIndex !== index))
  }, [])

  const clearDraftWaypoints = useCallback(() => {
    setDraftWaypoints([])
    setMissionPlanMsg('')
  }, [])

  const uploadDraftMission = useCallback(async () => {
    if (draftWaypoints.length === 0) {
      setMissionPlanMsg('请先在地图上添加航点')
      return
    }
    setMissionUploading(true)
    setMissionPlanMsg('')
    try {
      const res = await fetch('/api/mission/plan/upload', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          replace: true,
          sample_timeout_s: sampleTimeoutS,
          waypoints: draftWaypoints.map((wp, index) => ({
            ...gcj02Input(wp.gcj02),
            seq: index,
            sample: wp.sample,
          })),
        }),
      })
      const json = await res.json()
      if (json.success) {
        const data = json.data || {}
        setMissionPlanMsg(`已上传 ${data.nav_waypoint_count || draftWaypoints.length} 个航点，任务项 ${data.mission_items || 0} 个；未启动 AUTO`)
        setDraftWaypoints([])
        await loadLive()
      } else {
        setMissionPlanMsg(json.message || '上传失败')
      }
    } catch {
      setMissionPlanMsg('上传请求失败')
    } finally {
      setMissionUploading(false)
    }
  }, [draftWaypoints, loadLive, sampleTimeoutS])

  const startPrewarm = useCallback(async () => {
    const map = mapRef.current
    let bbox: Record<string, number> | undefined
    if (map) {
      const b = map.getBounds()
      bbox = {
        min_lng: b.getWest(), min_lat: b.getSouth(),
        max_lng: b.getEast(), max_lat: b.getNorth(),
      }
    }
    setCacheMsg('')
    try {
      const res = await fetch('/api/map/cache/prewarm', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ bbox }),
      })
      const json = await res.json()
      setCacheMsg(json.message || '')
      if (json.success && json.data) {
        setPrewarm({ running: true, total: json.data.total, done: 0, failed: 0, zoom: json.data.zoom_min, stopped: false })
      }
    } catch {
      setCacheMsg('预热请求失败')
    }
  }, [])

  const stopPrewarm = useCallback(async () => {
    await fetch('/api/map/cache/prewarm/stop', { method: 'POST' })
  }, [])

  const clearCache = useCallback(async () => {
    const res = await fetch('/api/map/cache/clear', { method: 'POST' })
    const json = await res.json()
    setCacheMsg(json.message || '')
    loadCacheStats()
    redrawMapTiles()
  }, [loadCacheStats, redrawMapTiles])

  useEffect(() => {
    loadConfig()
    loadMissions()
    loadCacheStats()
    probeOnline()
    const timer = window.setInterval(probeOnline, 15000)
    return () => window.clearInterval(timer)
  }, [loadConfig, loadMissions, loadCacheStats, probeOnline])

  useEffect(() => {
    if (!mapConfig?.enabled || !containerRef.current || mapRef.current) return
    setLoadingMap(true)
    try {
      const map = L.map(containerRef.current, {
        center: [mapConfig.default_center.lat, mapConfig.default_center.lng],
        zoom: mapConfig.default_zoom,
        zoomControl: true,
        attributionControl: false,
      })
      const tileTpl = mapConfig.tile_url
      const tileLayer = L.tileLayer(tileTpl.replace('{style}', mapConfig.default_style), {
        minZoom: mapConfig.min_zoom,
        maxZoom: mapConfig.max_zoom,
      })
      tileLayer.addTo(map)
      tileLayerRef.current = tileLayer
      overlaysRef.current = L.layerGroup().addTo(map)
      draftLayerRef.current = L.layerGroup().addTo(map)
      heatRef.current = (L as unknown as { heatLayer: (pts: Array<[number, number, number]>, opts: Record<string, unknown>) => HeatLayer }).heatLayer([], {
        radius: 30,
        blur: 18,
        gradient: { 0.2: '#2f9e44', 0.45: '#74b816', 0.65: '#f59f00', 0.82: '#f08c00', 1.0: '#e03131' },
      }).addTo(map)
      map.on('click', (event: L.LeafletMouseEvent) => {
        if (!missionPlanEnabledRef.current) return
        const point = {
          gcj02: {
            lat: event.latlng.lat,
            lng: event.latlng.lng,
          },
          sample: draftSampleEnabledRef.current,
        }
        setDraftWaypoints((current) => {
          const next = [...current, point]
          setMissionPlanMsg(`已添加 ${next.length} 个航点`)
          return next
        })
      })
      mapRef.current = map
      setMapError('')
    } catch (err) {
      console.error(err)
      setMapError('地图初始化失败')
    } finally {
      setLoadingMap(false)
    }
  }, [mapConfig])

  useEffect(() => {
    if (!socket) return
    const onProgress = (status: PrewarmStatus) => {
      setPrewarm(status)
      if (!status.running) {
        loadCacheStats()
        redrawMapTiles()
      }
    }
    socket.on('map_prewarm_progress', onProgress)
    return () => {
      socket.off('map_prewarm_progress', onProgress)
    }
  }, [socket, loadCacheStats, redrawMapTiles])

  useEffect(() => {
    if (mode === 'live') {
      loadLive()
      const timer = window.setInterval(loadLive, 2500)
      return () => window.clearInterval(timer)
    }
    loadHistory()
  }, [loadHistory, loadLive, mode])

  useEffect(() => {
    renderGeojson(geojson, surface)
  }, [geojson, renderGeojson, surface])

  useEffect(() => {
    const group = draftLayerRef.current
    if (!group) return
    group.clearLayers()
    if (draftWaypoints.length === 0) return
    const latlngs = draftWaypoints.map((wp) => [wp.gcj02.lat, wp.gcj02.lng] as [number, number])
    if (latlngs.length > 1) {
      L.polyline(latlngs, {
        color: '#dc2626',
        weight: 3,
        opacity: 0.85,
        dashArray: '6 6',
      }).addTo(group)
    }
    draftWaypoints.forEach((wp, index) => {
      const marker = L.marker([wp.gcj02.lat, wp.gcj02.lng], {
        icon: L.divIcon({
          className: 'usv-draft-waypoint-icon',
          html: `<span>${index + 1}${wp.sample ? 'S' : ''}</span>`,
          iconAnchor: [0, 0],
        }),
      })
      marker.bindPopup(
        `<div style="min-width:180px;font-size:12px;line-height:1.65">
          <b>Web 规划航点 #${index + 1}</b><br/>
          GCJ-02: ${wp.gcj02.lat.toFixed(7)}, ${wp.gcj02.lng.toFixed(7)}<br/>
          采样触发: ${wp.sample ? 'NAV_SCRIPT_TIME' : '无'}
        </div>`,
      )
      marker.addTo(group)
    })
  }, [draftWaypoints])

  return (
    <div className="min-h-[calc(100vh-5rem)] xl:h-screen p-3 md:p-5 flex flex-col gap-3 bg-muted/20">
      <header className="flex flex-col md:flex-row md:items-center md:justify-between gap-3 shrink-0">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">水域污染地图</h1>
          <p className="text-sm text-muted-foreground">{statusText}</p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <div className="inline-flex rounded-md border bg-background p-1">
            {(['live', 'history'] as MapMode[]).map((item) => (
              <Button key={item} size="sm" variant={mode === item ? 'default' : 'ghost'} onClick={() => setMode(item)}>
                {item === 'live' ? <Navigation className="w-4 h-4 mr-1" /> : <Database className="w-4 h-4 mr-1" />}
                {item === 'live' ? '实时' : '历史'}
              </Button>
            ))}
          </div>
          <label className="flex items-center gap-2 text-sm bg-background border px-3 h-9 rounded-md select-none cursor-pointer">
            <input type="checkbox" checked={includeLab} onChange={(e) => setIncludeLab(e.target.checked)} />
            <span>包含实验室数据</span>
          </label>
          <select className="h-9 rounded-md border border-input bg-background px-3 text-sm" value={metric} onChange={(e) => setMetric(e.target.value as MetricMode)}>
            {Object.entries(metricLabels).map(([key, label]) => <option key={key} value={key}>{label}</option>)}
          </select>
          <Button variant="outline" size="sm" onClick={() => mode === 'live' ? loadLive() : loadHistory()}>
            <RefreshCw className="w-4 h-4 mr-1" />
            刷新
          </Button>
          <Button variant="outline" size="sm" onClick={fitToCurrentBounds} disabled={!hasCurrentBounds}>
            <MapPinned className="w-4 h-4 mr-1" />
            适配范围
          </Button>
        </div>
      </header>

      <div className="grid grid-cols-1 xl:grid-cols-[320px_minmax(0,1fr)] gap-3 xl:flex-1 xl:min-h-0">
        <aside className="space-y-3 xl:min-h-0 xl:overflow-auto">
          {mode === 'live' && (
            <Card>
              <CardHeader className="pb-2">
                <div className="flex items-center justify-between gap-2">
                  <CardTitle className="text-base flex items-center gap-2"><Route className="w-4 h-4" />Web 航线规划</CardTitle>
                  <Button
                    size="sm"
                    variant={missionPlanEnabled ? 'default' : 'outline'}
                    onClick={() => setMissionPlanEnabled((value) => !value)}
                  >
                    <MapPinned className="w-4 h-4 mr-1" />
                    {missionPlanEnabled ? '落点中' : '落点'}
                  </Button>
                </div>
              </CardHeader>
              <CardContent className="space-y-3 text-sm">
                <label className="flex items-center justify-between rounded-md border bg-background px-3 py-2">
                  <span className="text-muted-foreground">新航点采样</span>
                  <input
                    type="checkbox"
                    className="h-4 w-4"
                    checked={draftSampleEnabled}
                    onChange={(event) => setDraftSampleEnabled(event.target.checked)}
                  />
                </label>
                <div className="space-y-2">
                  <span className="text-muted-foreground block text-xs">采样超时(秒，0代表禁用)</span>
                  <input
                    type="number"
                    min={0}
                    max={3600}
                    className="w-full h-9 rounded-md border border-input bg-background px-3 py-1 text-sm font-medium"
                    value={sampleTimeoutS}
                    onChange={(event) => setSampleTimeoutS(Number(event.target.value) || 0)}
                  />
                </div>
                <div className="grid grid-cols-2 gap-2">
                  <Button size="sm" onClick={uploadDraftMission} disabled={missionUploading || draftWaypoints.length === 0}>
                    {missionUploading ? <Loader2 className="w-4 h-4 mr-1 animate-spin" /> : <Upload className="w-4 h-4 mr-1" />}
                    上传航线
                  </Button>
                  <Button size="sm" variant="outline" onClick={clearDraftWaypoints} disabled={draftWaypoints.length === 0}>
                    <Trash2 className="w-4 h-4 mr-1" />
                    清空
                  </Button>
                </div>
                <div className="space-y-2">
                  {draftWaypoints.length === 0 ? (
                    <div className="rounded-md border border-dashed bg-muted/30 px-3 py-2 text-xs text-muted-foreground">
                      草案航点 0
                    </div>
                  ) : draftWaypoints.map((wp, index) => (
                    <div key={`${wp.gcj02.lat}-${wp.gcj02.lng}-${index}`} className="grid grid-cols-[1fr_auto] gap-2 rounded-md border bg-background px-3 py-2">
                      <div className="min-w-0">
                        <div className="flex items-center gap-2">
                          <span className="font-medium">#{index + 1}</span>
                          {wp.sample && <span className="rounded bg-emerald-500/10 px-1.5 py-0.5 text-[11px] font-medium text-emerald-700 dark:text-emerald-300">采样</span>}
                        </div>
                        <div className="mt-1 truncate font-mono text-[11px] text-muted-foreground">
                          {wp.gcj02.lat.toFixed(6)}, {wp.gcj02.lng.toFixed(6)}
                        </div>
                      </div>
                      <Button size="icon" variant="ghost" className="h-7 w-7 text-destructive" onClick={() => removeDraftWaypoint(index)}>
                        <Trash2 className="h-3.5 w-3.5" />
                      </Button>
                    </div>
                  ))}
                </div>
                {missionPlanMsg && <div className="rounded-md bg-muted px-3 py-2 text-xs">{missionPlanMsg}</div>}
                <div className="text-xs text-muted-foreground">仅写入飞控 mission；不解锁、不切 AUTO。</div>
              </CardContent>
            </Card>
          )}

          {mode === 'history' && (
            <Card>
              <CardHeader className="pb-2">
                <CardTitle className="text-base flex items-center gap-2"><Route className="w-4 h-4" />历史任务</CardTitle>
              </CardHeader>
              <CardContent>
                <select className="h-10 w-full rounded-md border border-input bg-background px-3 text-sm" value={selectedMission} onChange={(e) => setSelectedMission(e.target.value)}>
                  {missions.map((mission) => (
                    <option key={mission.id} value={mission.id}>{mission.name || mission.id}</option>
                  ))}
                </select>
                <div className="mt-3 grid grid-cols-2 gap-2">
                  <label className="space-y-1 text-xs text-muted-foreground">
                    <span>IDW size</span>
                    <input
                      type="number"
                      min={3}
                      max={120}
                      value={idwSize}
                      onChange={(e) => setIdwSize(Math.max(3, Number(e.target.value) || 3))}
                      className="h-9 w-full rounded-md border border-input bg-background px-2 text-sm text-foreground"
                    />
                  </label>
                  <label className="space-y-1 text-xs text-muted-foreground">
                    <span>IDW power</span>
                    <input
                      type="number"
                      min={0.5}
                      max={5}
                      step={0.1}
                      value={idwPower}
                      onChange={(e) => setIdwPower(Math.max(0.5, Math.min(5, Number(e.target.value) || 2)))}
                      className="h-9 w-full rounded-md border border-input bg-background px-2 text-sm text-foreground"
                    />
                  </label>
                </div>
                {exportUrls && (
                  <div className="mt-3 grid grid-cols-3 gap-2">
                    <Button asChild variant="outline" size="sm" className="gap-1 px-2 text-xs">
                      <a href={exportUrls.csv} download title="导出 CSV">
                        <Download className="h-3.5 w-3.5" />
                        CSV
                      </a>
                    </Button>
                    <Button asChild variant="outline" size="sm" className="gap-1 px-2 text-xs">
                      <a href={exportUrls.geojson} download title="导出 GeoJSON">
                        <Download className="h-3.5 w-3.5" />
                        GeoJSON
                      </a>
                    </Button>
                    <Button asChild variant="outline" size="sm" className="gap-1 px-2 text-xs">
                      <a href={exportUrls.surface} download title="导出 surface JSON">
                        <Download className="h-3.5 w-3.5" />
                        Surface
                      </a>
                    </Button>
                  </div>
                )}
                <div className="mt-3 space-y-2">
                  {missions.slice(0, 6).map((mission) => (
                    <button
                      key={mission.id}
                      className={cn(
                        'w-full text-left rounded-md border px-3 py-2 text-sm transition-colors',
                        selectedMission === mission.id ? 'border-primary bg-primary/10' : 'border-border hover:bg-muted',
                      )}
                      onClick={() => setSelectedMission(mission.id)}
                    >
                      <div className="font-medium truncate">{mission.name || mission.id}</div>
                      <div className="text-xs text-muted-foreground">{mission.point_count} 点 · {mission.valid_surface_point_count ?? '—'} 有效 · {new Date(mission.start_time).toLocaleString()}</div>
                      {(mission.pollutant_name || mission.surface_ready !== undefined) && (
                        <div className="mt-1 flex items-center justify-between text-xs">
                          <span className="text-muted-foreground truncate">{mission.pollutant_name || '污染物'} {mission.unit || ''}</span>
                          <span className={cn('font-medium', mission.surface_ready ? 'text-emerald-600' : 'text-amber-600')}>
                            {mission.surface_ready ? '可成面' : '样本不足'}
                          </span>
                        </div>
                      )}
                    </button>
                  ))}
                </div>
              </CardContent>
            </Card>
          )}

          <Card>
            <CardHeader className="pb-2">
              <CardTitle className="text-base flex items-center gap-2"><Activity className="w-4 h-4" />走航门控</CardTitle>
            </CardHeader>
            <CardContent className="space-y-3 text-sm">
              <div className="grid grid-cols-2 gap-2">
                <div>
                  <div className="text-xs text-muted-foreground">任务状态</div>
                  <div className="font-mono text-xs truncate">{surveyStatus?.mission_status || 'IDLE'}</div>
                </div>
                <div>
                  <div className="text-xs text-muted-foreground">最近采样</div>
                  <div className="font-mono text-xs truncate">
                    {surveyStatus?.last_sample_done_at ? new Date(surveyStatus.last_sample_done_at).toLocaleTimeString() : '—'}
                  </div>
                </div>
              </div>
              <div className={cn(
                'rounded-md border px-3 py-2',
                surveyStatus?.last_gate ? 'border-amber-500/40 bg-amber-500/10' : 'border-border bg-muted/20',
              )}>
                <div className="text-xs text-muted-foreground">最近门控</div>
                <div className="mt-1 font-medium">
                  {surveyStatus?.last_gate ? formatSurveyGateReason(surveyStatus.last_gate) : '暂无跳过'}
                </div>
                {surveyStatus?.last_gate?.received_at && (
                  <div className="mt-1 text-xs text-muted-foreground">
                    {new Date(surveyStatus.last_gate.received_at).toLocaleString()}
                  </div>
                )}
              </div>
              <div className="grid grid-cols-2 gap-x-3 gap-y-1 text-xs text-muted-foreground">
                <span>最小距离</span>
                <span className="text-right font-mono">{formatNumber(surveyStatus?.mapping_profile?.survey_min_distance_m, 2)} m</span>
                <span>速度范围</span>
                <span className="text-right font-mono">
                  {formatNumber(surveyStatus?.mapping_profile?.survey_min_speed_mps, 2)}-{formatNumber(surveyStatus?.mapping_profile?.survey_max_speed_mps, 2)} m/s
                </span>
                <span>GPS / 分光</span>
                <span className="text-right">
                  {surveyStatus?.mapping_profile?.survey_require_gps ? 'GPS' : 'GPS 可选'} · {surveyStatus?.mapping_profile?.survey_require_valid_spectrometer ? 'valid' : 'valid 可选'}
                </span>
              </div>
            </CardContent>
          </Card>

          <Card>
            <CardHeader className="pb-2">
              <CardTitle className="text-base flex items-center gap-2"><MapPinned className="w-4 h-4" />定位跳转</CardTitle>
            </CardHeader>
            <CardContent className="space-y-3 text-sm">
              <div className="grid grid-cols-2 gap-2">
                <label className="space-y-1">
                  <span className="text-xs text-muted-foreground">纬度 Lat</span>
                  <input
                    type="number"
                    inputMode="decimal"
                    step="any"
                    placeholder="25.0"
                    value={gotoLat}
                    onChange={(e) => setGotoLat(e.target.value)}
                    className="h-9 w-full rounded-md border border-input bg-background px-2 text-sm"
                  />
                </label>
                <label className="space-y-1">
                  <span className="text-xs text-muted-foreground">经度 Lng</span>
                  <input
                    type="number"
                    inputMode="decimal"
                    step="any"
                    placeholder="115.0"
                    value={gotoLng}
                    onChange={(e) => setGotoLng(e.target.value)}
                    className="h-9 w-full rounded-md border border-input bg-background px-2 text-sm"
                  />
                </label>
              </div>
              <div className="flex items-center justify-between">
                <span className="text-muted-foreground">坐标系</span>
                <span className="font-medium">GCJ-02</span>
              </div>
              <Button size="sm" className="w-full" onClick={flyToCoordinate} disabled={!mapConfig?.enabled}>
                <Navigation className="w-4 h-4 mr-1" />跳转到坐标
              </Button>
              {gotoMsg && <div className="rounded-md bg-muted p-2 text-xs">{gotoMsg}</div>}
              <p className="text-xs text-muted-foreground">无 GPS 信号时手动跳转到作业区；此处只接收图面 GCJ-02 坐标。</p>
            </CardContent>
          </Card>

          <Card>
            <CardHeader className="pb-2">
              <CardTitle className="text-base flex items-center gap-2"><Layers className="w-4 h-4" />图层状态</CardTitle>
            </CardHeader>
            <CardContent className="space-y-3 text-sm">
              <div className="flex justify-between"><span className="text-muted-foreground">采样点</span><span className="font-medium">{activeSamples.length}</span></div>
              <div className="flex justify-between"><span className="text-muted-foreground">指标</span><span className="font-medium">{activeMeta?.metric_label || metricLabels[metric]}</span></div>
              <div className="flex justify-between"><span className="text-muted-foreground">单位</span><span className="font-medium">{activeMeta?.unit || '—'}</span></div>
              <div className="flex justify-between"><span className="text-muted-foreground">污染物</span><span className="font-medium truncate max-w-[160px]">{activeMeta?.pollutant_name || '—'}</span></div>
              <div className="flex justify-between"><span className="text-muted-foreground">校准</span><span className="font-mono text-xs truncate max-w-[150px]">{activeMeta?.calibration_id || '—'}</span></div>
              <div className="grid grid-cols-2 gap-2">
                <div className="rounded-md border bg-background px-2 py-1.5">
                  <div className="text-xs text-muted-foreground">有效点</div>
                  <div className="font-semibold">{activeMeta?.valid_surface_point_count ?? surface?.point_count ?? '—'}</div>
                </div>
                <div className="rounded-md border bg-background px-2 py-1.5">
                  <div className="text-xs text-muted-foreground">排除点</div>
                  <div className="font-semibold">{activeMeta?.excluded_count ?? surface?.excluded_count ?? 0}</div>
                </div>
              </div>
              <div className="flex justify-between"><span className="text-muted-foreground">IDW</span><span className="font-medium">{surface?.valid ? `${surface.grid.length} 格` : '未生成'}</span></div>
              <div className="flex justify-between text-xs"><span className="text-muted-foreground">参数</span><span className="font-mono">size {activeMeta?.idw?.size ?? surface?.size ?? idwSize} · p {formatNumber(activeMeta?.idw?.power ?? surface?.power ?? idwPower, 1)}</span></div>
              <div className="pt-1">
                <div className="h-2 rounded-full" style={{ background: `linear-gradient(90deg, ${sampleColors.join(',')})` }} />
                <div className="mt-1 flex justify-between text-[11px] text-muted-foreground">
                  <span>{formatMetricValue(activeRange.min, activeMeta?.unit)}</span>
                  <span>{formatMetricValue((activeRange.min + activeRange.max) / 2, activeMeta?.unit)}</span>
                  <span>{formatMetricValue(activeRange.max, activeMeta?.unit)}</span>
                </div>
              </div>
              {excludedEntries.length > 0 && (
                <div className="rounded-md border bg-background p-2">
                  <div className="mb-1 text-xs font-medium text-muted-foreground">低质量/排除原因</div>
                  <div className="space-y-1">
                    {excludedEntries.slice(0, 5).map(([reason, count]) => (
                      <div key={reason} className="flex justify-between text-xs">
                        <span>{reasonLabels[reason] || reason}</span>
                        <span className="font-medium">{count}</span>
                      </div>
                    ))}
                  </div>
                </div>
              )}
              {surface && !surface.valid && <div className="rounded-md bg-amber-500/10 p-2 text-xs text-amber-700 dark:text-amber-300">{surface.reason}</div>}
            </CardContent>
          </Card>

          <Card>
            <CardHeader className="pb-2">
              <CardTitle className="text-base flex items-center gap-2"><Download className="w-4 h-4" />离线缓存</CardTitle>
            </CardHeader>
            <CardContent className="space-y-3 text-sm">
              <div className="flex items-center justify-between">
                <span className="text-muted-foreground">网络</span>
                <span className={cn('inline-flex items-center gap-1 font-medium',
                  online === false ? 'text-amber-600' : online ? 'text-emerald-600' : 'text-muted-foreground')}>
                  {online === false ? <WifiOff className="w-4 h-4" /> : <Wifi className="w-4 h-4" />}
                  {online === null ? '检测中' : online ? '在线' : '离线'}
                </span>
              </div>
              <div className="flex justify-between">
                <span className="text-muted-foreground">已缓存瓦片</span>
                <span className="font-medium">{cacheStats ? `${cacheStats.tiles} 张 · ${(cacheStats.bytes / 1048576).toFixed(1)} MB` : '—'}</span>
              </div>
              {prewarm?.running ? (
                <div className="space-y-2">
                  <div className="h-2 w-full rounded-full bg-muted overflow-hidden">
                    <div className="h-full bg-primary transition-all"
                      style={{ width: `${prewarm.total ? Math.round((prewarm.done / prewarm.total) * 100) : 0}%` }} />
                  </div>
                  <div className="text-xs text-muted-foreground">
                    {prewarm.done}/{prewarm.total} · 失败 {prewarm.failed} · z{prewarm.zoom}
                  </div>
                  <Button variant="outline" size="sm" className="w-full" onClick={stopPrewarm}>
                    <X className="w-4 h-4 mr-1" />停止预热
                  </Button>
                </div>
              ) : (
                <div className="space-y-2">
                  <Button size="sm" className="w-full" onClick={startPrewarm} disabled={!online}>
                    <Download className="w-4 h-4 mr-1" />预热当前作业区
                  </Button>
                  <input
                    ref={fileInputRef}
                    type="file"
                    accept=".tar"
                    className="hidden"
                    onChange={(e) => {
                      const file = e.target.files?.[0]
                      if (file) importPack(file)
                      e.target.value = ''
                    }}
                  />
                  <Button variant="outline" size="sm" className="w-full" disabled={importing}
                    onClick={() => fileInputRef.current?.click()}>
                    {importing ? <Loader2 className="w-4 h-4 mr-1 animate-spin" /> : <Upload className="w-4 h-4 mr-1" />}
                    导入离线包
                  </Button>
                  <Button variant="outline" size="sm" className="w-full" onClick={clearCache}>
                    <Trash2 className="w-4 h-4 mr-1" />清空缓存
                  </Button>
                </div>
              )}
              {prewarm && !prewarm.running && prewarm.total > 0 && (
                <div className="text-xs text-muted-foreground">
                  上次预热: {prewarm.done - prewarm.failed} 成功 · {prewarm.failed} 失败{prewarm.stopped ? ' · 已手动停止' : ''}
                </div>
              )}
              {cacheMsg && <div className="rounded-md bg-muted p-2 text-xs">{cacheMsg}</div>}
              <p className="text-xs text-muted-foreground">联网时底图按缓存优先自动落盘; 无网络时可导入离线包(由联网设备 map_pack_export 导出)。</p>
            </CardContent>
          </Card>
        </aside>

        <section className="relative min-h-[520px] overflow-hidden rounded-lg border bg-background">
          <div ref={containerRef} className="absolute inset-0" />
          {(loadingMap || !mapConfig?.enabled || mapError) && (
            <div className="absolute inset-0 z-10 flex items-center justify-center bg-background/90 p-6">
              <div className="max-w-sm text-center space-y-3">
                {loadingMap ? <Loader2 className="mx-auto h-8 w-8 animate-spin text-primary" /> : mapConfig?.enabled ? <AlertTriangle className="mx-auto h-8 w-8 text-amber-500" /> : <MapPinned className="mx-auto h-8 w-8 text-muted-foreground" />}
                <div className="font-medium">{loadingMap ? '地图加载中' : mapError || '地图不可用'}</div>
                <p className="text-sm text-muted-foreground">底图使用本地缓存瓦片, 离线时仅显示已预热区域。</p>
                <Button variant="outline" size="sm" onClick={loadConfig}>重新读取配置</Button>
              </div>
            </div>
          )}
          {mapConfig?.enabled && !mapError && activeSamples.length === 0 && (
            <div className="absolute left-4 bottom-4 z-10 rounded-md border bg-background/95 px-3 py-2 text-sm shadow-sm">
              <Activity className="inline h-4 w-4 mr-1 text-muted-foreground" />
              等待带 GPS 的采样点
            </div>
          )}
        </section>
      </div>
    </div>
  )
}

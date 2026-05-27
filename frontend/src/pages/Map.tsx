/* eslint-disable react-hooks/set-state-in-effect */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import AMapLoader from '@amap/amap-jsapi-loader'
import { Activity, AlertTriangle, Database, Layers, Loader2, MapPinned, Navigation, RefreshCw, Route } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { cn } from '@/lib/utils'

declare global {
  interface Window {
    _AMapSecurityConfig?: {
      securityJsCode?: string
    }
  }
}

type MapMode = 'live' | 'history'
type MetricMode = 'auto' | 'concentration' | 'absorbance' | 'voltage'

interface AMapConfig {
  enabled: boolean
  key: string
  securityJsCode: string
  version: string
  plugins: string[]
}

interface MissionMeta {
  id: string
  name: string
  start_time: string
  point_count: number
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
}

type Overlay = {
  on?: (event: string, handler: () => void) => void
}

type HeatMapOverlay = {
  setDataSet: (dataset: { data: Array<{ lng: number; lat: number; count: number }>; max: number }) => void
}

type MapInstance = {
  add: (overlay: Overlay | HeatMapOverlay) => void
  remove: (overlay: Overlay) => void
  setFitView: (overlays: Overlay[], immediately?: boolean, padding?: [number, number, number, number]) => void
  addControl: (control: Overlay) => void
}

type AMapApi = {
  Map: new (container: HTMLDivElement | null, options: Record<string, unknown>) => MapInstance
  Polyline: new (options: Record<string, unknown>) => Overlay
  Marker: new (options: Record<string, unknown>) => Overlay
  CircleMarker: new (options: Record<string, unknown>) => Overlay
  InfoWindow: new (options: Record<string, unknown>) => { open: (map: MapInstance, position: [number, number]) => void }
  Pixel: new (x: number, y: number) => unknown
  Scale: new () => Overlay
  ToolBar: new (options?: Record<string, unknown>) => Overlay
  HeatMap?: new (map: MapInstance, options: Record<string, unknown>) => HeatMapOverlay
}

const metricLabels: Record<MetricMode, string> = {
  auto: '自动',
  concentration: '浓度',
  absorbance: '吸光度',
  voltage: '电压',
}

const sampleColors = ['#2f9e44', '#74b816', '#f59f00', '#f08c00', '#e03131']

function numeric(value: unknown) {
  const n = Number(value)
  return Number.isFinite(n) ? n : null
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
  const mapRef = useRef<MapInstance | null>(null)
  const amapRef = useRef<AMapApi | null>(null)
  const overlaysRef = useRef<Overlay[]>([])
  const heatmapRef = useRef<HeatMapOverlay | null>(null)
  const [mode, setMode] = useState<MapMode>('live')
  const [metric, setMetric] = useState<MetricMode>('auto')
  const [mapConfig, setMapConfig] = useState<AMapConfig | null>(null)
  const [mapError, setMapError] = useState('')
  const [loadingMap, setLoadingMap] = useState(false)
  const [missions, setMissions] = useState<MissionMeta[]>([])
  const [selectedMission, setSelectedMission] = useState('')
  const [geojson, setGeojson] = useState<GeoJsonPayload | null>(null)
  const [surface, setSurface] = useState<SurfacePayload | null>(null)
  const [statusText, setStatusText] = useState('等待地图数据')

  const activeSamples = useMemo(
    () => geojson?.features.filter((f) => f.properties?.layer === 'sample') || [],
    [geojson],
  )

  const loadConfig = useCallback(async () => {
    const res = await fetch('/api/map/config')
    const json = await res.json()
    setMapConfig(json.data)
    if (!json.data?.enabled) setMapError('未配置高德地图 Key')
  }, [])

  const loadMissions = useCallback(async () => {
    const res = await fetch('/api/data/missions')
    const json = await res.json()
    if (!json.success) return
    setMissions(json.data || [])
    if (!selectedMission && json.data?.length > 0) setSelectedMission(json.data[0].id)
  }, [selectedMission])

  const clearOverlays = useCallback(() => {
    const map = mapRef.current
    if (!map) return
    overlaysRef.current.forEach((overlay) => map.remove(overlay))
    overlaysRef.current = []
    if (heatmapRef.current) {
      heatmapRef.current.setDataSet({ data: [], max: 1 })
    }
  }, [])

  const renderGeojson = useCallback((payload: GeoJsonPayload | null, surfacePayload: SurfacePayload | null) => {
    const AMap = amapRef.current
    const map = mapRef.current
    if (!AMap || !map || !payload) return

    clearOverlays()
    const range = sampleRange(payload.features)
    const fitTargets: Overlay[] = []

    payload.features.forEach((feature) => {
      const layer = feature.properties?.layer
      if (feature.geometry.type === 'LineString') {
        const path = feature.geometry.coordinates as [number, number][]
        const overlay = new AMap.Polyline({
          path,
          strokeWeight: layer === 'route' ? 4 : 3,
          strokeColor: layer === 'route' ? '#2563eb' : '#0f766e',
          strokeOpacity: layer === 'route' ? 0.75 : 0.55,
          lineJoin: 'round',
          lineCap: 'round',
        })
        map.add(overlay)
        overlaysRef.current.push(overlay)
        fitTargets.push(overlay)
        return
      }

      const [lng, lat] = feature.geometry.coordinates as [number, number]
      if (layer === 'waypoint') {
        const marker = new AMap.Marker({
          position: [lng, lat],
          anchor: 'bottom-center',
          label: { content: `#${feature.properties?.seq ?? ''}`, direction: 'top' },
        })
        map.add(marker)
        overlaysRef.current.push(marker)
        fitTargets.push(marker)
        return
      }

      const value = numeric(feature.properties?.value) ?? 0
      const marker = new AMap.CircleMarker({
        center: [lng, lat],
        radius: 8,
        fillColor: colorFor(value, range.min, range.max),
        fillOpacity: 0.9,
        strokeColor: '#ffffff',
        strokeWeight: 2,
      })
      marker.on?.('click', () => {
        const info = new AMap.InfoWindow({
          content: `<div style="min-width:160px;font-size:12px;line-height:1.6"><b>${metricLabels[metric]}</b><br/>值: ${value.toPrecision(5)}<br/>航点: ${feature.properties?.waypoint_seq ?? '-'}</div>`,
          offset: new AMap.Pixel(0, -8),
        })
        info.open(map, [lng, lat])
      })
      map.add(marker)
      overlaysRef.current.push(marker)
      fitTargets.push(marker)
    })

    if (surfacePayload?.valid && surfacePayload.grid.length > 0) {
      const max = surfacePayload.max || Math.max(...surfacePayload.grid.map((p) => p.value))
      if (!heatmapRef.current && AMap.HeatMap) {
        heatmapRef.current = new AMap.HeatMap(map, {
          radius: 30,
          opacity: [0.2, 0.75],
          gradient: {
            0.2: '#2f9e44',
            0.45: '#74b816',
            0.65: '#f59f00',
            0.82: '#f08c00',
            1.0: '#e03131',
          },
        })
      }
      heatmapRef.current?.setDataSet({
        data: surfacePayload.grid.map((p) => ({ lng: p.lng, lat: p.lat, count: p.value })),
        max,
      })
    }

    if (fitTargets.length > 0) map.setFitView(fitTargets, false, [40, 40, 40, 40])
  }, [clearOverlays, metric])

  const loadLive = useCallback(async () => {
    const res = await fetch('/api/map/live')
    const json = await res.json()
    const live = (json.data || {}) as LivePayload
    const features: GeoFeature[] = []
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
    setGeojson({ type: 'FeatureCollection', features })
    setSurface(null)
    setStatusText(live.position ? `实时船位 ${live.position.gcj02.lat.toFixed(6)}, ${live.position.gcj02.lng.toFixed(6)}` : '等待 GPS 船位')
  }, [metric])

  const loadHistory = useCallback(async () => {
    if (!selectedMission) return
    const [geoRes, surfaceRes] = await Promise.all([
      fetch(`/api/data/mission/${selectedMission}/geojson?metric=${metric}`),
      fetch(`/api/data/mission/${selectedMission}/surface?metric=${metric}`),
    ])
    const geo = await geoRes.json()
    const surfaceJson = await surfaceRes.json()
    setGeojson(geo.data)
    setSurface(surfaceJson.data)
    setStatusText(surfaceJson.data?.valid ? '历史污染面已生成' : surfaceJson.data?.reason || '历史任务已加载')
  }, [metric, selectedMission])

  useEffect(() => {
    loadConfig()
    loadMissions()
  }, [loadConfig, loadMissions])

  useEffect(() => {
    if (!mapConfig?.enabled || !containerRef.current || mapRef.current) return
    setLoadingMap(true)
    window._AMapSecurityConfig = { securityJsCode: mapConfig.securityJsCode }
    AMapLoader.load({
      key: mapConfig.key,
      version: mapConfig.version || '2.0',
      plugins: mapConfig.plugins?.length ? mapConfig.plugins : ['AMap.Scale', 'AMap.ToolBar', 'AMap.HeatMap'],
    }).then((AMap: AMapApi) => {
      amapRef.current = AMap
      mapRef.current = new AMap.Map(containerRef.current, {
        zoom: 15,
        viewMode: '2D',
        resizeEnable: true,
      })
      mapRef.current?.addControl(new AMap.Scale())
      mapRef.current?.addControl(new AMap.ToolBar({ position: { right: '16px', top: '16px' } }))
      setMapError('')
    }).catch((err) => {
      console.error(err)
      setMapError('高德地图加载失败')
    }).finally(() => setLoadingMap(false))
  }, [mapConfig])

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

  return (
    <div className="h-[calc(100vh-5rem)] md:h-screen p-3 md:p-5 flex flex-col gap-3 bg-muted/20">
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
          <select className="h-9 rounded-md border border-input bg-background px-3 text-sm" value={metric} onChange={(e) => setMetric(e.target.value as MetricMode)}>
            {Object.entries(metricLabels).map(([key, label]) => <option key={key} value={key}>{label}</option>)}
          </select>
          <Button variant="outline" size="sm" onClick={() => mode === 'live' ? loadLive() : loadHistory()}>
            <RefreshCw className="w-4 h-4 mr-1" />
            刷新
          </Button>
        </div>
      </header>

      <div className="grid grid-cols-1 xl:grid-cols-[320px_minmax(0,1fr)] gap-3 flex-1 min-h-0">
        <aside className="space-y-3 min-h-0 xl:overflow-auto">
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
                      <div className="text-xs text-muted-foreground">{mission.point_count} 点 · {new Date(mission.start_time).toLocaleString()}</div>
                    </button>
                  ))}
                </div>
              </CardContent>
            </Card>
          )}

          <Card>
            <CardHeader className="pb-2">
              <CardTitle className="text-base flex items-center gap-2"><Layers className="w-4 h-4" />图层状态</CardTitle>
            </CardHeader>
            <CardContent className="space-y-3 text-sm">
              <div className="flex justify-between"><span className="text-muted-foreground">采样点</span><span className="font-medium">{activeSamples.length}</span></div>
              <div className="flex justify-between"><span className="text-muted-foreground">指标</span><span className="font-medium">{metricLabels[metric]}</span></div>
              <div className="flex justify-between"><span className="text-muted-foreground">IDW</span><span className="font-medium">{surface?.valid ? `${surface.grid.length} 格` : '未生成'}</span></div>
              <div className="grid grid-cols-5 gap-1 pt-1">
                {sampleColors.map((color) => <div key={color} className="h-2 rounded-full" style={{ background: color }} />)}
              </div>
              {surface && !surface.valid && <div className="rounded-md bg-amber-500/10 p-2 text-xs text-amber-700 dark:text-amber-300">{surface.reason}</div>}
            </CardContent>
          </Card>
        </aside>

        <section className="relative min-h-[520px] overflow-hidden rounded-lg border bg-background">
          <div ref={containerRef} className="absolute inset-0" />
          {(loadingMap || !mapConfig?.enabled || mapError) && (
            <div className="absolute inset-0 z-10 flex items-center justify-center bg-background/90 p-6">
              <div className="max-w-sm text-center space-y-3">
                {loadingMap ? <Loader2 className="mx-auto h-8 w-8 animate-spin text-primary" /> : mapConfig?.enabled ? <AlertTriangle className="mx-auto h-8 w-8 text-amber-500" /> : <MapPinned className="mx-auto h-8 w-8 text-muted-foreground" />}
                <div className="font-medium">{loadingMap ? '地图加载中' : mapError || '高德地图未配置'}</div>
                <p className="text-sm text-muted-foreground">需要在运行环境配置 AMAP_WEB_KEY 和 AMAP_SECURITY_JS_CODE 后刷新页面。</p>
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

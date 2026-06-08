import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import L from 'leaflet'
import 'leaflet/dist/leaflet.css'
import 'leaflet.heat'
import { Activity, AlertTriangle, Database, Download, Layers, Loader2, MapPinned, Navigation, RefreshCw, Route, Trash2, Upload, Wifi, WifiOff, X } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
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
  const mapRef = useRef<L.Map | null>(null)
  const overlaysRef = useRef<L.LayerGroup | null>(null)
  const heatRef = useRef<HeatLayer | null>(null)
  const socket = useAppStore((state) => state.socket)
  const [mode, setMode] = useState<MapMode>('live')
  const [metric, setMetric] = useState<MetricMode>('auto')
  const [mapConfig, setMapConfig] = useState<MapConfig | null>(null)
  const [mapError, setMapError] = useState('')
  const [loadingMap, setLoadingMap] = useState(false)
  const [missions, setMissions] = useState<MissionMeta[]>([])
  const [selectedMission, setSelectedMission] = useState('')
  const [geojson, setGeojson] = useState<GeoJsonPayload | null>(null)
  const [surface, setSurface] = useState<SurfacePayload | null>(null)
  const [statusText, setStatusText] = useState('等待地图数据')
  const [online, setOnline] = useState<boolean | null>(null)
  const [prewarm, setPrewarm] = useState<PrewarmStatus | null>(null)
  const [cacheStats, setCacheStats] = useState<CacheStats | null>(null)
  const [cacheMsg, setCacheMsg] = useState('')
  const [offlineMode, setOfflineMode] = useState(false)
  const [importing, setImporting] = useState(false)
  const fileInputRef = useRef<HTMLInputElement | null>(null)

  const activeSamples = useMemo(
    () => geojson?.features.filter((f) => f.properties?.layer === 'sample') || [],
    [geojson],
  )

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
        if (typeof json.data.offline_mode === 'boolean') setOfflineMode(json.data.offline_mode)
      }
    } catch {
      /* 离线时忽略 */
    }
  }, [])

  const toggleOffline = useCallback(async (enabled: boolean) => {
    setOfflineMode(enabled)
    try {
      const res = await fetch('/api/map/offline-mode', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ enabled }),
      })
      const json = await res.json()
      setCacheMsg(json.message || '')
    } catch {
      setCacheMsg('离线模式切换失败')
    }
  }, [])

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
      } else {
        setCacheMsg(json.message || '导入失败')
      }
    } catch {
      setCacheMsg('导入请求失败')
    } finally {
      setImporting(false)
    }
  }, [loadCacheStats])

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
            html: `<span>#${feature.properties?.seq ?? ''}</span>`,
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
      marker.bindPopup(
        `<div style="min-width:160px;font-size:12px;line-height:1.6"><b>${metricLabels[metric]}</b><br/>值: ${value.toPrecision(5)}<br/>航点: ${feature.properties?.waypoint_seq ?? '-'}</div>`,
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

    if (bounds.isValid()) map.fitBounds(bounds, { padding: [40, 40] })
  }, [clearOverlays, metric])

  const loadLive = useCallback(async () => {
    const res = await fetch('/api/map/live')
    const json = await res.json()
    const live = (json.data || {}) as LivePayload
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
  }, [loadCacheStats])

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
      L.tileLayer(tileTpl.replace('{style}', mapConfig.default_style), {
        minZoom: mapConfig.min_zoom,
        maxZoom: mapConfig.max_zoom,
      }).addTo(map)
      overlaysRef.current = L.layerGroup().addTo(map)
      heatRef.current = (L as unknown as { heatLayer: (pts: Array<[number, number, number]>, opts: Record<string, unknown>) => HeatLayer }).heatLayer([], {
        radius: 30,
        blur: 18,
        gradient: { 0.2: '#2f9e44', 0.45: '#74b816', 0.65: '#f59f00', 0.82: '#f08c00', 1.0: '#e03131' },
      }).addTo(map)
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
      if (!status.running) loadCacheStats()
    }
    socket.on('map_prewarm_progress', onProgress)
    return () => {
      socket.off('map_prewarm_progress', onProgress)
    }
  }, [socket, loadCacheStats])

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
              <label className="flex items-center justify-between">
                <span className="text-muted-foreground">离线模式</span>
                <input
                  type="checkbox"
                  className="h-4 w-8 cursor-pointer accent-primary"
                  checked={offlineMode}
                  onChange={(e) => toggleOffline(e.target.checked)}
                />
              </label>
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
                  <Button size="sm" className="w-full" onClick={startPrewarm} disabled={!online || offlineMode}>
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
              <p className="text-xs text-muted-foreground">联网时预热当前视野; 无网络时可导入离线包(由联网设备 map_pack_export 导出), 并开启离线模式避免弱网卡顿。</p>
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

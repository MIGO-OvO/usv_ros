import { useCallback, useEffect, useRef, useState } from 'react'
import L from 'leaflet'
import 'leaflet/dist/leaflet.css'
import { Activity, Crosshair, Download, FlaskConical, Gauge, MapPin, Navigation, Play, RotateCcw, Save, Square, Trash2 } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Switch } from '@/components/ui/switch'
import { boatIcon, isFiniteLatLng, moveBoatMarker, speedPercent, startIcon } from '@/lib/lab-map'
import type { LabConfig, LabMission, LabPollution, LabStatus, LatLng, MapConfigLite } from '@/lib/lab-types'
import { fallbackConfig, fallbackStatus } from '@/lib/lab-types'

type DrawMode = '' | 'start' | 'waypoint' | 'source'

export default function Lab() {
  const [config, setConfig] = useState<LabConfig>(fallbackConfig)
  const [status, setStatus] = useState<LabStatus>(fallbackStatus)
  const [pending, setPending] = useState('')
  const [message, setMessage] = useState('')
  const [drawMode, setDrawMode] = useState<DrawMode>('')

  const containerRef = useRef<HTMLDivElement | null>(null)
  const mapRef = useRef<L.Map | null>(null)
  const layerRef = useRef<L.LayerGroup | null>(null)
  const boatRef = useRef<L.Marker | null>(null)
  const labBoundsRef = useRef<L.LatLngBounds | null>(null)
  const labInitialFitDoneRef = useRef(false)
  const configRef = useRef(config)
  const drawModeRef = useRef(drawMode)
  const pendingRef = useRef(pending)
  const dirtyRef = useRef(false)
  const [mapReady, setMapReady] = useState(false)
  const [hasLabBounds, setHasLabBounds] = useState(false)
  configRef.current = config
  drawModeRef.current = drawMode
  pendingRef.current = pending

  const fitLabBounds = useCallback(() => {
    const map = mapRef.current
    const bounds = labBoundsRef.current
    if (!map || !bounds?.isValid()) return
    map.invalidateSize()
    map.fitBounds(bounds, { padding: [36, 36], maxZoom: 15 })
    labInitialFitDoneRef.current = true
  }, [])

  const setBoatToStart = useCallback((simConfig: LabConfig['sim']) => {
    moveBoatMarker(
      boatRef.current,
      { lat: Number(simConfig.start_lat), lng: Number(simConfig.start_lng) },
      Number(simConfig.heading_deg) || 0,
    )
  }, [])

  const persistMission = useCallback(async (mission: LabMission) => {
    try {
      const res = await fetch('/api/lab/mission', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(mission),
      })
      const json = await res.json()
      if (json.success && json.data) {
        dirtyRef.current = false
        setConfig((c) => ({ ...c, mission: json.data }))
        return true
      }
      setMessage(json.message || '实验航线保存失败')
    } catch {
      setMessage('实验航线保存失败')
    }
    return false
  }, [])

  const persistConfig = useCallback(async (nextConfig: LabConfig) => {
    try {
      const res = await fetch('/api/lab/config', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(nextConfig),
      })
      const json = await res.json()
      if (json.success && json.data) {
        dirtyRef.current = false
        setConfig(json.data)
        return true
      }
      setMessage(json.message || '配置保存失败')
    } catch {
      setMessage('配置保存失败')
    }
    return false
  }, [])

  const setStartPosition = useCallback((position: LatLng) => {
    if (!isFiniteLatLng(position)) return
    const nextConfig = {
      ...configRef.current,
      sim: {
        ...configRef.current.sim,
        start_lat: position.lat,
        start_lng: position.lng,
      },
    }
    dirtyRef.current = true
    setConfig(nextConfig)
    setBoatToStart(nextConfig.sim)
    persistConfig(nextConfig).catch(() => {})
  }, [persistConfig, setBoatToStart])

  const refresh = async () => {
    const res = await fetch('/api/lab/status')
    const json = await res.json()
    if (json.data?.config && !drawModeRef.current && !pendingRef.current && !dirtyRef.current) setConfig(json.data.config)
    if (json.data?.status) setStatus({ ...fallbackStatus, ...json.data.status })
    if (json.data?.position?.gcj02 && boatRef.current) {
      moveBoatMarker(boatRef.current, json.data.position.gcj02, Number(json.data.position.heading_deg || json.data.status?.heading_deg || 0))
    }
  }

  // 初始化编辑地图 (复用 /api/map/config 的瓦片代理)
  useEffect(() => {
    let cancelled = false
    let resizeObserver: ResizeObserver | null = null
    fetch('/api/map/config').then((r) => r.json()).then((json) => {
      const cfg = json.data as MapConfigLite | undefined
      if (cancelled || !cfg || !containerRef.current || mapRef.current) return
      const map = L.map(containerRef.current, {
        center: [cfg.default_center.lat, cfg.default_center.lng],
        zoom: cfg.default_zoom,
        attributionControl: false,
      })
      L.tileLayer(cfg.tile_url.replace('{style}', cfg.default_style), {
        minZoom: cfg.min_zoom, maxZoom: cfg.max_zoom,
      }).addTo(map)
      layerRef.current = L.layerGroup().addTo(map)
      boatRef.current = L.marker([cfg.default_center.lat, cfg.default_center.lng], {
        icon: boatIcon(configRef.current.sim.heading_deg),
      }).addTo(map)
      setBoatToStart(configRef.current.sim)
      map.on('click', (e: L.LeafletMouseEvent) => {
        const mode = drawModeRef.current
        if (mode === 'start') {
          setStartPosition({ lat: e.latlng.lat, lng: e.latlng.lng })
        } else if (mode === 'waypoint') {
          const current = configRef.current
          const waypoints = [
            ...current.mission.waypoints,
            { lat: e.latlng.lat, lng: e.latlng.lng, seq: current.mission.waypoints.length },
          ]
          const nextMission = { waypoints, center: null }
          dirtyRef.current = true
          setConfig((c) => ({ ...c, mission: nextMission }))
          persistMission(nextMission).catch(() => {})
        } else if (mode === 'source') {
          const nextConfig = {
            ...configRef.current,
            pollution: {
              ...configRef.current.pollution,
              mode: 'manual' as const,
              source: { lat: e.latlng.lat, lng: e.latlng.lng },
            },
          }
          dirtyRef.current = true
          setConfig(nextConfig)
          persistConfig(nextConfig).catch(() => {})
        }
      })
      if (typeof ResizeObserver !== 'undefined') {
        resizeObserver = new ResizeObserver(() => map.invalidateSize())
        resizeObserver.observe(containerRef.current)
      }
      mapRef.current = map
      setMapReady(true)
      window.setTimeout(() => map.invalidateSize(), 0)
    }).catch(() => {})
    return () => {
      cancelled = true
      resizeObserver?.disconnect()
      mapRef.current?.remove()
      mapRef.current = null
      layerRef.current = null
      boatRef.current = null
      setMapReady(false)
    }
  }, [persistConfig, persistMission, setBoatToStart, setStartPosition])

  // 重绘航线/航点/污染源
  useEffect(() => {
    const group = layerRef.current
    if (!mapReady || !group) return
    group.clearLayers()
    const pts = config.mission.waypoints.map((w) => [w.lat, w.lng] as [number, number])
    const bounds = L.latLngBounds([])
    const startLat = Number(config.sim.start_lat)
    const startLng = Number(config.sim.start_lng)
    if (Number.isFinite(startLat) && Number.isFinite(startLng)) {
      L.marker([startLat, startLng], { icon: startIcon() }).addTo(group)
      bounds.extend([startLat, startLng])
    }
    if (pts.length > 1) {
      L.polyline(pts, { color: '#2563eb', weight: 3, opacity: 0.7 }).addTo(group)
    }
    config.mission.waypoints.forEach((w, i) => {
      L.marker([w.lat, w.lng], {
        icon: L.divIcon({ className: 'usv-waypoint-icon', html: `<span>#${i}</span>`, iconAnchor: [0, 0] }),
      }).addTo(group)
      bounds.extend([w.lat, w.lng])
    })
    const src = config.pollution.mode === 'manual' ? config.pollution.source : config.mission.center
    if (src) {
      L.circle([src.lat, src.lng], {
        radius: config.pollution.radius_m, color: '#e03131', fillColor: '#e03131', fillOpacity: 0.12, weight: 1,
      }).addTo(group)
      bounds.extend([src.lat, src.lng])
    }
    if (bounds.isValid()) {
      labBoundsRef.current = bounds
      setHasLabBounds(true)
      if (!labInitialFitDoneRef.current) {
        const map = mapRef.current
        map?.invalidateSize()
        map?.fitBounds(bounds, { padding: [36, 36], maxZoom: 15 })
        labInitialFitDoneRef.current = true
      }
    } else {
      labBoundsRef.current = null
      setHasLabBounds(false)
    }
  }, [config.mission, config.pollution, config.sim.start_lat, config.sim.start_lng, mapReady])

  useEffect(() => {
    refresh().catch(() => {})
    const timer = window.setInterval(() => refresh().catch(() => {}), 1000)
    return () => window.clearInterval(timer)
  }, [])

  const updateConfig = (patch: Partial<LabConfig>) => {
    dirtyRef.current = true
    setConfig((c) => ({ ...c, ...patch }))
  }
  const updateSim = (key: keyof LabConfig['sim'], value: string) => {
    dirtyRef.current = true
    setConfig((c) => ({ ...c, sim: { ...c.sim, [key]: Number(value) || 0 } }))
  }
  const updatePollution = (patch: Partial<LabPollution>) => {
    dirtyRef.current = true
    setConfig((c) => ({ ...c, pollution: { ...c.pollution, ...patch } }))
  }

  const run = async (key: string, action: () => Promise<void>) => {
    if (pending) return
    setPending(key)
    setMessage('')
    try { await action() } finally { setPending('') }
  }

  const saveConfig = async () => {
    const res = await fetch('/api/lab/config', {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(config),
    })
    const json = await res.json()
    setMessage(json.message || (json.success ? '已保存' : '保存失败'))
    if (json.data) {
      dirtyRef.current = false
      setConfig(json.data)
    }
  }

  const clearWaypoints = async () => {
    const nextMission = { waypoints: [], center: null }
    dirtyRef.current = true
    setConfig((c) => ({ ...c, mission: nextMission }))
    await persistMission(nextMission)
  }

  const importQgc = async () => {
    const res = await fetch('/api/lab/mission/import-qgc', { method: 'POST' })
    const json = await res.json()
    setMessage(json.message || (json.success ? '已导入' : '导入失败'))
    if (json.success && json.data) {
      dirtyRef.current = false
      setConfig((c) => ({ ...c, mission: json.data }))
    }
  }

  const start = async () => {
    await saveConfig()
    const res = await fetch('/api/lab/start', { method: 'POST' })
    const json = await res.json()
    setMessage(json.message || '已启动')
    await refresh()
  }

  const stop = async () => {
    const res = await fetch('/api/lab/stop', { method: 'POST' })
    const json = await res.json()
    setMessage(json.message || '已停止')
    await refresh()
  }

  const propulsion = status.virtual_propulsion || fallbackStatus.virtual_propulsion
  const mission = status.mission || fallbackStatus.mission!
  const speedLimitMps = Math.max(0, Number(config.sim.max_speed_mps) || 0)
  const liveSpeedPercent = speedPercent(status.speed_mps || 0, speedLimitMps)
  const stage = !config.mission.waypoints.length ? '未配置航点'
    : status.running ? (mission.active ? `航行中 · 目标 #${mission.target_seq ?? '-'}` : '采样中')
    : mission.reached_count >= config.mission.waypoints.length && config.mission.waypoints.length > 0 ? '已完成'
    : '就绪'

  return (
    <div className="min-h-[calc(100vh-5rem)] px-3 pb-24 pt-3 md:p-5 xl:h-screen xl:min-h-0 flex flex-col gap-3 bg-muted/20">
      <header className="flex flex-col md:flex-row md:items-center justify-between gap-3 shrink-0">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">实验室测试</h1>
          <p className="text-sm text-muted-foreground">半实物采样、模拟走航与虚拟差速输出</p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <Button variant="outline" disabled={!!pending} onClick={() => run('save', saveConfig)}>
            <Save className="w-4 h-4 mr-2" />
            保存
          </Button>
          <Button disabled={!!pending || status.running} onClick={() => run('start', start)}>
            <Play className="w-4 h-4 mr-2" />
            启动
          </Button>
          <Button variant="secondary" disabled={!!pending || !status.running} onClick={() => run('stop', stop)}>
            <Square className="w-4 h-4 mr-2" />
            停止
          </Button>
        </div>
      </header>

      {message && <div className="rounded-md border bg-card px-4 py-3 text-sm">{message}</div>}

      {/* 当前阶段引导横幅 */}
      <div className="flex items-start gap-3 rounded-md border bg-background px-4 py-3 text-sm shadow-sm shrink-0 sm:items-center">
        <Activity className="h-4 w-4 text-primary shrink-0" />
        <span className="font-medium">当前阶段: {stage}</span>
        <span className="text-muted-foreground">
          {stage === '未配置航点' && '在地图点击"画航点"放置虚拟航点, 或从 QGC 导入, 再点启动。'}
          {stage === '就绪' && '已配置航线, 点"启动"让虚拟船自动巡航并到点采样。'}
          {stage.startsWith('航行中') && `虚拟船自动巡航中, 已到达 ${mission.reached_count}/${config.mission.waypoints.length} 点。`}
          {stage === '采样中' && '到达航点, 正在按数据源采集 (模拟生成或真实设备)。'}
          {stage === '已完成' && '航线已跑完, 切到地图页查看采样点与污染热力图。'}
        </span>
      </div>

      <div className="grid grid-cols-1 gap-3 overflow-visible xl:grid-cols-[360px_minmax(0,1fr)] xl:flex-1 xl:min-h-0 xl:overflow-hidden lab-map-workspace">
        <aside className="space-y-3 min-h-0 xl:overflow-auto xl:pr-1">
          <Card>
            <CardHeader className="pb-2">
              <CardTitle className="flex items-center gap-2 text-base">
                <FlaskConical className="h-5 w-5" />
                模式与数据源
              </CardTitle>
            </CardHeader>
            <CardContent className="grid grid-cols-1 gap-3">
              <label className="flex items-center justify-between gap-3 rounded-md border p-3">
                <span>启用实验模式</span>
                <Switch checked={config.enabled} onCheckedChange={(enabled) => updateConfig({ enabled })} />
              </label>
              <div className="space-y-2 rounded-md border p-3">
                <span className="text-sm font-medium">数据源</span>
                <div className="grid grid-cols-2 rounded-md border bg-background p-1">
                  {(['simulated', 'real'] as const).map((ds) => (
                    <Button key={ds} size="sm" className="min-w-0" variant={config.data_source === ds ? 'default' : 'ghost'}
                      onClick={() => updateConfig({ data_source: ds })}>
                      {ds === 'simulated' ? '模拟生成' : '真实设备'}
                    </Button>
                  ))}
                </div>
              </div>
              <label className="flex items-center justify-between gap-3 rounded-md border p-3">
                <span>跳过 PID 角度等待</span>
                <Switch checked={config.bypass_pid_wait} onCheckedChange={(bypass_pid_wait) => updateConfig({ bypass_pid_wait })} />
              </label>
              <label className="flex items-center justify-between gap-3 rounded-md border p-3">
                <span>允许无 GPS</span>
                <Switch checked={config.allow_no_gps} onCheckedChange={(allow_no_gps) => updateConfig({ allow_no_gps })} />
              </label>
            </CardContent>
          </Card>

          <Card>
            <CardHeader className="pb-2">
              <CardTitle className="text-base">模拟参数与污染源</CardTitle>
            </CardHeader>
            <CardContent className="space-y-4">
              <div className="rounded-md border bg-muted/30 p-3">
                <div className="mb-3 flex items-center justify-between gap-3">
                  <div>
                    <div className="text-sm font-medium">起始位置</div>
                    <div className="text-xs text-muted-foreground">可输入经纬度，也可在右侧地图点选。</div>
                  </div>
                  <Button size="sm" variant={drawMode === 'start' ? 'default' : 'outline'}
                    onClick={() => setDrawMode((m) => (m === 'start' ? '' : 'start'))}>
                    <Navigation className="w-4 h-4 mr-1" />放起点
                  </Button>
                </div>
                <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
                  <div className="space-y-2">
                    <Label>起始纬度</Label>
                    <Input type="number" step="0.000001" value={config.sim.start_lat} onChange={(e) => updateSim('start_lat', e.target.value)} />
                  </div>
                  <div className="space-y-2">
                    <Label>起始经度</Label>
                    <Input type="number" step="0.000001" value={config.sim.start_lng} onChange={(e) => updateSim('start_lng', e.target.value)} />
                  </div>
                </div>
              </div>

              <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
                <div className="space-y-2">
                  <Label>航向</Label>
                  <Input type="number" min={0} max={360} step={1} value={config.sim.heading_deg} onChange={(e) => updateSim('heading_deg', e.target.value)} />
                </div>
                <div className="space-y-2">
                  <Label className="flex items-center gap-2"><Gauge className="h-4 w-4" />速度上限</Label>
                  <Input type="number" min={0} max={20} step={0.1} value={config.sim.max_speed_mps} onChange={(e) => updateSim('max_speed_mps', e.target.value)} />
                  <div className="text-xs text-muted-foreground">仿真器按该 m/s 上限换算左右推进输出，不再按 0-1 显示为船速。</div>
                </div>
                <div className="space-y-2">
                  <Label>差速轴距</Label>
                  <Input type="number" min={0.05} step={0.05} value={config.sim.wheel_base_m} onChange={(e) => updateSim('wheel_base_m', e.target.value)} />
                </div>
                <div className="space-y-2">
                  <Label>到点半径(m)</Label>
                  <Input type="number" min={0.5} step={0.5} value={config.sim.arrival_radius_m} onChange={(e) => updateSim('arrival_radius_m', e.target.value)} />
                </div>
              </div>
              <div className="grid grid-cols-1 gap-3 border-t pt-4 sm:grid-cols-2">
                <div className="col-span-2 space-y-2">
                  <Label>污染源模式</Label>
                  <div className="grid grid-cols-2 rounded-md border bg-background p-1">
                    {(['center', 'manual'] as const).map((pm) => (
                      <Button key={pm} size="sm" variant={config.pollution.mode === pm ? 'default' : 'ghost'}
                        onClick={() => updatePollution({ mode: pm })}>
                        {pm === 'center' ? '航线中心' : '手动放置'}
                      </Button>
                    ))}
                  </div>
                </div>
                <div className="space-y-2">
                  <Label>扩散半径(m)</Label>
                  <Input type="number" value={config.pollution.radius_m}
                    onChange={(e) => updatePollution({ radius_m: Number(e.target.value) || 0 })} />
                </div>
                <div className="space-y-2">
                  <Label>强度(0-1)</Label>
                  <Input type="number" step="0.1" value={config.pollution.strength}
                    onChange={(e) => updatePollution({ strength: Number(e.target.value) || 0 })} />
                </div>
              </div>
            </CardContent>
          </Card>

          <Card>
            <CardHeader className="pb-2">
              <CardTitle className="flex items-center gap-2 text-base">
                <Activity className="h-5 w-5" />
                状态
              </CardTitle>
            </CardHeader>
            <CardContent className="space-y-4">
              <div className="grid grid-cols-2 gap-3 text-sm">
                <div className="rounded-md border p-3">
                  <div className="text-muted-foreground">运行</div>
                  <div className="font-medium">{status.running ? '是' : '否'}</div>
                </div>
                <div className="rounded-md border p-3">
                  <div className="text-muted-foreground">航速</div>
                  <div className="font-medium">{(status.speed_mps || 0).toFixed(2)} / {speedLimitMps.toFixed(1)} m/s</div>
                  <div className="mt-2 h-1.5 rounded bg-muted">
                    <div className="h-1.5 rounded bg-emerald-500" style={{ width: `${liveSpeedPercent}%` }} />
                  </div>
                </div>
                <div className="rounded-md border p-3">
                  <div className="text-muted-foreground">航向</div>
                  <div className="font-medium">{(status.heading_deg || 0).toFixed(1)} deg</div>
                </div>
                <div className="rounded-md border p-3">
                  <div className="text-muted-foreground">到达进度</div>
                  <div className="font-medium">{mission.reached_count}/{config.mission.waypoints.length}</div>
                </div>
              </div>
              <div className="rounded-md border p-3 space-y-2">
                <div className="flex justify-between text-sm">
                  <span>左推进 (制导)</span>
                  <span className="font-mono">{(propulsion.left || 0).toFixed(2)}</span>
                </div>
                <div className="h-2 rounded bg-muted">
                  <div className="h-2 rounded bg-primary" style={{ width: `${Math.abs(propulsion.left || 0) * 100}%` }} />
                </div>
                <div className="flex justify-between text-sm">
                  <span>右推进 (制导)</span>
                  <span className="font-mono">{(propulsion.right || 0).toFixed(2)}</span>
                </div>
                <div className="h-2 rounded bg-muted">
                  <div className="h-2 rounded bg-primary" style={{ width: `${Math.abs(propulsion.right || 0) * 100}%` }} />
                </div>
              </div>
              <Button variant="outline" className="w-full" onClick={() => refresh().catch(() => {})}>
                <RotateCcw className="w-4 h-4 mr-2" />
                刷新
              </Button>
            </CardContent>
          </Card>
        </aside>

        {/* 虚拟航线编辑地图 */}
        <Card className="flex min-h-[440px] flex-col overflow-hidden sm:min-h-[520px] xl:min-h-0">
          <CardHeader className="pb-2">
            <CardTitle className="flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
              <span className="flex items-center gap-2"><MapPin className="h-5 w-5" />虚拟航线</span>
              <div className="flex flex-wrap items-center gap-2">
                <Button size="sm" variant={drawMode === 'start' ? 'default' : 'outline'}
                  onClick={() => setDrawMode((m) => (m === 'start' ? '' : 'start'))}>
                  <Navigation className="w-4 h-4 mr-1" />放起点
                </Button>
                <Button size="sm" variant={drawMode === 'waypoint' ? 'default' : 'outline'}
                  onClick={() => setDrawMode((m) => (m === 'waypoint' ? '' : 'waypoint'))}>
                  <MapPin className="w-4 h-4 mr-1" />画航点
                </Button>
                <Button size="sm" variant={drawMode === 'source' ? 'default' : 'outline'}
                  onClick={() => setDrawMode((m) => (m === 'source' ? '' : 'source'))}>
                  <Crosshair className="w-4 h-4 mr-1" />放污染源
                </Button>
                <Button size="sm" variant="outline" onClick={fitLabBounds} disabled={!hasLabBounds}>
                  <Crosshair className="w-4 h-4 mr-1" />适配范围
                </Button>
                <Button size="sm" variant="outline" onClick={() => run('import', importQgc)} disabled={!!pending}>
                  <Download className="w-4 h-4 mr-1" />导入QGC
                </Button>
                <Button size="sm" variant="outline" onClick={clearWaypoints}>
                  <Trash2 className="w-4 h-4 mr-1" />清空
                </Button>
              </div>
            </CardTitle>
          </CardHeader>
          <CardContent className="flex flex-1 min-h-0 flex-col p-3 pt-0">
            <div ref={containerRef} className="min-h-0 flex-1 w-full rounded-md border overflow-hidden" />
            <p className="mt-2 text-xs text-muted-foreground">
              {drawMode === 'start' ? '点击地图放置模拟起点，船标会立即移动到该位置。'
                : drawMode === 'waypoint' ? '点击地图添加航点。'
                : drawMode === 'source' ? '点击地图放置污染源 (切换为手动模式)。'
                : `起点 ${Number(config.sim.start_lat).toFixed(6)}, ${Number(config.sim.start_lng).toFixed(6)}；已配置 ${config.mission.waypoints.length} 个航点。`}
            </p>
          </CardContent>
        </Card>
      </div>
    </div>
  )
}

import { useCallback, useEffect, useState } from 'react'
import { Activity, Crosshair, Download, FlaskConical, Gauge, MapPin, Navigation, Play, RotateCcw, Route, Save, Settings2, Square, Trash2 } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Switch } from '@/components/ui/switch'
import { useLabMap } from '@/hooks/use-lab-map'
import { configWriteForManualStart, gcj02Input } from '@/lib/lab-coordinate-adapter'
import { speedPercent } from '@/lib/lab-map'
import type {
  LabConfig,
  LabAutoScanParams,
  LabAutoScanResponse,
  LabConfigWrite,
  LabMission,
  LabMissionWrite,
  LabNoiseConfig,
  LabPollution,
  LabSamplingMode,
  LabStatus,
  WaterArea,
  WaterAreaWrite,
} from '@/lib/lab-types'
import { fallbackConfig, fallbackStatus } from '@/lib/lab-types'

const dropletMin = 3
const dropletMax = 64

function numberOrDefault(value: unknown, fallback: number) {
  const parsed = Number(value)
  return Number.isFinite(parsed) ? parsed : fallback
}

function uiLabDefaults(config: LabConfig = fallbackConfig) {
  const analyte = config.analytes?.[0] || fallbackConfig.analytes![0]
  const source = config.sources?.[0] || fallbackConfig.sources![0]
  return {
    sampling_mode: config.sampling_mode || fallbackConfig.sampling_mode!,
    droplet_count: config.droplet_count ?? fallbackConfig.droplet_count!,
    seed: config.seed ?? fallbackConfig.seed!,
    noise: config.noise || fallbackConfig.noise!,
    analytes: [analyte],
    sources: [source],
    auto_scan: config.auto_scan || fallbackConfig.auto_scan!,
  }
}

function mergeLabUiConfig(remote: LabConfig, previous: LabConfig = fallbackConfig): LabConfig {
  const previousUi = uiLabDefaults(previous)
  return {
    ...remote,
    sampling_mode: remote.sampling_mode || previousUi.sampling_mode,
    droplet_count: remote.droplet_count ?? previousUi.droplet_count,
    seed: remote.seed ?? previousUi.seed,
    noise: remote.noise || previousUi.noise,
    analytes: remote.analytes?.length ? remote.analytes : previousUi.analytes,
    sources: remote.sources?.length ? remote.sources : previousUi.sources,
    auto_scan: remote.auto_scan || previousUi.auto_scan,
  }
}

function apiErrorMessage(json: { message?: string; error?: { code?: string; detail?: string } }) {
  if (json.message) return json.message
  if (json.error?.detail) return json.error.detail
  if (json.error?.code) return json.error.code
  return '请求失败'
}

export default function Lab() {
  const [config, setConfig] = useState<LabConfig>(() => mergeLabUiConfig(fallbackConfig))
  const [status, setStatus] = useState<LabStatus>(fallbackStatus)
  const [pending, setPending] = useState('')
  const [message, setMessage] = useState('')
  const [autoScanPreview, setAutoScanPreview] = useState<LabAutoScanResponse | null>(null)
  const setMergedConfig = useCallback<typeof setConfig>((value) => {
    setConfig((current) => {
      const next = typeof value === 'function' ? value(current) : value
      return mergeLabUiConfig(next, current)
    })
  }, [])
  const persistMission = useCallback(async (
    mission: LabMissionWrite,
  ): Promise<LabMission | null> => {
    try {
      const res = await fetch('/api/lab/mission', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(mission),
      })
      const json = await res.json()
      if (json.success && json.data) {
        return json.data
      }
      setMessage(json.message || '实验航线保存失败')
    } catch {
      setMessage('实验航线保存失败')
    }
    return null
  }, [])

  const persistConfig = useCallback(async (
    nextConfig: LabConfigWrite,
  ): Promise<LabConfig | null> => {
    try {
      const res = await fetch('/api/lab/config', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(nextConfig),
      })
      const json = await res.json()
      if (json.success && json.data) {
        return json.data
      }
      setMessage(json.message || '配置保存失败')
    } catch {
      setMessage('配置保存失败')
    }
    return null
  }, [])

  const persistWaterArea = useCallback(async (
    waterArea: WaterAreaWrite,
  ): Promise<WaterArea | null> => {
    try {
      const res = await fetch('/api/lab/water-area', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(waterArea),
      })
      const json = await res.json()
      if (json.success && json.data) {
        return json.data
      }
      setMessage(json.message || '水域范围保存失败')
    } catch {
      setMessage('水域范围保存失败')
    }
    return null
  }, [])

  const {
    containerRef,
    drawMode,
    setDrawMode,
    hasLabBounds,
    fitLabBounds,
    updateBoatPosition,
    setPreviewRoute,
    clearPreviewRoute,
    markDirty,
    clearDirty,
    canAcceptRemoteConfig,
  } = useLabMap({
    config,
    pending,
    setConfig: setMergedConfig,
    setMessage,
    persistConfig,
    persistMission,
    persistWaterArea,
  })

  const refresh = useCallback(async () => {
    const res = await fetch('/api/lab/status')
    const json = await res.json()
    if (json.data?.config && canAcceptRemoteConfig()) setConfig((current) => mergeLabUiConfig(json.data.config, current))
    if (json.data?.status) setStatus({ ...fallbackStatus, ...json.data.status })
    if (json.data?.position?.gcj02) {
      updateBoatPosition(json.data.position.gcj02, Number(json.data.position.heading_deg || json.data.status?.heading_deg || 0))
    }
  }, [canAcceptRemoteConfig, updateBoatPosition])
  const refreshQuietly = useCallback(() => {
    void refresh().catch((error: unknown) => {
      if (error instanceof Error) {
        setMessage('状态刷新失败')
        return
      }
      throw error
    })
  }, [refresh])

  useEffect(() => {
    refreshQuietly()
    const timer = window.setInterval(refreshQuietly, 1000)
    return () => window.clearInterval(timer)
  }, [refreshQuietly])

  const updateConfig = (patch: Partial<LabConfig>) => {
    markDirty()
    setConfig((c) => ({ ...c, ...patch }))
  }
  const updateSamplingMode = (sampling_mode: LabSamplingMode) => updateConfig({ sampling_mode })
  const updateDropletCount = (value: string) => updateConfig({ droplet_count: Number(value) || 0 })
  const updateSeed = (value: string) => updateConfig({ seed: Number(value) || 0 })
  const updateNoise = (patch: Partial<LabNoiseConfig>) => {
    markDirty()
    setConfig((c) => ({ ...c, noise: { ...uiLabDefaults(c).noise, ...patch } }))
  }
  const updateAutoScan = (patch: Partial<LabAutoScanParams>) => {
    markDirty()
    setConfig((c) => ({ ...c, auto_scan: { ...uiLabDefaults(c).auto_scan, ...patch } }))
  }
  const updateAnalyte = (patch: Partial<NonNullable<LabConfig['analytes']>[number]>) => {
    markDirty()
    setConfig((c) => {
      const current = uiLabDefaults(c).analytes[0]
      const next = { ...current, ...patch }
      return {
        ...c,
        analytes: [next],
        pollution: {
          ...c.pollution,
          analyte_id: next.analyte_id,
          name: next.name,
          unit: next.unit,
        },
      }
    })
  }
  const updateSource = (patch: Partial<NonNullable<LabConfig['sources']>[number]>) => {
    markDirty()
    setConfig((c) => {
      const current = uiLabDefaults(c).sources[0]
      return { ...c, sources: [{ ...current, ...patch }] }
    })
  }
  const updateSim = (key: keyof LabConfig['sim'], value: string) => {
    markDirty()
    setConfig((c) => ({ ...c, sim: { ...c.sim, [key]: Number(value) || 0 } }))
  }
  const updatePollution = (patch: Partial<LabPollution>) => {
    markDirty()
    setConfig((c) => ({ ...c, pollution: { ...c.pollution, ...patch } }))
  }

  const run = async (key: string, action: () => Promise<unknown>) => {
    if (pending) return
    setPending(key)
    setMessage('')
    try { await action() } finally { setPending('') }
  }

  const saveConfig = async () => {
    const dropletCount = numberOrDefault(config.droplet_count, 12)
    if (dropletCount < dropletMin || dropletCount > dropletMax) {
      setMessage(`后端错误: droplet_count_range ($.droplet_count) ${dropletCount}`)
      return false
    }
    const saved = await persistConfig(configWriteForManualStart(config))
    if (!saved) return false
    clearDirty()
    setConfig((current) => mergeLabUiConfig(saved, current))
    setMessage('已保存')
    return true
  }

  const clearWaypoints = async () => {
    const nextMission = { waypoints: [], center: null }
    markDirty()
    const saved = await persistMission(nextMission)
    if (!saved) return
    clearDirty()
    clearPreviewRoute()
    setAutoScanPreview(null)
    setConfig((c) => ({ ...c, mission: saved }))
  }

  const importQgc = async () => {
    const res = await fetch('/api/lab/mission/import-qgc', { method: 'POST' })
    const json = await res.json()
    setMessage(json.message || (json.success ? '已导入' : '导入失败'))
    if (json.success && json.data) {
      clearDirty()
      clearPreviewRoute()
      setAutoScanPreview(null)
      setConfig((c) => ({ ...c, mission: json.data }))
    }
  }

  const requestAutoScan = async (preview: boolean) => {
    const params = uiLabDefaults(config).auto_scan
    const request = {
      input_crs: 'GCJ02' as const,
      polygon: config.water_area.polygon.map((point) => gcj02Input(point.gcj02)),
      strip_spacing_m: params.strip_spacing_m,
      heading_deg: params.heading_deg,
      inward_margin_m: params.inward_margin_m,
      max_waypoints: params.max_waypoints,
      preview,
    }
    const res = await fetch('/api/lab/route/auto-scan', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(request),
    })
    const json = await res.json()
    if (!json.success || !json.data) {
      setMessage(apiErrorMessage(json))
      return
    }
    const data = json.data as LabAutoScanResponse
    setAutoScanPreview(data)
    if (preview) {
      setPreviewRoute(data.route_waypoints)
      setMessage(`预览已生成 ${data.waypoint_count} 个航点，当前 mission 未修改。`)
      return
    }
    clearDirty()
    clearPreviewRoute()
    setConfig((c) => ({ ...c, mission: { waypoints: data.route_waypoints, center: null } }))
    setMessage(`自动扫描航线已应用并保存：${data.waypoint_count} 个航点。`)
  }

  const start = async () => {
    const saved = await saveConfig()
    if (!saved) return
    const dropletCount = numberOrDefault(config.droplet_count, 12)
    if (dropletCount < dropletMin || dropletCount > dropletMax) return
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
  const sampling = status.sampling || fallbackStatus.sampling!
  const signal = status.signal || fallbackStatus.signal!
  const speedLimitMps = Math.max(0, Number(config.sim.max_speed_mps) || 0)
  const liveSpeedPercent = speedPercent(status.speed_mps || 0, speedLimitMps)
  const waypointTotal = Math.max(config.mission.waypoints.length, mission.total || 0)
  const isCompleted = Boolean(mission.completed)
    || (waypointTotal > 0 && mission.reached_count >= waypointTotal && !mission.active && !mission.waiting_sampling_done)
  const stage = !waypointTotal ? '未配置航点'
    : isCompleted ? '已完成'
    : status.running ? (mission.active ? `航行中 · 目标 #${mission.target_seq ?? '-'}` : '采样中')
    : sampling.active || mission.waiting_sampling_done ? '采样中'
    : '就绪'
  const samplingProgress = Math.max(0, Math.min(100, Number(sampling.progress_percent) || 0))
  const samplingDuration = Number(sampling.duration_s) || Number(config.sim.sample_dwell_s) || 0
  const latestSamplingEvent = sampling.latest_event
  const samplingDropletCount = latestSamplingEvent?.droplet_count ?? sampling.droplet_count ?? 12
  const samplingValidCount = latestSamplingEvent?.valid_count ?? sampling.valid_count ?? 0
  const samplingAggregateValue = latestSamplingEvent?.mean ?? sampling.aggregate_value ?? signal.pollution_value
  const signalValue = Number(signal.value || 0).toFixed(3)
  const absorbanceValue = Number(signal.absorbance || 0).toFixed(3)
  const pollutionValue = signal.pollution_value == null ? '--' : Number(signal.pollution_value).toFixed(3)
  const uiConfig = uiLabDefaults(config)
  const activeAnalyte = uiConfig.analytes[0]
  const activeSource = uiConfig.sources[0]
  const activeNoise = uiConfig.noise
  const autoScan = uiConfig.auto_scan
  const dropletCount = numberOrDefault(config.droplet_count, 12)
  const dropletCountInvalid = dropletCount < dropletMin || dropletCount > dropletMax

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
          {stage.startsWith('航行中') && `虚拟船自动巡航中, 已到达 ${mission.reached_count}/${waypointTotal} 点。`}
          {stage === '采样中' && `到达航点, 采样进度 ${samplingProgress.toFixed(0)}%, 预计 ${samplingDuration.toFixed(1)} 秒。`}
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
              <div className="space-y-2 rounded-md border p-3">
                <span className="text-sm font-medium">采样模式</span>
                <div className="grid grid-cols-2 rounded-md border bg-background p-1">
                  {(['waypoint', 'survey'] as const).map((mode) => (
                    <Button key={mode} size="sm" className="min-w-0" variant={uiConfig.sampling_mode === mode ? 'default' : 'ghost'}
                      onClick={() => updateSamplingMode(mode)}>
                      {mode === 'waypoint' ? '定点采样' : '走航 Survey'}
                    </Button>
                  ))}
                </div>
                <p className="text-xs text-muted-foreground">waypoint 到点等待液滴完成；survey 用覆盖航线连续走航采样。</p>
              </div>
              <label className="flex items-center justify-between gap-3 rounded-md border p-3">
                <span>限制水域范围 (剔除界外)</span>
                <Switch checked={config.water_area.enabled} onCheckedChange={(enabled) => {
                  markDirty()
                  void persistWaterArea({
                    enabled,
                    polygon: config.water_area.polygon,
                  }).then((saved) => {
                    if (!saved) return
                    clearDirty()
                    setConfig((current) => ({ ...current, water_area: saved }))
                  })
                }} />
              </label>
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
                <div className="mb-3 flex items-center gap-2 text-sm font-medium">
                  <Settings2 className="h-4 w-4" />液滴序列
                </div>
                <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
                  <div className="space-y-2">
                    <Label>droplet_count</Label>
                    <Input type="number" min={dropletMin} max={dropletMax} step={1} value={config.droplet_count ?? 12}
                      onChange={(e) => updateDropletCount(e.target.value)} />
                    <div className={dropletCountInvalid ? 'text-xs text-destructive' : 'text-xs text-muted-foreground'}>
                      后端范围 3..64；非法值会阻止启动并显示 droplet_count_range。
                    </div>
                  </div>
                  <div className="space-y-2">
                    <Label>seed</Label>
                    <Input type="number" step={1} value={config.seed ?? 1} onChange={(e) => updateSeed(e.target.value)} />
                    <div className="text-xs text-muted-foreground">固定 seed 用于重复同一组模拟采样。</div>
                  </div>
                </div>
                <div className="mt-3 rounded-md border bg-background p-3">
                  <label className="flex items-center justify-between gap-3">
                    <span className="text-sm font-medium">液滴信号噪声</span>
                    <Switch checked={activeNoise.enabled} onCheckedChange={(enabled) => updateNoise({ enabled })} />
                  </label>
                  <div className="mt-3 grid grid-cols-1 gap-3 sm:grid-cols-3">
                    <div className="space-y-2">
                      <Label>电压噪声</Label>
                      <Input type="number" min={0} step={0.001} value={activeNoise.voltage_noise} disabled={!activeNoise.enabled}
                        onChange={(e) => updateNoise({ voltage_noise: Number(e.target.value) || 0 })} />
                    </div>
                    <div className="space-y-2">
                      <Label>吸光度噪声</Label>
                      <Input type="number" min={0} step={0.001} value={activeNoise.absorbance_noise} disabled={!activeNoise.enabled}
                        onChange={(e) => updateNoise({ absorbance_noise: Number(e.target.value) || 0 })} />
                    </div>
                    <div className="space-y-2">
                      <Label>浓度噪声</Label>
                      <Input type="number" min={0} step={0.001} value={activeNoise.concentration_noise} disabled={!activeNoise.enabled}
                        onChange={(e) => updateNoise({ concentration_noise: Number(e.target.value) || 0 })} />
                    </div>
                  </div>
                </div>
              </div>

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
                  <Label>采样停留(秒)</Label>
                  <Input type="number" min={0} max={600} step={0.5} value={config.sim.sample_dwell_s} onChange={(e) => updateSim('sample_dwell_s', e.target.value)} />
                </div>
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
                  <Label>模拟污染下限</Label>
                  <Input type="number" step="0.1" value={config.pollution.value_min}
                    onChange={(e) => updatePollution({ value_min: Number(e.target.value) || 0 })} />
                </div>
                <div className="space-y-2">
                  <Label>模拟污染上限</Label>
                  <Input type="number" step="0.1" value={config.pollution.value_max}
                    onChange={(e) => updatePollution({ value_max: Number(e.target.value) || 0 })} />
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
                <div className="col-span-2 rounded-md border bg-muted/30 p-3">
                  <div className="mb-3 text-sm font-medium">多分析物 / 污染源基础配置</div>
                  <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
                    <div className="space-y-2">
                      <Label>分析物 ID</Label>
                      <Input value={activeAnalyte.analyte_id} onChange={(e) => {
                        updateAnalyte({ analyte_id: e.target.value || 'sim' })
                        updateSource({ analyte_id: e.target.value || 'sim' })
                      }} />
                    </div>
                    <div className="space-y-2">
                      <Label>显示名称</Label>
                      <Input value={activeAnalyte.name} onChange={(e) => updateAnalyte({ name: e.target.value || activeAnalyte.analyte_id })} />
                    </div>
                    <div className="space-y-2">
                      <Label>单位</Label>
                      <Input value={activeAnalyte.unit} onChange={(e) => updateAnalyte({ unit: e.target.value })} />
                    </div>
                    <div className="space-y-2">
                      <Label>污染源 ID</Label>
                      <Input value={activeSource.source_id} onChange={(e) => updateSource({ source_id: e.target.value || 'lab-source' })} />
                    </div>
                    <div className="space-y-2 sm:col-span-2">
                      <Label>峰值浓度</Label>
                      <Input type="number" min={0} step={0.1} value={activeSource.peak_concentration}
                        onChange={(e) => updateSource({ peak_concentration: Number(e.target.value) || 0 })} />
                    </div>
                  </div>
                </div>
              </div>
            </CardContent>
          </Card>

          <Card>
            <CardHeader className="pb-2">
              <CardTitle className="flex items-center gap-2 text-base">
                <Route className="h-5 w-5" />自动扫描
              </CardTitle>
            </CardHeader>
            <CardContent className="space-y-4">
              <div className="rounded-md border bg-muted/30 p-3 text-xs text-muted-foreground">
                先画水域，再预览覆盖航线；预览只画红色虚线，不保存 mission。点应用后才调用后端保存。
              </div>
              <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
                <div className="space-y-2">
                  <Label>条带间距(m)</Label>
                  <Input type="number" min={0.1} step={0.5} value={autoScan.strip_spacing_m}
                    onChange={(e) => updateAutoScan({ strip_spacing_m: Number(e.target.value) || 0 })} />
                </div>
                <div className="space-y-2">
                  <Label>扫描角度(deg)</Label>
                  <Input type="number" min={0} max={360} step={1} value={autoScan.heading_deg}
                    onChange={(e) => updateAutoScan({ heading_deg: Number(e.target.value) || 0 })} />
                </div>
                <div className="space-y-2">
                  <Label>内缩距离(m)</Label>
                  <Input type="number" min={0} step={0.5} value={autoScan.inward_margin_m}
                    onChange={(e) => updateAutoScan({ inward_margin_m: Number(e.target.value) || 0 })} />
                </div>
                <div className="space-y-2">
                  <Label>最大航点数</Label>
                  <Input type="number" min={2} step={1} value={autoScan.max_waypoints}
                    onChange={(e) => updateAutoScan({ max_waypoints: Number(e.target.value) || 0 })} />
                </div>
              </div>
              <div className="grid grid-cols-2 gap-2">
                <Button variant="outline" disabled={!!pending} onClick={() => run('auto-scan-preview', () => requestAutoScan(true))}>预览扫描</Button>
                <Button disabled={!!pending || !autoScanPreview} onClick={() => run('auto-scan-apply', () => requestAutoScan(false))}>应用扫描</Button>
              </div>
              <div className="rounded-md border p-3 text-xs text-muted-foreground">
                水域顶点 {config.water_area.polygon.length} 个；预览 {autoScanPreview ? `${autoScanPreview.waypoint_count} 点 · ${autoScanPreview.water_snapshot_hash.slice(0, 8)}` : '未生成'}
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
                  <div className="font-medium">{mission.reached_count}/{waypointTotal}</div>
                </div>
                <div className="rounded-md border p-3">
                  <div className="text-muted-foreground">任务状态</div>
                  <div className="font-medium">
                    {isCompleted ? (
                      <span className="inline-flex items-center rounded-full bg-emerald-50 px-2 py-1 text-xs font-medium text-emerald-700 ring-1 ring-inset ring-emerald-600/20">
                        已完成
                      </span>
                    ) : status.running ? (
                      <span className="inline-flex items-center rounded-full bg-blue-50 px-2 py-1 text-xs font-medium text-blue-700 ring-1 ring-inset ring-blue-700/10 animate-pulse">
                        进行中
                      </span>
                    ) : (
                      <span className="inline-flex items-center rounded-full bg-gray-50 px-2 py-1 text-xs font-medium text-gray-600 ring-1 ring-inset ring-gray-500/10">
                        未开始
                      </span>
                    )}
                  </div>
                </div>
              </div>
              <div className="rounded-md border p-3 space-y-3">
                <div className="flex items-center justify-between gap-3 text-sm">
                  <span className="font-medium">采样进度</span>
                  <span className="font-mono text-xs text-muted-foreground">
                    {sampling.progress_percent.toFixed(1)}%
                  </span>
                </div>
                <div className="h-2 rounded bg-muted">
                  <div className="h-2 rounded bg-sky-500" style={{ width: `${samplingProgress}%` }} />
                </div>
                <div className="grid grid-cols-3 gap-2 text-xs text-muted-foreground">
                  <span>用时 {sampling.elapsed_s.toFixed(1)}s</span>
                  <span>剩余 {sampling.remaining_s.toFixed(1)}s</span>
                  <span>采样停留 {sampling.duration_s.toFixed(1)}s</span>
                </div>
                <div className="grid grid-cols-3 gap-2 border-t pt-2 text-xs text-muted-foreground">
                  <span>液滴 {samplingDropletCount}</span>
                  <span>有效 {samplingValidCount}</span>
                  <span>
                    聚合 {Number.isFinite(Number(samplingAggregateValue)) ? Number(samplingAggregateValue).toFixed(3) : '-'}
                  </span>
                </div>
                <div className="text-xs text-muted-foreground">任务阶段 {sampling.mission_status || 'IDLE'}</div>
              </div>
              <div className="rounded-md border p-3 space-y-3">
                <div className="flex items-center justify-between gap-3 text-sm">
                  <span className="font-medium">虚拟信号</span>
                  <span className="text-xs text-muted-foreground">
                    {signal.simulated ? '模拟生成' : '真实/未标记'}
                  </span>
                </div>
                <div className="grid grid-cols-2 gap-3 text-sm">
                  <div>
                    <div className="text-muted-foreground">电压</div>
                    <div className="font-mono font-medium">{signalValue} V</div>
                  </div>
                  <div>
                    <div className="text-muted-foreground">吸光度</div>
                    <div className="font-mono font-medium">{absorbanceValue}</div>
                  </div>
                  <div>
                    <div className="text-muted-foreground">污染值</div>
                    <div className="font-mono font-medium">{signal.pollution_value == null ? '--' : pollutionValue}</div>
                  </div>
                  <div>
                    <div className="text-muted-foreground">航点</div>
                    <div className="font-mono font-medium">#{signal.waypoint_seq ?? '-'}</div>
                  </div>
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
              <Button variant="outline" className="w-full" onClick={refreshQuietly}>
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
                <Button size="sm" variant={drawMode === 'water_area' ? 'default' : 'outline'}
                  onClick={() => setDrawMode((m) => (m === 'water_area' ? '' : 'water_area'))}>
                  <MapPin className="w-4 h-4 mr-1 text-emerald-500" />画水域
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
                <Button size="sm" variant="outline" onClick={async () => {
                  const saved = await persistWaterArea({
                    enabled: config.water_area.enabled,
                    polygon: [],
                  })
                  if (!saved) return
                  setConfig((current) => ({ ...current, water_area: saved }))
                }}>
                  <Trash2 className="w-4 h-4 mr-1 text-emerald-500" />清空水域
                </Button>
                <Button size="sm" variant="outline" onClick={clearWaypoints}>
                  <Trash2 className="w-4 h-4 mr-1" />清空航点
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

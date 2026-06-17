import { useCallback, useEffect, useState } from 'react'
import { Activity, Crosshair, Download, FlaskConical, Gauge, MapPin, Navigation, Play, RotateCcw, Save, Square, Trash2 } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Switch } from '@/components/ui/switch'
import { useLabMap } from '@/hooks/use-lab-map'
import { speedPercent } from '@/lib/lab-map'
import type { LabConfig, LabMission, LabPollution, LabStatus } from '@/lib/lab-types'
import { fallbackConfig, fallbackStatus } from '@/lib/lab-types'

export default function Lab() {
  const [config, setConfig] = useState<LabConfig>(fallbackConfig)
  const [status, setStatus] = useState<LabStatus>(fallbackStatus)
  const [pending, setPending] = useState('')
  const [message, setMessage] = useState('')
  const persistMission = useCallback(async (mission: LabMission) => {
    try {
      const res = await fetch('/api/lab/mission', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(mission),
      })
      const json = await res.json()
      if (json.success && json.data) {
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
        setConfig(json.data)
        return true
      }
      setMessage(json.message || '配置保存失败')
    } catch {
      setMessage('配置保存失败')
    }
    return false
  }, [])

  const {
    containerRef,
    drawMode,
    setDrawMode,
    hasLabBounds,
    fitLabBounds,
    updateBoatPosition,
    markDirty,
    clearDirty,
    canAcceptRemoteConfig,
  } = useLabMap({
    config,
    pending,
    setConfig,
    setMessage,
    persistConfig,
    persistMission,
  })

  const refresh = async () => {
    const res = await fetch('/api/lab/status')
    const json = await res.json()
    if (json.data?.config && canAcceptRemoteConfig()) setConfig(json.data.config)
    if (json.data?.status) setStatus({ ...fallbackStatus, ...json.data.status })
    if (json.data?.position?.gcj02) {
      updateBoatPosition(json.data.position.gcj02, Number(json.data.position.heading_deg || json.data.status?.heading_deg || 0))
    }
  }
  const refreshQuietly = () => {
    void refresh().catch((error: unknown) => {
      if (error instanceof Error) {
        setMessage('状态刷新失败')
        return
      }
      throw error
    })
  }

  useEffect(() => {
    refreshQuietly()
    const timer = window.setInterval(refreshQuietly, 1000)
    return () => window.clearInterval(timer)
  }, [])

  const updateConfig = (patch: Partial<LabConfig>) => {
    markDirty()
    setConfig((c) => ({ ...c, ...patch }))
  }
  const updateSim = (key: keyof LabConfig['sim'], value: string) => {
    markDirty()
    setConfig((c) => ({ ...c, sim: { ...c.sim, [key]: Number(value) || 0 } }))
  }
  const updatePollution = (patch: Partial<LabPollution>) => {
    markDirty()
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
      clearDirty()
      setConfig(json.data)
    }
  }

  const clearWaypoints = async () => {
    const nextMission = { waypoints: [], center: null }
    markDirty()
    setConfig((c) => ({ ...c, mission: nextMission }))
    await persistMission(nextMission)
  }

  const importQgc = async () => {
    const res = await fetch('/api/lab/mission/import-qgc', { method: 'POST' })
    const json = await res.json()
    setMessage(json.message || (json.success ? '已导入' : '导入失败'))
    if (json.success && json.data) {
      clearDirty()
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
  const isCompleted = mission.completed;
  const stage = !config.mission.waypoints.length ? '未配置航点'
    : status.running ? (mission.active ? `航行中 · 目标 #${mission.target_seq ?? '-'}` : '采样中')
    : isCompleted ? '已完成'
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
                <span>限制水域范围 (剔除界外)</span>
                <Switch checked={config.water_area.enabled} onCheckedChange={(enabled) => {
                  const nextConfig = {
                    ...config,
                    water_area: {
                      ...config.water_area,
                      enabled,
                    },
                  }
                  updateConfig(nextConfig)
                  void fetch('/api/lab/water-area', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                      enabled,
                      polygon: config.water_area.polygon,
                    }),
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
                  const nextConfig = {
                    ...config,
                    water_area: {
                      enabled: config.water_area.enabled,
                      polygon: [],
                    },
                  }
                  setConfig(nextConfig)
                  try {
                    await fetch('/api/lab/water-area', {
                      method: 'POST',
                      headers: { 'Content-Type': 'application/json' },
                      body: JSON.stringify({
                        enabled: config.water_area.enabled,
                        polygon: [],
                      }),
                    })
                  } catch (e) {
                    console.error(e)
                  }
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

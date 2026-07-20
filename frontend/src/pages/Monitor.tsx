import { useCallback, useEffect, useMemo, useState } from 'react'
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Button } from "@/components/ui/button"
import { useAppStore, type VoltagePoint } from '@/store'
import { Activity, Zap, Play, Square, Anchor, Navigation, Pause, AlertTriangle, CheckCircle, Loader, Target } from 'lucide-react'
import { cn } from '@/lib/utils'
import { InjectionPumpCard } from '@/components/injection-pump-card'
import { LinkDiagnosticsCard } from '@/components/link-diagnostics-card'
import { SystemHealthCard } from '@/components/system-health-card'
import { VoltageCanvasChart } from '@/components/voltage-canvas-chart'

const MISSION_STATUS_MAP: Record<string, { label: string; color: string; icon: typeof Play }> = {
  IDLE:             { label: '空闲',       color: 'text-muted-foreground', icon: Square },
  NAVIGATING:       { label: '航行中',     color: 'text-blue-500',        icon: Navigation },
  WAYPOINT_REACHED: { label: '到达航点',   color: 'text-amber-500',       icon: Anchor },
  HOLDING:          { label: '保持',       color: 'text-amber-500',       icon: Pause },
  WAITING_STABLE:   { label: '稳定等待',   color: 'text-amber-500',       icon: Loader },
  SAMPLING:         { label: '采样中',     color: 'text-emerald-500',     icon: Play },
  SAMPLING_DONE:    { label: '采样完成',   color: 'text-emerald-500',     icon: CheckCircle },
  RESUMING_AUTO:    { label: '恢复航行',   color: 'text-blue-500',        icon: Navigation },
  HOLD_NO_MISSION:  { label: '无任务保持', color: 'text-muted-foreground', icon: Square },
  FAILED:           { label: '失败',       color: 'text-red-500',         icon: AlertTriangle },
  PAUSED:           { label: '已暂停',     color: 'text-amber-500',       icon: Pause },
  ABORTED:          { label: '已中止',     color: 'text-red-500',         icon: AlertTriangle },
  RUNNING:          { label: '运行中',     color: 'text-emerald-500',     icon: Play },
  COMPLETED:        { label: '已完成',     color: 'text-emerald-500',     icon: CheckCircle },
  STOPPED:          { label: '已停止',     color: 'text-muted-foreground', icon: Square },
}

function parseMissionStatus(raw: string): { state: string; waypointSeq: string; detail: string } {
  const parts = raw.split(':')
  return {
    state: parts[0] || 'IDLE',
    waypointSeq: parts[1] || '',
    detail: parts.slice(2).join(':') || '',
  }
}

const TIME_WINDOWS = [
  { label: '30 秒', value: 30_000 },
  { label: '2 分钟', value: 120_000 },
  { label: '10 分钟', value: 600_000 },
  { label: '全部', value: 0 },
] as const

export default function Monitor() {
  const connected = useAppStore((state) => state.connected)
  const pumpConnected = useAppStore((state) => state.pumpConnected)
  const missionStatus = useAppStore((state) => state.missionStatus)
  const pumpAngles = useAppStore((state) => state.pumpAngles)
  const angleTelemetry = useAppStore((state) => state.angleTelemetry)
  const currentVoltage = useAppStore((state) => state.currentVoltage)
  const currentAbsorbance = useAppStore((state) => state.currentAbsorbance)
  const currentReferenceVoltage = useAppStore((state) => state.currentReferenceVoltage)
  const spectrometerBaselineSet = useAppStore((state) => state.spectrometerBaselineSet)
  const spectrometerStatus = useAppStore((state) => state.spectrometerStatus)
  const voltageHistory = useAppStore((state) => state.voltageHistory)
  const voltageHistoryRevision = useAppStore((state) => state.voltageHistoryRevision)
  const voltageSequenceGaps = useAppStore((state) => state.voltageSequenceGaps)
  const voltageUiDropped = useAppStore((state) => state.voltageUiDropped)
  const voltageServerBacklogMs = useAppStore((state) => state.voltageServerBacklogMs)
  const refreshInjectionPumpStatus = useAppStore((state) => state.refreshInjectionPumpStatus)

  const [spectroSubmitting, setSpectroSubmitting] = useState<'start' | 'stop' | 'baseline' | null>(null)
  const [timeWindowMs, setTimeWindowMs] = useState(600_000)
  const [pausedHistory, setPausedHistory] = useState<VoltagePoint[] | null>(null)
  const [renderedCount, setRenderedCount] = useState(0)
  const [, setClock] = useState(0)

  useEffect(() => {
    refreshInjectionPumpStatus().catch(() => {})
  }, [refreshInjectionPumpStatus])

  useEffect(() => {
    const timer = setInterval(() => setClock(Date.now()), 1000)
    return () => clearInterval(timer)
  }, [])

  const handleSpectrometerCommand = async (action: 'start' | 'stop') => {
    setSpectroSubmitting(action)
    try {
      const url = action === 'start' ? '/api/spectrometer/start' : '/api/spectrometer/stop'
      await fetch(url, { method: 'POST' })
    } finally {
      setSpectroSubmitting(null)
    }
  }

  const handleSetSpectrometerBaseline = async () => {
    setSpectroSubmitting('baseline')
    try {
      await fetch('/api/spectrometer/baseline', { method: 'POST' })
    } finally {
      setSpectroSubmitting(null)
    }
  }

  const liveHistory = useMemo(() => voltageHistory.toArray(voltageHistoryRevision), [voltageHistory, voltageHistoryRevision])
  const displayedVoltageHistory = useMemo(() => {
    const points = pausedHistory ?? liveHistory
    if (timeWindowMs === 0 || points.length === 0) return points
    const cutoff = points[points.length - 1].receivedAtMs - timeWindowMs
    let start = 0
    while (start < points.length && points[start].receivedAtMs < cutoff) start += 1
    return points.slice(start)
  }, [liveHistory, pausedHistory, timeWindowMs])
  const latestPoint = liveHistory[liveHistory.length - 1]
  const latestAgeMs = latestPoint ? Math.max(0, Date.now() - latestPoint.receivedAtMs) : null
  const receiveRateHz = useMemo(() => {
    const recent = displayedVoltageHistory.slice(-100)
    if (recent.length < 2) return 0
    const elapsed = recent[recent.length - 1].receivedAtMs - recent[0].receivedAtMs
    return elapsed > 0 ? (recent.length - 1) * 1000 / elapsed : 0
  }, [displayedVoltageHistory])
  const handleRenderedCount = useCallback((count: number) => setRenderedCount(count), [])

  const spectroStatusLabels: Record<string, string> = {
    configured: '已配置，未采集',
    stopped: '已停止',
    idle: '未采集',
    disabled: '已禁用',
    acquiring: '采集中',
    baseline_set: '基线已设定',
    i2c_error: 'I2C 错误',
    not_configured: '未配置',
    saturated: '数据饱和',
  }
  const spectroStatusLabel = spectroStatusLabels[spectrometerStatus] ?? spectrometerStatus
  const hasSpectroSample = voltageHistory.length > 0
  const showSpectroPlaceholder = !hasSpectroSample
  const angleStatusLabel = !angleTelemetry.valid ? '未收到' : angleTelemetry.stale ? '陈旧' : '实时'
  const angleStatusClass = !angleTelemetry.valid
    ? 'text-muted-foreground'
    : angleTelemetry.stale
      ? 'text-amber-500'
      : 'text-emerald-500'

  return (
    <div className="max-w-7xl mx-auto space-y-6 overflow-x-hidden p-4 md:p-8">
      <header className="flex flex-col md:flex-row md:items-center justify-between gap-4">
        <div>
            <h1 className="text-2xl font-bold tracking-tight sm:text-3xl">系统监控</h1>
            <p className="text-muted-foreground">实时遥测数据与系统状态</p>
        </div>
        <div className="flex w-full min-w-0 flex-wrap items-center gap-2 sm:gap-3 md:w-auto md:justify-end">
             <Button
               className="shrink-0"
               size="sm"
               variant="outline"
               onClick={() => handleSpectrometerCommand('start')}
               disabled={spectroSubmitting !== null}
             >
               <Play className="w-4 h-4 mr-2" />开始分光
             </Button>
             <Button
               className="shrink-0"
               size="sm"
               variant="secondary"
               onClick={() => handleSpectrometerCommand('stop')}
               disabled={spectroSubmitting !== null}
             >
               <Square className="w-4 h-4 mr-2" />停止分光
             </Button>
             <Button
               className="shrink-0"
               size="sm"
               variant="outline"
               onClick={handleSetSpectrometerBaseline}
               disabled={spectroSubmitting !== null || !hasSpectroSample}
             >
               <Target className="w-4 h-4 mr-2" />设定基线
             </Button>
             <div className={cn("flex shrink-0 items-center gap-2 rounded-full border px-3 py-1 text-xs font-medium whitespace-nowrap",
                connected ? "bg-emerald-500/10 text-emerald-500 border-emerald-500/20" : "bg-red-500/10 text-red-500 border-red-500/20")}>
                <div className={cn("w-2 h-2 rounded-full", connected ? "bg-emerald-500" : "bg-red-500")} />
                {connected ? "已连接" : "未连接"}
             </div>
             <div className={cn("flex shrink-0 items-center gap-2 rounded-full border px-3 py-1 text-xs font-medium whitespace-nowrap",
                pumpConnected ? "bg-blue-500/10 text-blue-500 border-blue-500/20" : "bg-orange-500/10 text-orange-500 border-orange-500/20")}>
                <Zap className="w-3 h-3" />
                {pumpConnected ? "泵组在线" : "泵组离线"}
             </div>
        </div>
      </header>

      {/* Status Summary Cards */}
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <Card className="min-w-0 bg-card/50 backdrop-blur-sm">
            <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
                <CardTitle className="text-sm font-medium">分光计电压</CardTitle>
                <Activity className="h-4 w-4 text-muted-foreground" />
            </CardHeader>
            <CardContent>
                <div className="text-2xl font-bold">{showSpectroPlaceholder ? '--' : `${currentVoltage.toFixed(3)} V`}</div>
                <div className="text-xs text-muted-foreground mt-1">
                  {spectrometerBaselineSet && currentReferenceVoltage !== null
                    ? `参考 ${currentReferenceVoltage.toFixed(3)} V`
                    : '未设定基线'}
                </div>
            </CardContent>
        </Card>

        <Card className="min-w-0 bg-card/50 backdrop-blur-sm">
            <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
                <CardTitle className="text-sm font-medium">吸光度</CardTitle>
                <Activity className="h-4 w-4 text-muted-foreground" />
            </CardHeader>
            <CardContent>
                <div className="text-2xl font-bold">{showSpectroPlaceholder ? '--' : currentAbsorbance.toFixed(4)}</div>
                <div className="text-xs text-muted-foreground mt-1">{spectroStatusLabel}</div>
            </CardContent>
        </Card>

        {(() => {
          const parsed = parseMissionStatus(missionStatus)
          const info = MISSION_STATUS_MAP[parsed.state] ?? { label: parsed.state, color: 'text-muted-foreground', icon: Square }
          const StatusIcon = info.icon
          return (
            <Card className="min-w-0 bg-card/50 backdrop-blur-sm">
              <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
                <CardTitle className="text-sm font-medium">任务阶段</CardTitle>
                <StatusIcon className={cn("h-4 w-4", info.color)} />
              </CardHeader>
              <CardContent>
                <div className={cn("text-xl font-bold", info.color)}>{info.label}</div>
                {parsed.waypointSeq && (
                  <div className="text-xs text-muted-foreground mt-1">航点 #{parsed.waypointSeq}{parsed.detail ? ` · ${parsed.detail}` : ''}</div>
                )}
              </CardContent>
            </Card>
          )
        })()}

        <Card className="min-w-0 bg-card/50 backdrop-blur-sm">
            <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
                 <CardTitle className="text-sm font-medium">泵组角度</CardTitle>
            </CardHeader>
            <CardContent>
              <div className={cn("mb-2 text-xs font-medium", angleStatusClass)}>{angleStatusLabel}</div>
              <div className="grid grid-cols-4 gap-1">
                {Object.entries(pumpAngles).map(([axis, angle]) => (
                    <div key={axis} className="min-w-0 text-center">
                        <div className="text-xs text-muted-foreground">{axis}</div>
                        <div className="font-mono text-xs font-semibold sm:text-sm">{angle.toFixed(1)}°</div>
                    </div>
                ))}
              </div>
              <div className="mt-3 grid grid-cols-2 gap-2 text-[11px] text-muted-foreground">
                <div>ROS {angleTelemetry.age_ms ?? '--'} ms</div>
                <div>I2C {angleTelemetry.detector_angle_age_ms ?? '--'} ms</div>
              </div>
            </CardContent>
        </Card>
      </div>

      <Card className="flex h-[440px] min-w-0 flex-col overflow-hidden lg:h-[500px]">
         <CardHeader className="flex flex-col gap-3 pb-2 lg:flex-row lg:items-center lg:justify-between">
            <div>
              <CardTitle className="text-base">分光计电压</CardTitle>
              <div className="mt-1 text-xs text-muted-foreground">
                原始 {displayedVoltageHistory.length}/{voltageHistory.length} · 绘制 {renderedCount} · {receiveRateHz.toFixed(1)} Hz · 端到端 {latestAgeMs === null ? '--' : Math.round(latestAgeMs)} ms · 服务端积压 {Math.round(voltageServerBacklogMs)} ms
                {(voltageSequenceGaps > 0 || voltageUiDropped > 0) && ` · gap ${voltageSequenceGaps} / UI 丢弃 ${voltageUiDropped}`}
              </div>
            </div>
            <div className="flex flex-wrap items-center gap-2">
              {TIME_WINDOWS.map((window) => (
                <Button key={window.label} size="sm" variant={timeWindowMs === window.value ? 'secondary' : 'ghost'} onClick={() => setTimeWindowMs(window.value)}>{window.label}</Button>
              ))}
              <Button size="sm" variant="outline" onClick={() => setPausedHistory(pausedHistory ? null : liveHistory)}>
                {pausedHistory ? <Play className="mr-2 h-4 w-4" /> : <Pause className="mr-2 h-4 w-4" />}
                {pausedHistory ? '回到实时' : '暂停视图'}
              </Button>
            </div>
         </CardHeader>
         <CardContent className="min-h-0 min-w-0 flex-1 overflow-hidden">
            <VoltageCanvasChart points={displayedVoltageHistory} onRenderedCount={handleRenderedCount} />
         </CardContent>
      </Card>

      <div className="grid min-w-0 grid-cols-1 gap-6 xl:grid-cols-[minmax(0,1fr)_380px]">
        <div className="min-w-0 space-y-6">
          <LinkDiagnosticsCard />
        </div>

        <div className="min-w-0 space-y-6">
          <InjectionPumpCard />
          <SystemHealthCard />
        </div>
      </div>
    </div>
  )
}

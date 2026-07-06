import { useEffect, useMemo, useRef, useState } from 'react'
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Button } from "@/components/ui/button"
import { NumericInput } from "@/components/ui/numeric-input"
import { useAppStore } from '@/store'
import { LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer } from 'recharts'
import { Activity, Zap, Play, Square, Anchor, Navigation, Pause, AlertTriangle, CheckCircle, Loader, Target } from 'lucide-react'
import { cn } from '@/lib/utils'
import { InjectionPumpCard } from '@/components/injection-pump-card'
import { LinkDiagnosticsCard } from '@/components/link-diagnostics-card'
import { SystemHealthCard } from '@/components/system-health-card'

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

function computeAdaptiveDomain(values: number[], fallback: [number, number], minSpan: number): [number, number] {
  const finiteValues = values.filter((value) => Number.isFinite(value))
  if (finiteValues.length === 0) return fallback

  let min = Math.min(...finiteValues)
  let max = Math.max(...finiteValues)

  if (min === max) {
    const pad = Math.max(Math.abs(min) * 0.15, minSpan / 2)
    min -= pad
    max += pad
  } else {
    const span = max - min
    const pad = Math.max(span * 0.12, minSpan / 2)
    min -= pad
    max += pad
  }

  return [min, max]
}

const MIN_CHART_POINTS = 20
const MAX_CHART_POINTS = 6000
const DEFAULT_CHART_POINTS = 500

function clampChartPoints(value: number): number {
  if (!Number.isFinite(value)) return DEFAULT_CHART_POINTS
  return Math.min(MAX_CHART_POINTS, Math.max(MIN_CHART_POINTS, Math.round(value)))
}

function formatChartNumber(value: number | string | undefined): string {
  const numeric = typeof value === 'number' ? value : Number(value)
  return Number.isFinite(numeric) ? Number.parseFloat(numeric.toPrecision(4)).toString() : String(value ?? '')
}

const formatChartTooltip = (value: number | string | undefined) => formatChartNumber(value)

export default function Monitor() {
  const socket = useAppStore((state) => state.socket)
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
  const refreshInjectionPumpStatus = useAppStore((state) => state.refreshInjectionPumpStatus)

  const pidErrorsRef = useRef<Record<string, number>>({ X: 0, Y: 0, Z: 0, A: 0 })
  const [pidErrors, setPidErrors] = useState<Record<string, number>>({ X: 0, Y: 0, Z: 0, A: 0 })
  const [spectroSubmitting, setSpectroSubmitting] = useState<'start' | 'stop' | 'baseline' | null>(null)
  const [chartPointCount, setChartPointCount] = useState(DEFAULT_CHART_POINTS)

  useEffect(() => {
    if (!socket) return
    const handlePidError = (data: { motor: string; error: number }) => {
      pidErrorsRef.current[data.motor] = data.error ?? 0
    }
    socket.on('pid_error', handlePidError)
    // 每 500ms 将 ref 同步到 state 触发渲染
    const timer = setInterval(() => {
      setPidErrors({ ...pidErrorsRef.current })
    }, 500)
    return () => {
      socket.off('pid_error', handlePidError)
      clearInterval(timer)
    }
  }, [socket])

  useEffect(() => {
    refreshInjectionPumpStatus().catch(() => {})
  }, [refreshInjectionPumpStatus])

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

  const tooltipStyle = {
    backgroundColor: 'hsl(var(--card))',
    borderColor: 'hsl(var(--border))',
    borderRadius: '8px',
  }

  const displayedVoltageHistory = useMemo(
    () => voltageHistory.slice(-chartPointCount),
    [chartPointCount, voltageHistory],
  )

  const voltageDomain = useMemo<[number, number]>(() => {
    return computeAdaptiveDomain(
      displayedVoltageHistory.map((point) => point.voltage),
      [0, 5],
      0.05,
    )
  }, [displayedVoltageHistory])

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
         <CardHeader className="flex flex-col gap-3 pb-2 sm:flex-row sm:items-center sm:justify-between">
            <div>
              <CardTitle className="text-base">分光计电压</CardTitle>
              <div className="mt-1 text-xs text-muted-foreground">显示 {displayedVoltageHistory.length}/{voltageHistory.length} 个数据点</div>
            </div>
            <label className="flex items-center gap-2 text-xs text-muted-foreground">
              显示点数
              <NumericInput
                className="h-9 min-h-9 w-28"
                integer
                min={MIN_CHART_POINTS}
                max={MAX_CHART_POINTS}
                value={chartPointCount}
                onValueChange={(value) => setChartPointCount(clampChartPoints(value))}
              />
            </label>
         </CardHeader>
         <CardContent className="min-h-0 min-w-0 flex-1 overflow-hidden">
            <ResponsiveContainer width="100%" height="100%">
                <LineChart data={displayedVoltageHistory}>
                    <CartesianGrid strokeDasharray="3 3" stroke="hsl(var(--border))" opacity={0.4} />
                    <XAxis dataKey="time" stroke="hsl(var(--muted-foreground))" fontSize={11} tickLine={false} axisLine={false} interval="preserveStartEnd" />
                    <YAxis stroke="hsl(var(--muted-foreground))" fontSize={11} tickLine={false} axisLine={false} domain={voltageDomain}
                           allowDataOverflow
                           tickFormatter={formatChartNumber}
                           label={{ value: 'V', angle: -90, position: 'insideLeft', style: { fill: 'hsl(var(--muted-foreground))', fontSize: 11 } }} />
                    <Tooltip contentStyle={tooltipStyle} formatter={formatChartTooltip} />
                    <Line type="monotone" dataKey="voltage" name="电压" stroke="hsl(var(--chart-1))" strokeWidth={2} dot={false} isAnimationActive={false} />
                </LineChart>
            </ResponsiveContainer>
         </CardContent>
      </Card>

      <div className="grid min-w-0 grid-cols-1 gap-6 xl:grid-cols-[minmax(0,1fr)_380px]">
        <div className="min-w-0 space-y-6">
          <Card className="min-w-0">
            <CardHeader className="pb-2">
              <CardTitle className="text-base">PID 误差</CardTitle>
            </CardHeader>
            <CardContent>
              <div className="grid grid-cols-2 gap-3 sm:grid-cols-4 sm:gap-4">
                {(['X', 'Y', 'Z', 'A'] as const).map((axis) => {
                  const err = pidErrors[axis] ?? 0
                  const absErr = Math.abs(err)
                  return (
                    <div key={axis} className="rounded-lg border border-border/60 bg-muted/20 p-3 text-center">
                      <div className="mb-1 text-xs font-medium text-muted-foreground">{axis} 轴</div>
                      <div className={cn(
                        "font-mono text-xl font-bold",
                        absErr < 0.5 ? "text-emerald-500" : absErr < 2 ? "text-amber-500" : "text-red-500"
                      )}>
                        {err.toFixed(2)}°
                      </div>
                      <div className={cn(
                        "mt-1 text-xs",
                        absErr < 0.5 ? "text-emerald-500/70" : absErr < 2 ? "text-amber-500/70" : "text-red-500/70"
                      )}>
                        {absErr < 0.5 ? "到位" : absErr < 2 ? "调节中" : "偏差大"}
                      </div>
                    </div>
                  )
                })}
              </div>
            </CardContent>
          </Card>

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

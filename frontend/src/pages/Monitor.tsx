import { useEffect, useMemo, useRef, useState } from 'react'
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { useAppStore } from '@/store'
import { LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer } from 'recharts'
import { Activity, Zap, Play, Square, Anchor, Navigation, Pause, AlertTriangle, CheckCircle, Loader } from 'lucide-react'
import { cn } from '@/lib/utils'
import { InjectionPumpCard } from '@/components/injection-pump-card'
import { LinkDiagnosticsCard } from '@/components/link-diagnostics-card'

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

export default function Monitor() {
  const {
    socket,
    connected,
    pumpConnected,
    missionStatus,
    pumpAngles,
    currentVoltage,
    currentAbsorbance,
    voltageHistory,
    refreshInjectionPumpStatus,
  } = useAppStore()

  const pidErrorsRef = useRef<Record<string, number>>({ X: 0, Y: 0, Z: 0, A: 0 })
  const [pidErrors, setPidErrors] = useState<Record<string, number>>({ X: 0, Y: 0, Z: 0, A: 0 })

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

  const tooltipStyle = {
    backgroundColor: 'hsl(var(--card))',
    borderColor: 'hsl(var(--border))',
    borderRadius: '8px',
  }

  const voltageDomain = useMemo<[number, number]>(() => {
    return computeAdaptiveDomain(
      voltageHistory.map((point) => point.voltage),
      [0, 5],
      0.05,
    )
  }, [voltageHistory])

  const absorbanceDomain = useMemo<[number, number]>(() => {
    return computeAdaptiveDomain(
      voltageHistory.map((point) => point.absorbance),
      [-0.05, 0.05],
      0.01,
    )
  }, [voltageHistory])

  return (
    <div className="p-4 md:p-8 space-y-6 max-w-7xl mx-auto">
      <header className="flex flex-col md:flex-row md:items-center justify-between gap-4">
        <div>
            <h1 className="text-3xl font-bold tracking-tight">系统监控</h1>
            <p className="text-muted-foreground">实时遥测数据与系统状态</p>
        </div>
        <div className="flex items-center gap-3">
             <div className={cn("px-3 py-1 rounded-full text-xs font-medium border flex items-center gap-2",
                connected ? "bg-emerald-500/10 text-emerald-500 border-emerald-500/20" : "bg-red-500/10 text-red-500 border-red-500/20")}>
                <div className={cn("w-2 h-2 rounded-full", connected ? "bg-emerald-500" : "bg-red-500")} />
                {connected ? "已连接" : "未连接"}
             </div>
             <div className={cn("px-3 py-1 rounded-full text-xs font-medium border flex items-center gap-2",
                pumpConnected ? "bg-blue-500/10 text-blue-500 border-blue-500/20" : "bg-orange-500/10 text-orange-500 border-orange-500/20")}>
                <Zap className="w-3 h-3" />
                {pumpConnected ? "泵组在线" : "泵组离线"}
             </div>
        </div>
      </header>

      {/* Status Summary Cards */}
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
        <Card className="bg-card/50 backdrop-blur-sm">
            <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
                <CardTitle className="text-sm font-medium">分光计电压</CardTitle>
                <Activity className="h-4 w-4 text-muted-foreground" />
            </CardHeader>
            <CardContent>
                <div className="text-2xl font-bold">{currentVoltage.toFixed(3)} V</div>
            </CardContent>
        </Card>

        <Card className="bg-card/50 backdrop-blur-sm">
            <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
                <CardTitle className="text-sm font-medium">吸光度</CardTitle>
                <Activity className="h-4 w-4 text-muted-foreground" />
            </CardHeader>
            <CardContent>
                <div className="text-2xl font-bold">{currentAbsorbance.toFixed(4)}</div>
            </CardContent>
        </Card>

        {(() => {
          const parsed = parseMissionStatus(missionStatus)
          const info = MISSION_STATUS_MAP[parsed.state] ?? { label: parsed.state, color: 'text-muted-foreground', icon: Square }
          const StatusIcon = info.icon
          return (
            <Card className="bg-card/50 backdrop-blur-sm">
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

        <Card className="bg-card/50 backdrop-blur-sm">
            <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
                 <CardTitle className="text-sm font-medium">泵组角度</CardTitle>
            </CardHeader>
            <CardContent className="grid grid-cols-4 gap-1">
                {Object.entries(pumpAngles).map(([axis, angle]) => (
                    <div key={axis} className="text-center">
                        <div className="text-xs text-muted-foreground">{axis}</div>
                        <div className="text-sm font-mono font-semibold">{angle.toFixed(1)}°</div>
                    </div>
                ))}
            </CardContent>
        </Card>
      </div>

      {/* Charts Row */}
      <div className="grid grid-cols-1 xl:grid-cols-3 gap-6">
        <div className="xl:col-span-2 space-y-6">
          {/* Voltage Chart */}
          <Card className="h-[280px] flex flex-col">
             <CardHeader className="pb-2">
                <CardTitle className="text-base">分光计电压</CardTitle>
             </CardHeader>
             <CardContent className="flex-1 min-h-0">
                <ResponsiveContainer width="100%" height="100%">
                    <LineChart data={voltageHistory}>
                        <CartesianGrid strokeDasharray="3 3" stroke="hsl(var(--border))" opacity={0.4} />
                        <XAxis dataKey="time" stroke="hsl(var(--muted-foreground))" fontSize={11} tickLine={false} axisLine={false} />
                        <YAxis stroke="hsl(var(--muted-foreground))" fontSize={11} tickLine={false} axisLine={false} domain={voltageDomain}
                               allowDataOverflow
                               label={{ value: 'V', angle: -90, position: 'insideLeft', style: { fill: 'hsl(var(--muted-foreground))', fontSize: 11 } }} />
                        <Tooltip contentStyle={tooltipStyle} />
                        <Line type="monotone" dataKey="voltage" name="电压" stroke="hsl(var(--chart-1))" strokeWidth={2} dot={false} isAnimationActive={false} />
                    </LineChart>
                </ResponsiveContainer>
             </CardContent>
          </Card>

          {/* Absorbance Chart */}
          <Card className="h-[280px] flex flex-col">
             <CardHeader className="pb-2">
                <CardTitle className="text-base">吸光度曲线</CardTitle>
             </CardHeader>
             <CardContent className="flex-1 min-h-0">
                <ResponsiveContainer width="100%" height="100%">
                    <LineChart data={voltageHistory}>
                        <CartesianGrid strokeDasharray="3 3" stroke="hsl(var(--border))" opacity={0.4} />
                        <XAxis dataKey="time" stroke="hsl(var(--muted-foreground))" fontSize={11} tickLine={false} axisLine={false} />
                        <YAxis stroke="hsl(var(--muted-foreground))" fontSize={11} tickLine={false} axisLine={false} domain={absorbanceDomain}
                               allowDataOverflow
                               label={{ value: 'Abs', angle: -90, position: 'insideLeft', style: { fill: 'hsl(var(--muted-foreground))', fontSize: 11 } }} />
                        <Tooltip contentStyle={tooltipStyle} />
                        <Line type="monotone" dataKey="absorbance" name="吸光度" stroke="hsl(var(--chart-2))" strokeWidth={2} dot={false} isAnimationActive={false} />
                    </LineChart>
                </ResponsiveContainer>
             </CardContent>
          </Card>

          {/* PID Errors as Text */}
          <Card>
             <CardHeader className="pb-2">
                <CardTitle className="text-base">PID 误差</CardTitle>
             </CardHeader>
             <CardContent>
                <div className="grid grid-cols-4 gap-4">
                  {(['X', 'Y', 'Z', 'A'] as const).map((axis) => {
                    const err = pidErrors[axis] ?? 0
                    const absErr = Math.abs(err)
                    return (
                      <div key={axis} className="text-center p-3 rounded-lg border border-border/60 bg-muted/20">
                        <div className="text-xs text-muted-foreground font-medium mb-1">{axis} 轴</div>
                        <div className={cn(
                          "text-xl font-mono font-bold",
                          absErr < 0.5 ? "text-emerald-500" : absErr < 2 ? "text-amber-500" : "text-red-500"
                        )}>
                          {err.toFixed(2)}°
                        </div>
                        <div className={cn(
                          "text-xs mt-1",
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
        </div>

        <div className="xl:col-span-1 space-y-6">
          <InjectionPumpCard />
          <LinkDiagnosticsCard />
        </div>
      </div>
    </div>
  )
}

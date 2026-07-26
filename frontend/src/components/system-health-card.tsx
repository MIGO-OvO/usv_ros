import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { useAppStore } from '@/store'
import { cn } from '@/lib/utils'
import { Activity, AlertTriangle, CheckCircle, Cpu, HardDrive, Thermometer } from 'lucide-react'
import type { LucideIcon } from 'lucide-react'

function fmt(value: number | null | undefined, suffix = '', digits = 1) {
  return typeof value === 'number' && Number.isFinite(value) ? `${value.toFixed(digits)}${suffix}` : '--'
}

export interface VoltageDiagnostics {
  latestAgeMs: number | null
  serverBacklogMs: number
  ingressLagMs: number
  serverQueueMs: number
  sequenceGaps: number
  uiDropped: number
  staleDropped: number
  inputDrop20mv: number
  nonDetectorSamples: number
}

export function SystemHealthCard({ voltageDiagnostics }: {
  voltageDiagnostics?: VoltageDiagnostics
}) {
  const health = useAppStore((state) => state.systemHealth)
  const level = health?.health?.level || 'unknown'
  const ok = level === 'ok'
  const warn = level === 'warn'
  const nodes = health?.ros_nodes || []
  const aliveNodes = nodes.filter((node) => node.alive).length
  const spectrometer = health?.detector?.spectrometer

  return (
    <Card className="min-w-0 overflow-hidden bg-card/50 backdrop-blur-sm">
      <CardHeader className="pb-2">
        <CardTitle className="flex flex-wrap items-center justify-between gap-2 text-sm font-medium">
          <span className="flex items-center gap-2">
            <Activity className="h-4 w-4" />
            系统健康
          </span>
          <span className={cn(
            'inline-flex items-center gap-1 text-xs font-medium',
            ok ? 'text-emerald-500' : warn ? 'text-orange-500' : 'text-red-500',
          )}>
            {ok ? <CheckCircle className="h-3.5 w-3.5" /> : <AlertTriangle className="h-3.5 w-3.5" />}
            {health?.health?.summary || '等待数据'}
          </span>
        </CardTitle>
      </CardHeader>
      <CardContent className="min-w-0 text-sm">
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-6">
          <Metric icon={Thermometer} label="Jetson 温度" value={fmt(health?.jetson?.temperature_c, '°C')} />
          <Metric icon={Thermometer} label="ESP32 温度" value={fmt(health?.detector?.temperature_c, '°C')} />
          <Metric icon={Cpu} label="Jetson CPU" value={fmt(health?.jetson?.cpu_percent, '%')} />
          <Metric icon={HardDrive} label="Jetson 内存" value={fmt(health?.jetson?.memory_percent, '%')} />
          <Metric icon={HardDrive} label="ESP32 Heap" value={fmt(health?.detector?.heap_percent_free, '%')} />
          <Metric icon={Activity} label="ROS 节点" value={nodes.length ? `${aliveNodes}/${nodes.length}` : '--'} />
          <Metric icon={Activity} label="ADS CRC" value={fmt(spectrometer?.crc_error, '', 0)} />
          <Metric icon={Activity} label="ADS 重复" value={fmt(spectrometer?.duplicate, '', 0)} />
          <Metric icon={Activity} label="ADS 瞬态丢弃" value={fmt(spectrometer?.transient_drop, '', 0)} />
        </div>
        {voltageDiagnostics && (
          <div className="mt-3 border-t pt-3">
            <div className="mb-2 text-xs font-medium text-muted-foreground">分光链路</div>
            <div className="grid grid-cols-2 gap-x-4 gap-y-2 sm:grid-cols-4 lg:grid-cols-8">
              <DiagnosticMetric label="端到端" value={fmt(voltageDiagnostics.latestAgeMs, ' ms', 0)} />
              <DiagnosticMetric label="发送前" value={fmt(voltageDiagnostics.serverBacklogMs, ' ms', 0)} />
              <DiagnosticMetric label="ROS 入口" value={fmt(voltageDiagnostics.ingressLagMs, ' ms', 0)} />
              <DiagnosticMetric label="批次" value={fmt(voltageDiagnostics.serverQueueMs, ' ms', 0)} />
              <DiagnosticMetric label="Gap / UI 丢弃" value={`${voltageDiagnostics.sequenceGaps} / ${voltageDiagnostics.uiDropped}`} />
              <DiagnosticMetric label="追实时丢弃" value={`${voltageDiagnostics.staleDropped}`} />
              <DiagnosticMetric label="下冲 >20mV" value={`${voltageDiagnostics.inputDrop20mv}`} />
              <DiagnosticMetric label="非检测器源" value={`${voltageDiagnostics.nonDetectorSamples}`} />
            </div>
          </div>
        )}
      </CardContent>
    </Card>
  )
}

function Metric({ icon: Icon, label, value }: {
  icon: LucideIcon
  label: string
  value: string
}) {
  return (
    <div className="flex items-center justify-between gap-2">
      <span className="flex items-center gap-1.5 text-muted-foreground">
        <Icon className="h-3.5 w-3.5" />
        {label}
      </span>
      <span className="font-medium tabular-nums">{value}</span>
    </div>
  )
}

function DiagnosticMetric({ label, value }: { label: string; value: string }) {
  return (
    <div className="min-w-0">
      <div className="truncate text-[11px] text-muted-foreground">{label}</div>
      <div className="font-mono text-xs font-medium tabular-nums">{value}</div>
    </div>
  )
}

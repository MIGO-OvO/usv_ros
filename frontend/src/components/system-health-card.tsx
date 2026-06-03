import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { useAppStore } from '@/store'
import { cn } from '@/lib/utils'
import { Activity, AlertTriangle, CheckCircle, Cpu, HardDrive, Thermometer } from 'lucide-react'
import type { LucideIcon } from 'lucide-react'
import { Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'

function fmt(value: number | null | undefined, suffix = '', digits = 1) {
  return typeof value === 'number' && Number.isFinite(value) ? `${value.toFixed(digits)}${suffix}` : '--'
}

export function SystemHealthCard() {
  const health = useAppStore((state) => state.systemHealth)
  const history = useAppStore((state) => state.systemHealthHistory)
  const level = health?.health?.level || 'unknown'
  const ok = level === 'ok'
  const warn = level === 'warn'
  const nodes = health?.ros_nodes || []
  const aliveNodes = nodes.filter((node) => node.alive).length
  const trend = history.slice(-120).map((item) => ({
    time: item.ts ? new Date(item.ts).toLocaleTimeString() : '',
    jetson: item.jetson?.temperature_c ?? null,
    detector: item.detector?.temperature_c ?? null,
  }))

  return (
    <Card className="bg-card/50 backdrop-blur-sm">
      <CardHeader className="pb-2">
        <CardTitle className="text-sm font-medium flex items-center justify-between gap-2">
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
      <CardContent className="space-y-4 text-sm">
        <div className="grid grid-cols-2 gap-3">
          <Metric icon={Thermometer} label="Jetson 温度" value={fmt(health?.jetson?.temperature_c, '°C')} />
          <Metric icon={Thermometer} label="ESP32 温度" value={fmt(health?.detector?.temperature_c, '°C')} />
          <Metric icon={Cpu} label="Jetson CPU" value={fmt(health?.jetson?.cpu_percent, '%')} />
          <Metric icon={HardDrive} label="Jetson 内存" value={fmt(health?.jetson?.memory_percent, '%')} />
          <Metric icon={HardDrive} label="ESP32 Heap" value={fmt(health?.detector?.heap_percent_free, '%')} />
          <Metric icon={Activity} label="ROS 节点" value={nodes.length ? `${aliveNodes}/${nodes.length}` : '--'} />
        </div>

        <div className="h-28">
          <ResponsiveContainer width="100%" height="100%">
            <LineChart data={trend}>
              <XAxis dataKey="time" hide />
              <YAxis hide domain={['dataMin - 2', 'dataMax + 2']} />
              <Tooltip
                contentStyle={{
                  backgroundColor: 'hsl(var(--card))',
                  borderColor: 'hsl(var(--border))',
                  borderRadius: '8px',
                }}
              />
              <Line type="monotone" dataKey="jetson" name="Jetson" stroke="hsl(var(--chart-1))" strokeWidth={2} dot={false} isAnimationActive={false} />
              <Line type="monotone" dataKey="detector" name="ESP32" stroke="hsl(var(--chart-2))" strokeWidth={2} dot={false} isAnimationActive={false} />
            </LineChart>
          </ResponsiveContainer>
        </div>
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

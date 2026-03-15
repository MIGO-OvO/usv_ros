import { useEffect, useMemo, useState } from 'react'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Droplets, Gauge, Power, RefreshCw } from 'lucide-react'
import { useAppStore } from '@/store'
import { cn } from '@/lib/utils'

export function InjectionPumpCard() {
  const {
    connected,
    injectionPump,
    refreshInjectionPumpStatus,
    setInjectionPumpSpeed,
    turnInjectionPumpOn,
    turnInjectionPumpOff,
  } = useAppStore()
  const [speedInput, setSpeedInput] = useState('0')
  const [submitting, setSubmitting] = useState(false)

  useEffect(() => {
    setSpeedInput(String(injectionPump.speed ?? 0))
  }, [injectionPump.speed])

  const statusText = useMemo(() => {
    if (injectionPump.last_error) return '故障'
    return injectionPump.enabled ? '运行中' : '已停止'
  }, [injectionPump.enabled, injectionPump.last_error])

  const statusClass = useMemo(() => {
    if (injectionPump.last_error) return 'text-red-500 border-red-500/20 bg-red-500/10'
    return injectionPump.enabled
      ? 'text-emerald-500 border-emerald-500/20 bg-emerald-500/10'
      : 'text-muted-foreground border-border bg-muted/30'
  }, [injectionPump.enabled, injectionPump.last_error])

  const withSubmit = async (action: () => Promise<unknown>) => {
    setSubmitting(true)
    try {
      await action()
    } finally {
      setSubmitting(false)
    }
  }

  const handleSetSpeed = async () => {
    const speed = Math.max(0, Math.min(100, Number(speedInput) || 0))
    setSpeedInput(String(speed))
    await withSubmit(() => setInjectionPumpSpeed(speed))
  }

  return (
    <Card className="bg-card/50 backdrop-blur-sm border-border/60 shadow-sm">
      <CardHeader className="pb-3">
        <CardTitle className="flex items-center justify-between gap-3 text-base">
          <span className="flex items-center gap-2">
            <Droplets className="w-4 h-4 text-cyan-500" />
            进样泵控制
          </span>
          <span className={cn('px-2.5 py-1 rounded-full border text-xs font-medium', statusClass)}>
            {statusText}
          </span>
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-4">
        <div className="grid grid-cols-2 gap-3">
          <div className="rounded-lg border border-border/60 bg-background/60 p-3">
            <div className="flex items-center gap-2 text-xs text-muted-foreground mb-1">
              <Gauge className="w-3.5 h-3.5" /> 当前转速
            </div>
            <div className="text-2xl font-semibold tracking-tight">{injectionPump.speed}%</div>
          </div>
          <div className="rounded-lg border border-border/60 bg-background/60 p-3">
            <div className="flex items-center gap-2 text-xs text-muted-foreground mb-1">
              <Power className="w-3.5 h-3.5" /> 连接状态
            </div>
            <div className="text-sm font-medium">{connected ? 'WebSocket 已连接' : 'WebSocket 未连接'}</div>
          </div>
        </div>

        <div className="space-y-2">
          <Label htmlFor="injection-pump-speed">目标转速 (%)</Label>
          <div className="flex gap-2">
            <Input
              id="injection-pump-speed"
              type="number"
              min={0}
              max={100}
              value={speedInput}
              onChange={(e) => setSpeedInput(e.target.value)}
              className="font-mono"
            />
            <Button variant="outline" onClick={handleSetSpeed} disabled={submitting}>
              设置转速
            </Button>
          </div>
        </div>

        <div className="grid grid-cols-3 gap-2">
          <Button onClick={() => withSubmit(turnInjectionPumpOn)} disabled={submitting}>
            开启
          </Button>
          <Button variant="secondary" onClick={() => withSubmit(turnInjectionPumpOff)} disabled={submitting}>
            关闭
          </Button>
          <Button variant="outline" onClick={() => withSubmit(refreshInjectionPumpStatus)} disabled={submitting}>
            <RefreshCw className="w-4 h-4 mr-2" />刷新
          </Button>
        </div>

        <div className="rounded-lg border border-border/60 bg-muted/20 p-3 space-y-1 text-xs">
          <div className="text-muted-foreground">最近响应</div>
          <div className="font-mono break-all">{injectionPump.last_response || '—'}</div>
          <div className="text-muted-foreground pt-2">最近错误</div>
          <div className={cn('font-mono break-all', injectionPump.last_error ? 'text-red-500' : 'text-muted-foreground')}>
            {injectionPump.last_error || '—'}
          </div>
        </div>
      </CardContent>
    </Card>
  )
}


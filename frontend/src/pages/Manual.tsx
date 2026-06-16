import { useEffect, useMemo, useState } from 'react'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Switch } from '@/components/ui/switch'
import { useAppStore } from '@/store'
import { cn } from '@/lib/utils'
import { AlertTriangle, Power, RotateCcw, Send, Square } from 'lucide-react'

const AXES = ['X', 'Y', 'Z', 'A'] as const

type Axis = typeof AXES[number]

interface AxisDraft {
  direction: 'F' | 'B'
  speed: string
  angle: string
  continuous: boolean
}

const DEFAULT_AXIS: AxisDraft = {
  direction: 'F',
  speed: '5',
  angle: '10',
  continuous: false,
}

export default function Manual() {
  const manualStatus = useAppStore((state) => state.manualStatus)
  const controlEvents = useAppStore((state) => state.controlEvents)
  const pumpAngles = useAppStore((state) => state.pumpAngles)
  const injectionPump = useAppStore((state) => state.injectionPump)
  const refreshManualStatus = useAppStore((state) => state.refreshManualStatus)
  const setManualMode = useAppStore((state) => state.setManualMode)
  const sendManualPumpStep = useAppStore((state) => state.sendManualPumpStep)
  const stopManualPumps = useAppStore((state) => state.stopManualPumps)
  const setInjectionPumpSpeed = useAppStore((state) => state.setInjectionPumpSpeed)
  const turnInjectionPumpOn = useAppStore((state) => state.turnInjectionPumpOn)
  const turnInjectionPumpOff = useAppStore((state) => state.turnInjectionPumpOff)
  const [axisDrafts, setAxisDrafts] = useState<Record<Axis, AxisDraft>>({
    X: { ...DEFAULT_AXIS },
    Y: { ...DEFAULT_AXIS },
    Z: { ...DEFAULT_AXIS },
    A: { ...DEFAULT_AXIS },
  })
  const [pending, setPending] = useState<string | null>(null)
  const [message, setMessage] = useState('')
  const [injectionSpeed, setInjectionSpeed] = useState('60')

  useEffect(() => {
    refreshManualStatus().catch(() => {})
  }, [refreshManualStatus])

  useEffect(() => {
    setInjectionSpeed(String(injectionPump.speed || 60))
  }, [injectionPump.speed])

  const latestEvent = controlEvents[controlEvents.length - 1]
  const interlockActive = manualStatus.automation_active

  const runAction = async (key: string, action: () => Promise<{ success: boolean; message: string }>) => {
    if (pending) return
    setPending(key)
    setMessage('')
    try {
      const result = await action()
      setMessage(result.message)
    } finally {
      setPending(null)
    }
  }

  const updateAxis = (axis: Axis, patch: Partial<AxisDraft>) => {
    setAxisDrafts((state) => ({
      ...state,
      [axis]: { ...state[axis], ...patch },
    }))
  }

  const sendAxis = async (axis: Axis) => {
    const draft = axisDrafts[axis]
    const speed = Math.max(0, Math.min(100, Number(draft.speed) || 0))
    const angle = Math.max(0, Math.min(99999, Number(draft.angle) || 0))
    await runAction(`axis-${axis}`, () => sendManualPumpStep({
      axis,
      direction: draft.direction,
      speed_rpm: speed,
      angle_deg: angle,
      continuous: draft.continuous,
    }))
  }

  const statusText = useMemo(() => {
    if (manualStatus.enabled) return '手动模式'
    if (interlockActive) return '互锁中'
    return '待启用'
  }, [manualStatus.enabled, interlockActive])

  return (
    <div className="p-4 md:p-8 space-y-6 max-w-7xl mx-auto">
      <header className="flex flex-col md:flex-row md:items-center justify-between gap-4">
        <div>
          <h1 className="text-3xl font-bold tracking-tight">手动控制</h1>
          <p className="text-muted-foreground">试剂预载入与微泵角度微调</p>
        </div>
        <div className="flex items-center gap-3">
          <span className={cn(
            'px-3 py-1 rounded-full border text-sm font-medium',
            manualStatus.enabled
              ? 'bg-emerald-500/10 text-emerald-500 border-emerald-500/20'
              : interlockActive
                ? 'bg-amber-500/10 text-amber-500 border-amber-500/20'
                : 'bg-muted text-muted-foreground border-border',
          )}>
            {statusText}
          </span>
          <Button
            variant={manualStatus.enabled ? 'secondary' : 'default'}
            disabled={pending !== null}
            onClick={() => runAction('mode', () => setManualMode(!manualStatus.enabled))}
          >
            <Power className="w-4 h-4 mr-2" />
            {manualStatus.enabled ? '退出手动' : '进入手动'}
          </Button>
          <Button
            variant="destructive"
            disabled={pending !== null}
            onClick={() => runAction('stop-all', stopManualPumps)}
          >
            <Square className="w-4 h-4 mr-2" />
            停止全部
          </Button>
        </div>
      </header>

      {interlockActive && !manualStatus.enabled && (
        <Card className="border-amber-500/30 bg-amber-500/10">
          <CardContent className="py-4 flex items-center gap-3 text-amber-600">
            <AlertTriangle className="w-5 h-5" />
            自动采样正在运行，手动模式会被后端拒绝。
          </CardContent>
        </Card>
      )}

      <div className="grid grid-cols-1 xl:grid-cols-3 gap-6">
        <div className="xl:col-span-2 grid grid-cols-1 md:grid-cols-2 gap-4">
          {AXES.map((axis) => {
            const draft = axisDrafts[axis]
            return (
              <Card key={axis} className="bg-card/50 border-border/60">
                <CardHeader className="pb-3">
                  <CardTitle className="flex items-center justify-between text-base">
                    <span>{axis} 轴微泵</span>
                    <span className="font-mono text-sm text-muted-foreground">
                      {(pumpAngles[axis] ?? 0).toFixed(1)} deg
                    </span>
                  </CardTitle>
                </CardHeader>
                <CardContent className="space-y-4">
                  <div className="grid grid-cols-2 gap-3">
                    <div className="space-y-2">
                      <Label>方向</Label>
                      <div className="grid grid-cols-2 gap-2">
                        <Button
                          type="button"
                          variant={draft.direction === 'F' ? 'default' : 'outline'}
                          onClick={() => updateAxis(axis, { direction: 'F' })}
                        >
                          正转
                        </Button>
                        <Button
                          type="button"
                          variant={draft.direction === 'B' ? 'default' : 'outline'}
                          onClick={() => updateAxis(axis, { direction: 'B' })}
                        >
                          反转
                        </Button>
                      </div>
                    </div>
                    <div className="space-y-2">
                      <Label>连续</Label>
                      <div className="h-10 flex items-center">
                        <Switch
                          checked={draft.continuous}
                          onCheckedChange={(checked) => updateAxis(axis, { continuous: checked })}
                        />
                      </div>
                    </div>
                  </div>

                  <div className="grid grid-cols-2 gap-3">
                    <div className="space-y-2">
                      <Label>速度 RPM</Label>
                      <Input
                        type="number"
                        min={0}
                        max={100}
                        value={draft.speed}
                        onChange={(event) => updateAxis(axis, { speed: event.target.value })}
                        className="font-mono"
                      />
                    </div>
                    <div className="space-y-2">
                      <Label>角度 deg</Label>
                      <Input
                        type="number"
                        min={0}
                        max={99999}
                        value={draft.angle}
                        disabled={draft.continuous}
                        onChange={(event) => updateAxis(axis, { angle: event.target.value })}
                        className="font-mono"
                      />
                    </div>
                  </div>

                  <Button
                    className="w-full"
                    disabled={!manualStatus.enabled || pending !== null}
                    onClick={() => sendAxis(axis)}
                  >
                    <Send className="w-4 h-4 mr-2" />
                    {pending === `axis-${axis}` ? '发送中' : '发送'}
                  </Button>
                </CardContent>
              </Card>
            )
          })}
        </div>

        <div className="space-y-4">
          <Card className="bg-card/50 border-border/60">
            <CardHeader>
              <CardTitle className="text-base">试剂预载入</CardTitle>
            </CardHeader>
            <CardContent className="space-y-4">
              <div className="space-y-2">
                <Label>进样泵速度 (%)</Label>
                <Input
                  type="number"
                  min={0}
                  max={100}
                  value={injectionSpeed}
                  onChange={(event) => setInjectionSpeed(event.target.value)}
                  className="font-mono"
                />
              </div>
              <div className="grid grid-cols-3 gap-2">
                <Button
                  variant="outline"
                  disabled={pending !== null}
                  onClick={() => runAction('inject-set', () => setInjectionPumpSpeed(Number(injectionSpeed) || 0))}
                >
                  设置
                </Button>
                <Button
                  disabled={pending !== null}
                  onClick={() => runAction('inject-on', () => turnInjectionPumpOn(Number(injectionSpeed) || 0))}
                >
                  开启
                </Button>
                <Button
                  variant="secondary"
                  disabled={pending !== null}
                  onClick={() => runAction('inject-off', turnInjectionPumpOff)}
                >
                  关闭
                </Button>
              </div>
            </CardContent>
          </Card>

          <Card className="bg-card/50 border-border/60">
            <CardHeader>
              <CardTitle className="text-base">最近命令</CardTitle>
            </CardHeader>
            <CardContent className="space-y-3">
              {message && <div className="text-sm text-muted-foreground">{message}</div>}
              {latestEvent ? (
                <div className="rounded-lg border border-border/60 bg-muted/20 p-3 text-sm space-y-1">
                  <div className="font-mono">{latestEvent.action}</div>
                  <div className={cn(
                    'font-medium',
                    latestEvent.state === 'succeeded'
                      ? 'text-emerald-500'
                      : latestEvent.state === 'failed'
                        ? 'text-red-500'
                        : 'text-amber-500',
                  )}>
                    {latestEvent.state}
                  </div>
                  <div className="text-muted-foreground break-all">{latestEvent.message || '-'}</div>
                </div>
              ) : (
                <div className="text-sm text-muted-foreground">暂无控制事务</div>
              )}
              <Button variant="outline" className="w-full" onClick={() => refreshManualStatus()}>
                <RotateCcw className="w-4 h-4 mr-2" />
                刷新状态
              </Button>
            </CardContent>
          </Card>
        </div>
      </div>
    </div>
  )
}

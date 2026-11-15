import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Label } from '@/components/ui/label'
import { NumericInput } from '@/components/ui/numeric-input'
import { PUMP_AXES, type PumpAxis, type PreflightConfig } from '@/lib/automation-preflight'
import { useAppStore } from '@/store'
import { cn } from '@/lib/utils'

interface Props {
  motors: PumpAxis[]
  config: PreflightConfig
  disabled: boolean
  onConfig: (patch: Partial<PreflightConfig>) => void
}

export function AutomationPreflightCard({ motors, config, disabled, onConfig }: Props) {
  const { pumpAngles, angleTelemetry, connected } = useAppStore()
  const live = connected && angleTelemetry.valid && !angleTelemetry.stale
  return (
    <Card>
      <CardHeader>
        <CardTitle>启动前准备</CardTitle>
      </CardHeader>
      <CardContent className="grid gap-6 lg:grid-cols-2">
        <section aria-label="泵组实时角度" className="min-w-0 space-y-3">
          <div className="flex items-center justify-between gap-2">
            <h3 className="text-sm font-semibold">泵组角度</h3>
            <span className={cn('text-xs', live ? 'text-emerald-700 dark:text-emerald-400' : 'text-muted-foreground')}>
              {live ? '实时' : connected && angleTelemetry.valid ? '数据过期' : '未连接'}
            </span>
          </div>
          <dl className="grid grid-cols-2 gap-3 sm:grid-cols-4">
            {PUMP_AXES.map((axis) => (
              <div key={axis} className="min-w-0 space-y-2">
                <dt className={cn('text-sm', !motors.includes(axis) && 'text-muted-foreground')}>{axis} 轴</dt>
                <dd className="font-mono text-lg tabular-nums" aria-label={`${axis} 轴角度`}>
                  {live ? pumpAngles[axis] === null ? '未校准' : `${pumpAngles[axis].toFixed(1)}°` : '--'}
                </dd>
              </div>
            ))}
          </dl>
        </section>
        <fieldset disabled={disabled} className="min-w-0 space-y-3">
          <legend className="text-sm font-semibold">油相分隔</legend>
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
            <div className="space-y-2">
              <Label htmlFor="preflight-oil-axis">油相泵轴</Label>
              <select id="preflight-oil-axis" value={config.oil_axis ?? ''}
                onChange={(event) => onConfig({ oil_axis: event.target.value ? event.target.value as PumpAxis : null })}
                className="min-h-11 w-full rounded-md border border-input bg-background px-3 text-sm">
                <option value="">选择轴</option>
                {PUMP_AXES.map((axis) => <option key={axis} value={axis}>{axis} 轴</option>)}
              </select>
            </div>
            <div className="space-y-2">
              <Label htmlFor="preflight-turns">分隔圈数</Label>
              <NumericInput id="preflight-turns" value={config.separation_turns} min={0} max={10} title="0 圈跳过"
                onValueChange={(value) => onConfig({ separation_turns: value })} />
            </div>
          </div>
        </fieldset>
      </CardContent>
    </Card>
  )
}

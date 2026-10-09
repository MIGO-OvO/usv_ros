import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { NumericInput } from '@/components/ui/numeric-input'
import { PUMP_AXES, type PumpAxis, type PreflightConfig } from '@/lib/automation-preflight'

interface Props {
  motors: PumpAxis[]
  zeros: Partial<Record<PumpAxis, string>>
  config: PreflightConfig
  disabled: boolean
  onZero: (axis: PumpAxis, value: string) => void
  onConfig: (patch: Partial<PreflightConfig>) => void
}

export function AutomationPreflightCard({ motors, zeros, config, disabled, onZero, onConfig }: Props) {
  return (
    <Card>
      <CardHeader className="space-y-2">
        <CardTitle>启动前准备</CardTitle>
        <p className="text-sm text-muted-foreground">
          本次归零与补偿：<span className="font-medium text-foreground">{motors.join(' / ') || '无启用轴'}</span>
          <span className="ml-2">按序列自动统计，每次任务执行一次。</span>
        </p>
      </CardHeader>
      <CardContent className="grid gap-6 lg:grid-cols-2">
        <fieldset disabled={disabled} className="min-w-0 space-y-3">
          <legend className="text-sm font-semibold">相对零点</legend>
          <p id="relative-zero-help" className="text-xs text-muted-foreground">
            填入预先标定零点的原始角度（°）。归零使用这些已保存位置，不会重新设零。
          </p>
          <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
            {PUMP_AXES.map((axis) => (
              <div key={axis} className="min-w-0 space-y-2">
                <Label htmlFor={`preflight-zero-${axis}`}>{axis} 轴（°）</Label>
                <Input id={`preflight-zero-${axis}`} type="number" inputMode="decimal" min={0} max={359.999} step="any"
                  value={zeros[axis] ?? ''} onChange={(event) => onZero(axis, event.target.value)}
                  placeholder="未设置" aria-describedby="relative-zero-help" className="min-h-11" />
              </div>
            ))}
          </div>
        </fieldset>
        <fieldset disabled={disabled} className="min-w-0 space-y-3">
          <legend className="text-sm font-semibold">油相分隔</legend>
          <p id="separation-help" className="text-xs text-muted-foreground">360° 补偿完成后，油相泵按以下转速额外正转。圈数为 0 时跳过。</p>
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
            <div className="space-y-2">
              <Label htmlFor="preflight-oil-axis">油相泵轴</Label>
              <select id="preflight-oil-axis" value={config.oil_axis ?? ''}
                onChange={(event) => onConfig({ oil_axis: event.target.value ? event.target.value as PumpAxis : null })}
                aria-describedby="separation-help" className="min-h-11 w-full rounded-md border border-input bg-background px-3 text-sm">
                <option value="">选择轴</option>
                {PUMP_AXES.map((axis) => <option key={axis} value={axis}>{axis} 轴</option>)}
              </select>
            </div>
            <div className="space-y-2">
              <Label htmlFor="preflight-turns">分隔圈数</Label>
              <NumericInput id="preflight-turns" value={config.separation_turns} min={0} max={10}
                onValueChange={(value) => onConfig({ separation_turns: value })} aria-describedby="separation-help" />
            </div>
            <div className="space-y-2">
              <Label htmlFor="preflight-rpm">转速（rpm）</Label>
              <NumericInput id="preflight-rpm" value={config.separation_rpm} min={0.1} max={20}
                onValueChange={(value) => onConfig({ separation_rpm: value })} aria-describedby="separation-help" />
            </div>
          </div>
        </fieldset>
      </CardContent>
    </Card>
  )
}

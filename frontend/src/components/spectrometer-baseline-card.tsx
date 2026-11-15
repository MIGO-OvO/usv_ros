import { Play, Square, Target } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { type BaselineAcquisitionSummary } from '@/lib/spectrometer-baseline'
import { cn } from '@/lib/utils'

export function SpectrometerBaselineCard({
  summary, saving, canStart, baselineSet, referenceVoltage, onStart, onCancel,
}: {
  readonly summary: BaselineAcquisitionSummary | null
  readonly saving: boolean
  readonly canStart: boolean
  readonly baselineSet: boolean
  readonly referenceVoltage: number | null
  readonly onStart: () => void
  readonly onCancel: () => void
}) {
  const active = summary !== null
  const phaseLabel = saving ? '正在写入' : active ? '平均采集' : baselineSet ? '已有基线' : '未获取'

  return (
    <Card className="min-w-0 overflow-hidden shadow-none">
      <CardHeader className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <CardTitle className="flex items-center gap-2 text-sm font-medium">
          <Target className="h-4 w-4" />参考基线 · 20 秒平均
        </CardTitle>
        <div className="flex flex-wrap items-center gap-2">
          {baselineSet && referenceVoltage !== null && !active && (
            <span className="font-mono text-sm tabular-nums">{referenceVoltage.toFixed(6)} V</span>
          )}
          <span role="status" className={cn('text-xs font-medium', baselineSet && !active ? 'text-emerald-700 dark:text-emerald-400' : 'text-muted-foreground')}>{phaseLabel}</span>
          <Button size="sm" variant="outline" onClick={onStart} disabled={!canStart || active || saving}>
            <Play className="mr-2 h-4 w-4" />开始获取
          </Button>
          {active && <Button size="sm" variant="secondary" onClick={onCancel} disabled={saving}>
            <Square className="mr-2 h-4 w-4" />取消
          </Button>}
        </div>
      </CardHeader>
      {summary && <CardContent className="space-y-3">
        <div className="h-2 overflow-hidden rounded-full bg-muted" role="progressbar"
          aria-label="参考基线获取进度" aria-valuemin={0} aria-valuemax={100} aria-valuenow={Math.round(summary.progressPercent)}>
          <div className="h-full rounded-full bg-primary" style={{ width: `${summary.progressPercent}%` }} />
        </div>
        <dl className="grid grid-cols-3 gap-3 text-sm">
          <div><dt className="text-xs text-muted-foreground">剩余</dt><dd className="tabular-nums">{Math.ceil(summary.remainingMs / 1000)} 秒</dd></div>
          <div><dt className="text-xs text-muted-foreground">有效样本</dt><dd className="tabular-nums">{summary.validSampleCount}</dd></div>
          <div><dt className="text-xs text-muted-foreground">平均电压</dt><dd className="font-mono tabular-nums">{summary.averageVoltage === null ? '--' : `${summary.averageVoltage.toFixed(6)} V`}</dd></div>
        </dl>
      </CardContent>}
    </Card>
  )
}

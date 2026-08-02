import { Play, Square, Target } from 'lucide-react'

import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import type { BaselineAcquisitionSummary } from '@/lib/spectrometer-baseline'
import { cn } from '@/lib/utils'

function formatRemaining(milliseconds: number): string {
  const seconds = Math.max(0, Math.ceil(milliseconds / 1000))
  const minutes = Math.floor(seconds / 60)
  return `${String(minutes).padStart(2, '0')}:${String(seconds % 60).padStart(2, '0')}`
}

function Metric({ label, value }: { readonly label: string; readonly value: string }) {
  return (
    <div className="min-w-0 rounded-md border bg-muted/20 px-3 py-2">
      <div className="truncate text-[11px] text-muted-foreground">{label}</div>
      <div className="mt-0.5 font-mono text-sm font-semibold tabular-nums">{value}</div>
    </div>
  )
}

export function SpectrometerBaselineCard({
  summary,
  saving,
  canStart,
  baselineSet,
  referenceVoltage,
  onStart,
  onCancel,
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
  const phaseLabel = saving
    ? '正在写入基线'
    : summary?.phase === 'stabilizing'
      ? '稳定等待'
      : summary?.phase === 'averaging'
        ? '平均采集'
        : active
          ? '采集完成'
          : baselineSet
            ? '已有基线'
            : '未获取'
  const phaseClass = active || saving
    ? 'text-emerald-500'
    : baselineSet
      ? 'text-blue-500'
      : 'text-muted-foreground'
  const remainingLabel = summary?.phase === 'stabilizing' ? '距平均开始' : '距流程完成'

  return (
    <Card className="min-w-0 overflow-hidden bg-card/50 backdrop-blur-sm">
      <CardHeader className="grid min-w-0 gap-3 pb-3 lg:grid-cols-[minmax(0,1fr)_auto] lg:items-center">
        <div className="min-w-0">
          <CardTitle className="flex items-center gap-2 text-sm font-medium">
            <Target className="h-4 w-4" />
            参考基线获取
          </CardTitle>
          <p className="mt-1 text-xs text-muted-foreground">
            油相完全通入流通池后开始；自动启动分光，先稳定 5 分钟，再对随后 1 分钟的有效电压求平均。
          </p>
        </div>
        <div className="flex min-w-0 flex-wrap items-center gap-2 lg:justify-end">
          <span className={cn('mr-1 text-xs font-medium', phaseClass)}>{phaseLabel}</span>
          <Button size="sm" variant="outline" onClick={onStart} disabled={!canStart || active || saving}>
            <Play className="mr-2 h-4 w-4" />
            开始获取
          </Button>
          <Button size="sm" variant="secondary" onClick={onCancel} disabled={!active || saving}>
            <Square className="mr-2 h-4 w-4" />
            取消
          </Button>
        </div>
      </CardHeader>
      <CardContent className="space-y-3">
        <div
          className="h-2 overflow-hidden rounded-full bg-muted"
          role="progressbar"
          aria-label="参考基线获取进度"
          aria-valuemin={0}
          aria-valuemax={100}
          aria-valuenow={Math.round(summary?.progressPercent ?? (baselineSet ? 100 : 0))}
        >
          <div
            className="h-full rounded-full bg-emerald-500 transition-[width] duration-500"
            style={{ width: `${summary?.progressPercent ?? (baselineSet ? 100 : 0)}%` }}
          />
        </div>
        <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
          <Metric label="当前阶段" value={phaseLabel} />
          <Metric label={remainingLabel} value={active ? formatRemaining(summary?.remainingMs ?? 0) : '--:--'} />
          <Metric label="平均窗口有效样本" value={String(summary?.validSampleCount ?? 0)} />
          <Metric
            label={active ? '窗口实时均值' : '当前参考电压'}
            value={active
              ? summary?.averageVoltage === null ? '--' : `${summary.averageVoltage.toFixed(6)} V`
              : baselineSet && referenceVoltage !== null ? `${referenceVoltage.toFixed(6)} V` : '--'}
          />
        </div>
      </CardContent>
    </Card>
  )
}

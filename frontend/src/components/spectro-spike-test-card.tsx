import { Activity, Download, Play, Square } from 'lucide-react'

import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { cn } from '@/lib/utils'
import type { SpikeTestSummary } from '@/lib/spectro-spike-test'

function counter(value: number | null | undefined): string {
  return typeof value === 'number' ? String(value) : '--'
}

function Metric({ label, value }: { readonly label: string; readonly value: string }) {
  return (
    <div className="min-w-0 rounded-md border bg-muted/20 px-3 py-2">
      <div className="truncate text-[11px] text-muted-foreground">{label}</div>
      <div className="mt-0.5 font-mono text-sm font-semibold tabular-nums">{value}</div>
    </div>
  )
}

export function SpectroSpikeTestCard({
  summary,
  canStart,
  onStart,
  onStop,
  onExport,
}: {
  readonly summary: SpikeTestSummary | null
  readonly canStart: boolean
  readonly onStart: () => void
  readonly onStop: () => void
  readonly onExport: () => void
}) {
  const active = summary?.active ?? false
  const completed = !!summary && !summary.active
  const status = active ? '测试中' : completed ? '已结束' : '未开始'
  const statusClass = active
    ? 'text-emerald-500'
    : completed
      ? 'text-blue-500'
      : 'text-muted-foreground'

  return (
    <Card className="min-w-0 overflow-hidden bg-card/50 backdrop-blur-sm">
      <CardHeader className="grid min-w-0 gap-3 pb-3 lg:grid-cols-[minmax(0,1fr)_auto] lg:items-center">
        <div className="min-w-0">
          <CardTitle className="flex items-center gap-2 text-sm font-medium">
            <Activity className="h-4 w-4" />
            毛刺测试
          </CardTitle>
          <p className="mt-1 text-xs text-muted-foreground">
            统计本次会话相邻有效样本的向下跳变；ADS 指标显示开始至结束的计数增量。
          </p>
        </div>
        <div className="flex min-w-0 flex-wrap items-center gap-2 lg:justify-end">
          <span className={cn('mr-1 text-xs font-medium', statusClass)}>
            {status}
            {summary?.counterResetDetected ? ' · ADS 计数器已重置' : ''}
          </span>
          <Button size="sm" variant="outline" onClick={onStart} disabled={!canStart || active}>
            <Play className="mr-2 h-4 w-4" />
            开始测试
          </Button>
          <Button size="sm" variant="secondary" onClick={onStop} disabled={!active}>
            <Square className="mr-2 h-4 w-4" />
            结束测试
          </Button>
          <Button
            size="sm"
            variant="outline"
            onClick={onExport}
            disabled={!completed || (summary?.sampleCount ?? 0) === 0}
          >
            <Download className="mr-2 h-4 w-4" />
            导出结果
          </Button>
        </div>
      </CardHeader>
      <CardContent>
        <div className="grid grid-cols-2 gap-2 sm:grid-cols-3 lg:grid-cols-5 xl:grid-cols-10">
          <Metric label="测试时长" value={`${(summary?.durationS ?? 0).toFixed(1)} s`} />
          <Metric
            label="有效样本 / 频率"
            value={`${summary?.sampleCount ?? 0} / ${summary?.receiveRateHz?.toFixed(1) ?? '--'} Hz`}
          />
          <Metric label="下冲 >5mV" value={String(summary?.dropCount5mv ?? 0)} />
          <Metric label="下冲 >10mV" value={String(summary?.dropCount10mv ?? 0)} />
          <Metric label="下冲 >20mV" value={String(summary?.dropCount20mv ?? 0)} />
          <Metric label="最大下冲" value={`${(summary?.maxDownMv ?? 0).toFixed(2)} mV`} />
          <Metric label="ADS CRC Δ" value={counter(summary?.adsCrcErrorDelta)} />
          <Metric label="ADS 重复 Δ" value={counter(summary?.adsDuplicateDelta)} />
          <Metric label="ADS 瞬态 Δ" value={counter(summary?.adsTransientDropDelta)} />
          <Metric
            label="会话状态"
            value={summary?.counterResetDetected ? '计数重置' : active ? '采集中' : completed ? '已冻结' : '--'}
          />
        </div>
      </CardContent>
    </Card>
  )
}

import { Activity, Download, Play, Square } from 'lucide-react'

import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { cn } from '@/lib/utils'
import {
  SPIKE_TEST_DURATION_OPTIONS_S,
  type SpikeTestSummary,
} from '@/lib/spectro-spike-test'

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

function durationLabel(durationS: number): string {
  return durationS % 60 === 0 ? `${durationS / 60} 分钟` : `${durationS} 秒`
}

export function SpectroSpikeTestCard({
  summary,
  canStart,
  durationS,
  onDurationChange,
  onStart,
  onStop,
  onExport,
}: {
  readonly summary: SpikeTestSummary | null
  readonly canStart: boolean
  readonly durationS: number
  readonly onDurationChange: (durationS: number) => void
  readonly onStart: () => void
  readonly onStop: () => void
  readonly onExport: () => void
}) {
  const active = summary?.active ?? false
  const completed = !!summary && !summary.active
  const autoCompleted = completed
    && (summary?.durationS ?? 0) >= (summary?.targetDurationS ?? Number.POSITIVE_INFINITY)
  const status = active ? '测试中' : autoCompleted ? '已完成' : completed ? '已结束' : '未开始'
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
            两端使用相同测试时长，到时自动结束；统计相邻有效样本的向下跳变和 ADS 计数增量。
          </p>
        </div>
        <div className="flex min-w-0 flex-wrap items-center gap-2 lg:justify-end">
          <span className={cn('mr-1 text-xs font-medium', statusClass)}>
            {status}
            {summary?.counterResetDetected ? ' · ADS 计数器已重置' : ''}
          </span>
          <label className="flex h-9 items-center gap-2 rounded-md border border-input bg-background px-3 text-xs">
            <span className="text-muted-foreground">测试时长</span>
            <select
              aria-label="毛刺测试时长"
              className="bg-transparent font-medium outline-none"
              value={durationS}
              onChange={(event) => onDurationChange(Number(event.target.value))}
              disabled={active}
            >
              {SPIKE_TEST_DURATION_OPTIONS_S.map((optionS) => (
                <option key={optionS} value={optionS}>
                  {durationLabel(optionS)}
                </option>
              ))}
            </select>
          </label>
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
          <Metric
            label="进度 / 剩余"
            value={`${(summary?.durationS ?? 0).toFixed(1)}/${summary?.targetDurationS ?? durationS} · 余 ${(summary?.remainingS ?? durationS).toFixed(1)} s`}
          />
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

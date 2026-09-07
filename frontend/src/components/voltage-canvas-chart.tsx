import type { VoltagePoint } from '@/store'
import type { TimeSeriesRange } from '@/lib/time-series/chart-utils'
import { TimeSeriesCanvasChart } from './time-series-canvas-chart'

const VOLTAGE_FALLBACK_DOMAIN: readonly [number, number] = [0, 5]
const voltageValueAccessor = (point: VoltagePoint) => point.voltage
const formatVoltageValue = (value: number) => value.toPrecision(5)
const voltageHoverDetails = (point: VoltagePoint) => [
  `seq ${point.seq} · source ${point.sourceTimestampMs} ms`,
]

export function VoltageCanvasChart({ points, timeRange, onRenderedCount }: {
  readonly points: readonly VoltagePoint[]
  readonly timeRange?: TimeSeriesRange | null
  readonly onRenderedCount?: (count: number) => void
}) {
  return (
    <TimeSeriesCanvasChart
      points={points}
      valueAccessor={voltageValueAccessor}
      valueLabel="分光计电压"
      unit="V"
      ariaLabel="分光计电压时序图"
      emptyState={{
        title: '暂无电压历史数据',
        description: '新数据到达后将自动恢复绘制',
      }}
      formatValue={formatVoltageValue}
      hoverDetails={voltageHoverDetails}
      timeRange={timeRange}
      fallbackDomain={VOLTAGE_FALLBACK_DOMAIN}
      lineColor="--chart-1"
      onRenderedCount={onRenderedCount}
    />
  )
}

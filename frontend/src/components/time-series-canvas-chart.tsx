import { useEffect, useMemo, useRef, useState } from 'react'
import { minMaxDownsample } from '@/lib/time-series/min-max-downsample'
import {
  calculateTimeSeriesDomain,
  getTimeSeriesRange,
  isFiniteTimeSeriesValue,
  type TimeSeriesPoint,
  type TimeSeriesRange,
  type TimeSeriesValueAccessor,
} from '@/lib/time-series/chart-utils'

type Size = { width: number; height: number }

const MARGINS = { left: 58, right: 14, top: 12, bottom: 30 } as const
const DEFAULT_FALLBACK_DOMAIN: readonly [number, number] = [0, 1]

function cssColor(name: string, fallback: string) {
  const value = getComputedStyle(document.documentElement).getPropertyValue(name).trim()
  return value ? `hsl(${value})` : fallback
}

function formatTime(timestampMs: number) {
  return new Date(timestampMs).toLocaleTimeString([], { fractionalSecondDigits: 3 })
}

export interface TimeSeriesEmptyState {
  readonly title: string
  readonly description: string
}

export interface TimeSeriesCanvasChartProps<T extends TimeSeriesPoint> {
  readonly points: readonly T[]
  readonly valueAccessor: TimeSeriesValueAccessor<T>
  readonly valueLabel: string
  readonly unit: string
  readonly ariaLabel: string
  readonly emptyState: TimeSeriesEmptyState
  readonly formatValue?: (value: number) => string
  readonly hoverDetails?: (point: T) => readonly string[]
  readonly timeRange?: TimeSeriesRange | null
  readonly fallbackDomain?: readonly [number, number]
  readonly lineColor?: string
  readonly onRenderedCount?: (count: number) => void
}

export function TimeSeriesCanvasChart<T extends TimeSeriesPoint>({
  points,
  valueAccessor,
  valueLabel,
  unit,
  ariaLabel,
  emptyState,
  formatValue = (value) => value.toPrecision(5),
  hoverDetails,
  timeRange,
  fallbackDomain = DEFAULT_FALLBACK_DOMAIN,
  lineColor = '--chart-1',
  onRenderedCount,
}: TimeSeriesCanvasChartProps<T>) {
  const hostRef = useRef<HTMLDivElement>(null)
  const canvasRef = useRef<HTMLCanvasElement>(null)
  const [size, setSize] = useState<Size>({ width: 0, height: 0 })
  const [hover, setHover] = useState<T | null>(null)
  const [themeRevision, setThemeRevision] = useState(0)
  const rendered = useMemo(
    () => minMaxDownsample(points, Math.max(1, size.width - MARGINS.left - MARGINS.right), valueAccessor),
    [points, size.width, valueAccessor],
  )
  const chartRange = useMemo(() => {
    if (
      timeRange
      && Number.isFinite(timeRange.startMs)
      && Number.isFinite(timeRange.endMs)
    ) {
      return timeRange
    }
    return getTimeSeriesRange(rendered)
  }, [rendered, timeRange])

  useEffect(() => {
    const host = hostRef.current
    if (!host) return
    const resize = () => setSize({ width: host.clientWidth, height: host.clientHeight })
    resize()
    const observer = new ResizeObserver(resize)
    observer.observe(host)
    return () => observer.disconnect()
  }, [])

  useEffect(() => {
    const observer = new MutationObserver(() => setThemeRevision((value) => value + 1))
    observer.observe(document.documentElement, { attributes: true, attributeFilter: ['class'] })
    return () => observer.disconnect()
  }, [])

  useEffect(() => onRenderedCount?.(rendered.length), [onRenderedCount, rendered.length])

  useEffect(() => {
    const canvas = canvasRef.current
    if (!canvas || size.width < 2 || size.height < 2) return
    const ratio = Math.min(window.devicePixelRatio || 1, 2)
    canvas.width = Math.round(size.width * ratio)
    canvas.height = Math.round(size.height * ratio)
    canvas.style.width = `${size.width}px`
    canvas.style.height = `${size.height}px`
    const context = canvas.getContext('2d')
    if (!context) return
    context.setTransform(ratio, 0, 0, ratio, 0, 0)
    context.clearRect(0, 0, size.width, size.height)

    const width = Math.max(1, size.width - MARGINS.left - MARGINS.right)
    const height = Math.max(1, size.height - MARGINS.top - MARGINS.bottom)
    const foreground = cssColor('--muted-foreground', '#6b7280')
    const grid = cssColor('--border', '#d1d5db')
    const line = cssColor(lineColor, '#2563eb')
    const [yMin, yMax] = calculateTimeSeriesDomain(rendered, valueAccessor, fallbackDomain)
    const xMin = chartRange?.startMs ?? rendered[0]?.receivedAtMs ?? Date.now()
    const xMax = Math.max(xMin + 1, chartRange?.endMs ?? rendered[rendered.length - 1]?.receivedAtMs ?? xMin + 1)
    const x = (value: number) => MARGINS.left + (value - xMin) / (xMax - xMin) * width
    const y = (value: number) => MARGINS.top + (yMax - value) / (yMax - yMin) * height

    context.font = '11px sans-serif'
    context.fillStyle = foreground
    context.strokeStyle = grid
    context.lineWidth = 1
    context.globalAlpha = 0.45
    for (let index = 0; index <= 4; index += 1) {
      const py = MARGINS.top + height * index / 4
      context.beginPath()
      context.moveTo(MARGINS.left, py)
      context.lineTo(MARGINS.left + width, py)
      context.stroke()
      context.globalAlpha = 1
      const value = yMax - (yMax - yMin) * index / 4
      context.fillText(Number.parseFloat(value.toPrecision(4)).toString(), 4, py + 4)
      context.globalAlpha = 0.45
    }
    context.globalAlpha = 1
    context.fillText(formatTime(xMin), MARGINS.left, size.height - 8)
    const endLabel = formatTime(xMax)
    const endWidth = context.measureText(endLabel).width
    context.fillText(endLabel, size.width - MARGINS.right - endWidth, size.height - 8)
    context.fillText(unit, MARGINS.left - 16, MARGINS.top + 4)

    let started = false
    context.beginPath()
    for (const point of rendered) {
      const value = valueAccessor(point)
      if (!isFiniteTimeSeriesValue(value)) continue
      if (!started) {
        context.moveTo(x(point.receivedAtMs), y(value))
        started = true
      } else {
        context.lineTo(x(point.receivedAtMs), y(value))
      }
    }
    if (!started) return
    context.strokeStyle = line
    context.lineWidth = 2
    context.lineJoin = 'round'
    context.stroke()
  }, [chartRange, fallbackDomain, lineColor, rendered, size, themeRevision, unit, valueAccessor])

  const handlePointerMove = (clientX: number) => {
    const canvas = canvasRef.current
    if (!canvas || rendered.length === 0 || !chartRange) return
    const ratio = Math.max(
      0,
      Math.min(
        1,
        (clientX - canvas.getBoundingClientRect().left - MARGINS.left) / Math.max(1, size.width - MARGINS.left - MARGINS.right),
      ),
    )
    const target = chartRange.startMs + ratio * (chartRange.endMs - chartRange.startMs)
    let low = 0
    let high = rendered.length - 1
    while (low < high) {
      const middle = Math.floor((low + high) / 2)
      if (rendered[middle].receivedAtMs < target) low = middle + 1
      else high = middle
    }
    const previous = rendered[Math.max(0, low - 1)]
    setHover(Math.abs(previous.receivedAtMs - target) < Math.abs(rendered[low].receivedAtMs - target) ? previous : rendered[low])
  }

  const hoverValue = hover ? valueAccessor(hover) : null
  const hoverLines = hover && hoverDetails ? hoverDetails(hover) : []

  return (
    <div
      ref={hostRef}
      className="relative h-full w-full"
      onPointerMove={(event) => handlePointerMove(event.clientX)}
      onPointerLeave={() => setHover(null)}
    >
      <canvas
        ref={canvasRef}
        role="img"
        aria-label={`${ariaLabel}，共 ${points.length} 个原始样本，${rendered.length} 个可绘制样本`}
      />
      {rendered.length === 0 && (
        <div className="pointer-events-none absolute inset-0 flex items-center justify-center px-4 text-center" role="status">
          <div>
            <div className="text-sm font-medium text-muted-foreground">{emptyState.title}</div>
            <div className="mt-1 text-xs text-muted-foreground/80">{emptyState.description}</div>
          </div>
        </div>
      )}
      {hover && isFiniteTimeSeriesValue(hoverValue) && (
        <div className="pointer-events-none absolute right-3 top-3 rounded-md border bg-card/95 px-3 py-2 text-xs shadow-sm">
          <div>{formatTime(hover.receivedAtMs)}</div>
          <div className="font-mono font-semibold" aria-label={`${valueLabel} ${formatValue(hoverValue)} ${unit}`}>
            {formatValue(hoverValue)} {unit}
          </div>
          {hoverLines.map((lineText) => (
            <div key={lineText} className="text-muted-foreground">{lineText}</div>
          ))}
        </div>
      )}
    </div>
  )
}

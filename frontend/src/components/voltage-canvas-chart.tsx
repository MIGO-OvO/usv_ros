import { useEffect, useMemo, useRef, useState } from 'react'
import type { VoltagePoint } from '@/store'
import { minMaxDownsample } from '@/lib/time-series/min-max-downsample'

type Size = { width: number; height: number }

function cssColor(name: string, fallback: string) {
  const value = getComputedStyle(document.documentElement).getPropertyValue(name).trim()
  return value ? `hsl(${value})` : fallback
}

function voltageDomain(points: readonly VoltagePoint[]): [number, number] {
  let min = Infinity
  let max = -Infinity
  for (const point of points) {
    min = Math.min(min, point.voltage)
    max = Math.max(max, point.voltage)
  }
  if (!Number.isFinite(min) || !Number.isFinite(max)) return [0, 5]
  const span = Math.max(max - min, 0.05)
  const pad = span * 0.12
  return [min - pad, max + pad]
}

export function VoltageCanvasChart({ points, onRenderedCount }: {
  readonly points: readonly VoltagePoint[]
  readonly onRenderedCount?: (count: number) => void
}) {
  const hostRef = useRef<HTMLDivElement>(null)
  const canvasRef = useRef<HTMLCanvasElement>(null)
  const [size, setSize] = useState<Size>({ width: 0, height: 0 })
  const [hover, setHover] = useState<VoltagePoint | null>(null)
  const [themeRevision, setThemeRevision] = useState(0)
  const rendered = useMemo(() => minMaxDownsample(points, Math.max(1, size.width - 72)), [points, size.width])

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

    const margin = { left: 58, right: 14, top: 12, bottom: 30 }
    const width = Math.max(1, size.width - margin.left - margin.right)
    const height = Math.max(1, size.height - margin.top - margin.bottom)
    const foreground = cssColor('--muted-foreground', '#6b7280')
    const grid = cssColor('--border', '#d1d5db')
    const line = cssColor('--chart-1', '#2563eb')
    const [yMin, yMax] = voltageDomain(rendered)
    const xMin = rendered[0]?.receivedAtMs ?? Date.now()
    const xMax = Math.max(xMin + 1, rendered[rendered.length - 1]?.receivedAtMs ?? xMin + 1)
    const x = (value: number) => margin.left + (value - xMin) / (xMax - xMin) * width
    const y = (value: number) => margin.top + (yMax - value) / (yMax - yMin) * height

    context.font = '11px sans-serif'
    context.fillStyle = foreground
    context.strokeStyle = grid
    context.lineWidth = 1
    context.globalAlpha = 0.45
    for (let index = 0; index <= 4; index += 1) {
      const py = margin.top + height * index / 4
      context.beginPath()
      context.moveTo(margin.left, py)
      context.lineTo(margin.left + width, py)
      context.stroke()
      context.globalAlpha = 1
      const value = yMax - (yMax - yMin) * index / 4
      context.fillText(Number.parseFloat(value.toPrecision(4)).toString(), 4, py + 4)
      context.globalAlpha = 0.45
    }
    context.globalAlpha = 1
    context.fillText(new Date(xMin).toLocaleTimeString(), margin.left, size.height - 8)
    const endLabel = new Date(xMax).toLocaleTimeString()
    const endWidth = context.measureText(endLabel).width
    context.fillText(endLabel, size.width - margin.right - endWidth, size.height - 8)
    context.fillText('V', margin.left - 16, margin.top + 4)

    if (rendered.length === 0) return
    context.beginPath()
    for (let index = 0; index < rendered.length; index += 1) {
      const point = rendered[index]
      if (index === 0) context.moveTo(x(point.receivedAtMs), y(point.voltage))
      else context.lineTo(x(point.receivedAtMs), y(point.voltage))
    }
    context.strokeStyle = line
    context.lineWidth = 2
    context.lineJoin = 'round'
    context.stroke()
  }, [rendered, size, themeRevision])

  const handlePointerMove = (clientX: number) => {
    const canvas = canvasRef.current
    if (!canvas || rendered.length === 0) return
    const ratio = Math.max(0, Math.min(1, (clientX - canvas.getBoundingClientRect().left - 58) / Math.max(1, size.width - 72)))
    const target = rendered[0].receivedAtMs + ratio * (rendered[rendered.length - 1].receivedAtMs - rendered[0].receivedAtMs)
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

  return (
    <div ref={hostRef} className="relative h-full w-full" onPointerMove={(event) => handlePointerMove(event.clientX)} onPointerLeave={() => setHover(null)}>
      <canvas ref={canvasRef} role="img" aria-label={`分光计电压时序图，共 ${points.length} 个原始样本`} />
      {points.length === 0 && (
        <div className="pointer-events-none absolute inset-0 flex items-center justify-center px-4 text-center" role="status">
          <div>
            <div className="text-sm font-medium text-muted-foreground">暂无电压历史数据</div>
            <div className="mt-1 text-xs text-muted-foreground/80">新数据到达后将自动恢复绘制</div>
          </div>
        </div>
      )}
      {hover && (
        <div className="pointer-events-none absolute right-3 top-3 rounded-md border bg-card/95 px-3 py-2 text-xs shadow-sm">
          <div>{new Date(hover.receivedAtMs).toLocaleTimeString([], { fractionalSecondDigits: 3 })}</div>
          <div className="font-mono font-semibold">{hover.voltage.toPrecision(5)} V</div>
          <div className="text-muted-foreground">seq {hover.seq} · source {hover.sourceTimestampMs} ms</div>
        </div>
      )}
    </div>
  )
}

export type TimeSeriesPoint = {
  readonly receivedAtMs: number
}

export type TimeSeriesValue = number | null | undefined

export type TimeSeriesValueAccessor<T> = (point: T) => TimeSeriesValue

export interface TimeSeriesRange {
  readonly startMs: number
  readonly endMs: number
}

export function isFiniteTimeSeriesValue(value: TimeSeriesValue): value is number {
  return typeof value === 'number' && Number.isFinite(value)
}

export function calculateTimeSeriesDomain<T extends TimeSeriesPoint>(
  points: readonly T[],
  valueAccessor: TimeSeriesValueAccessor<T>,
  fallback: readonly [number, number],
  minimumSpan = 0.05,
  paddingRatio = 0.12,
): [number, number] {
  let min = Infinity
  let max = -Infinity
  for (const point of points) {
    const value = valueAccessor(point)
    if (!isFiniteTimeSeriesValue(value)) continue
    min = Math.min(min, value)
    max = Math.max(max, value)
  }

  if (!Number.isFinite(min) || !Number.isFinite(max)) return [fallback[0], fallback[1]]
  const span = Math.max(max - min, minimumSpan)
  const padding = span * paddingRatio
  return [min - padding, max + padding]
}

export function getTimeSeriesRange<T extends TimeSeriesPoint>(points: readonly T[]): TimeSeriesRange | null {
  let startMs = Infinity
  let endMs = -Infinity
  for (const point of points) {
    if (!Number.isFinite(point.receivedAtMs)) continue
    startMs = Math.min(startMs, point.receivedAtMs)
    endMs = Math.max(endMs, point.receivedAtMs)
  }
  if (!Number.isFinite(startMs) || !Number.isFinite(endMs)) return null
  return { startMs, endMs }
}

export function selectTimeWindow<T extends TimeSeriesPoint>(
  points: readonly T[],
  timeWindowMs: number,
): readonly T[] {
  if (timeWindowMs <= 0 || points.length === 0) return points
  const endMs = points[points.length - 1].receivedAtMs
  if (!Number.isFinite(endMs)) return points

  const cutoff = endMs - timeWindowMs
  let start = 0
  while (start < points.length && points[start].receivedAtMs < cutoff) start += 1
  return start === 0 ? points : points.slice(start)
}

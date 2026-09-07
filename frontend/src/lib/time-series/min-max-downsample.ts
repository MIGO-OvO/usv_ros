import { isFiniteTimeSeriesValue, type TimeSeriesPoint, type TimeSeriesValueAccessor } from './chart-utils.ts'

type NumericPoint = TimeSeriesPoint & { readonly voltage: number }

interface ValidEntry<T> {
  readonly point: T
  readonly index: number
  readonly value: number
}

export function minMaxDownsample<T extends TimeSeriesPoint>(
  points: readonly T[],
  pixelWidth: number,
  valueAccessor?: TimeSeriesValueAccessor<T>,
): T[] {
  const readValue: TimeSeriesValueAccessor<T> = valueAccessor
    ?? ((point) => (point as unknown as NumericPoint).voltage)
  const valid: ValidEntry<T>[] = []
  for (let index = 0; index < points.length; index += 1) {
    const point = points[index]
    const value = readValue(point)
    if (Number.isFinite(point.receivedAtMs) && isFiniteTimeSeriesValue(value)) {
      valid.push({ point, index, value })
    }
  }

  const bucketCount = Math.max(1, Math.floor(pixelWidth))
  if (valid.length <= bucketCount * 2 + 2) return valid.map(({ point }) => point)

  const firstTime = valid[0].point.receivedAtMs
  const lastTime = valid[valid.length - 1].point.receivedAtMs
  const span = Math.max(1, lastTime - firstTime)
  const buckets = new Map<number, { min: typeof valid[number]; max: typeof valid[number] }>()

  for (const entry of valid) {
    const bucket = Math.min(bucketCount - 1, Math.floor((entry.point.receivedAtMs - firstTime) * bucketCount / span))
    const current = buckets.get(bucket)
    if (!current) buckets.set(bucket, { min: entry, max: entry })
    else {
      if (entry.value < current.min.value) current.min = entry
      if (entry.value > current.max.value) current.max = entry
    }
  }

  const selected = new Map<number, T>([
    [valid[0].index, valid[0].point],
    [valid[valid.length - 1].index, valid[valid.length - 1].point],
  ])
  for (const { min, max } of buckets.values()) {
    selected.set(min.index, min.point)
    selected.set(max.index, max.point)
  }
  return [...selected.entries()].sort(([left], [right]) => left - right).map(([, point]) => point)
}

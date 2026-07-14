type NumericPoint = { readonly receivedAtMs: number; readonly voltage: number }

export function minMaxDownsample<T extends NumericPoint>(points: readonly T[], pixelWidth: number): T[] {
  const valid = points
    .map((point, index) => ({ point, index }))
    .filter(({ point }) => Number.isFinite(point.receivedAtMs) && Number.isFinite(point.voltage))
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
      if (entry.point.voltage < current.min.point.voltage) current.min = entry
      if (entry.point.voltage > current.max.point.voltage) current.max = entry
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

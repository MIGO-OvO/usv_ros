export interface VoltageHistoryCsvPoint {
  readonly seq: number
  readonly sourceTimestampMs: number
  readonly receivedAtMs: number
  readonly voltage: number
  readonly absorbance: number | null
  readonly rawCode?: number
  readonly valid: boolean
}

const CSV_HEADER = [
  'received_time_iso',
  'received_time_ms',
  'source_time_iso',
  'source_time_ms',
  'sequence',
  'voltage_v',
  'absorbance',
  'raw_code',
  'valid',
]

function isoTimestamp(timestampMs: number): string {
  if (!Number.isFinite(timestampMs) || timestampMs <= 0) return ''
  return new Date(timestampMs).toISOString()
}

export function buildVoltageHistoryCsv(points: readonly VoltageHistoryCsvPoint[]): string {
  const rows = points.map((point) => [
    isoTimestamp(point.receivedAtMs),
    point.receivedAtMs,
    isoTimestamp(point.sourceTimestampMs),
    point.sourceTimestampMs > 0 ? point.sourceTimestampMs : '',
    point.seq,
    point.voltage,
    point.absorbance ?? '',
    point.rawCode ?? '',
    point.valid,
  ].join(','))

  return `\uFEFF${[CSV_HEADER.join(','), ...rows].join('\r\n')}\r\n`
}

export function voltageHistoryFilename(exportedAt = new Date()): string {
  const localTimestamp = [
    exportedAt.getFullYear(),
    '-',
    String(exportedAt.getMonth() + 1).padStart(2, '0'),
    '-',
    String(exportedAt.getDate()).padStart(2, '0'),
    '_',
    String(exportedAt.getHours()).padStart(2, '0'),
    '-',
    String(exportedAt.getMinutes()).padStart(2, '0'),
    '-',
    String(exportedAt.getSeconds()).padStart(2, '0'),
  ].join('')
  return `spectrometer-voltage-history_${localTimestamp}.csv`
}

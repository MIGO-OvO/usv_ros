export interface SpikeTestCounters {
  readonly crcError?: number | null
  readonly duplicate?: number | null
  readonly transientDrop?: number | null
}

export interface SpikeTestPoint {
  readonly receivedAtMs: number
  readonly voltage: number
  readonly valid: boolean
}

export interface SpikeTestSession {
  readonly startedAtMs: number
  readonly endedAtMs: number | null
  readonly targetDurationS: number
  readonly deadlineMs: number
  readonly baselineCounters: SpikeTestCounters
  readonly finalCounters: SpikeTestCounters | null
}

export interface SpikeTestSummary {
  readonly active: boolean
  readonly startedAtMs: number
  readonly endedAtMs: number | null
  readonly durationS: number
  readonly targetDurationS: number
  readonly remainingS: number
  readonly sampleCount: number
  readonly receiveRateHz: number | null
  readonly dropCount5mv: number
  readonly dropCount10mv: number
  readonly dropCount20mv: number
  readonly maxDownMv: number
  readonly adsCrcErrorDelta: number | null
  readonly adsDuplicateDelta: number | null
  readonly adsTransientDropDelta: number | null
  readonly counterResetDetected: boolean
}

const DROP_THRESHOLDS_V = [0.005, 0.010, 0.020] as const
const VOLTAGE_EPSILON_V = 1e-12
export const SPIKE_TEST_DURATION_OPTIONS_S = [30, 120, 300, 600] as const
export const DEFAULT_SPIKE_TEST_DURATION_S = 600

const SUMMARY_COLUMNS = [
  'session_id',
  'transport_path',
  'started_at_ms',
  'ended_at_ms',
  'duration_s',
  'target_duration_s',
  'sample_count',
  'receive_rate_hz',
  'drop_count_5mv',
  'drop_count_10mv',
  'drop_count_20mv',
  'max_down_mv',
  'ads_crc_error_delta',
  'ads_duplicate_delta',
  'ads_transient_drop_delta',
  'counter_reset_detected',
] as const

export function createSpikeTestSession(
  startedAtMs: number,
  counters: SpikeTestCounters = {},
  targetDurationS = DEFAULT_SPIKE_TEST_DURATION_S,
): SpikeTestSession {
  const normalizedDurationS = Math.trunc(targetDurationS)
  if (!Number.isFinite(normalizedDurationS) || normalizedDurationS <= 0) {
    throw new Error('targetDurationS must be positive')
  }
  return {
    startedAtMs,
    endedAtMs: null,
    targetDurationS: normalizedDurationS,
    deadlineMs: startedAtMs + normalizedDurationS * 1000,
    baselineCounters: normalizeCounters(counters),
    finalCounters: null,
  }
}

export function finishSpikeTestSession(
  session: SpikeTestSession,
  endedAtMs: number,
  counters: SpikeTestCounters,
): SpikeTestSession {
  return {
    ...session,
    endedAtMs: Math.min(
      session.deadlineMs,
      Math.max(session.startedAtMs, endedAtMs),
    ),
    finalCounters: normalizeCounters(counters),
  }
}

export function analyzeSpikeTest(
  points: readonly SpikeTestPoint[],
  session: SpikeTestSession,
  currentCounters: SpikeTestCounters,
  nowMs = Date.now(),
): SpikeTestSummary {
  const effectiveEndMs = session.endedAtMs ?? Math.min(
    session.deadlineMs,
    Math.max(session.startedAtMs, nowMs),
  )
  const samples = points.filter((point) => (
    point.valid
    && Number.isFinite(point.receivedAtMs)
    && Number.isFinite(point.voltage)
    && point.receivedAtMs >= session.startedAtMs
    && point.receivedAtMs <= effectiveEndMs
  ))

  const dropCounts = [0, 0, 0]
  let maxDownV = 0
  for (let index = 1; index < samples.length; index += 1) {
    const downV = samples[index - 1].voltage - samples[index].voltage
    maxDownV = Math.max(maxDownV, downV)
    DROP_THRESHOLDS_V.forEach((thresholdV, thresholdIndex) => {
      if (downV - thresholdV > VOLTAGE_EPSILON_V) {
        dropCounts[thresholdIndex] += 1
      }
    })
  }

  let receiveRateHz: number | null = null
  if (samples.length >= 2) {
    const sampleDurationMs = samples[samples.length - 1].receivedAtMs - samples[0].receivedAtMs
    if (sampleDurationMs > 0) {
      receiveRateHz = (samples.length - 1) * 1000 / sampleDurationMs
    }
  }

  const counters = session.finalCounters ?? normalizeCounters(currentCounters)
  const crc = counterDelta(session.baselineCounters.crcError, counters.crcError)
  const duplicate = counterDelta(session.baselineCounters.duplicate, counters.duplicate)
  const transient = counterDelta(session.baselineCounters.transientDrop, counters.transientDrop)

  return {
    active: session.endedAtMs === null,
    startedAtMs: session.startedAtMs,
    endedAtMs: session.endedAtMs,
    durationS: Math.max(0, effectiveEndMs - session.startedAtMs) / 1000,
    targetDurationS: session.targetDurationS,
    remainingS: session.endedAtMs === null
      ? Math.max(0, session.deadlineMs - effectiveEndMs) / 1000
      : 0,
    sampleCount: samples.length,
    receiveRateHz,
    dropCount5mv: dropCounts[0],
    dropCount10mv: dropCounts[1],
    dropCount20mv: dropCounts[2],
    maxDownMv: maxDownV * 1000,
    adsCrcErrorDelta: crc.value,
    adsDuplicateDelta: duplicate.value,
    adsTransientDropDelta: transient.value,
    counterResetDetected: crc.reset || duplicate.reset || transient.reset,
  }
}

export function buildSpikeTestSummaryCsv(
  summary: SpikeTestSummary,
  sessionId: string,
  transportPath: string,
): string {
  const values: Record<typeof SUMMARY_COLUMNS[number], string | number | boolean | null> = {
    session_id: sessionId,
    transport_path: transportPath,
    started_at_ms: summary.startedAtMs,
    ended_at_ms: summary.endedAtMs,
    duration_s: summary.durationS,
    target_duration_s: summary.targetDurationS,
    sample_count: summary.sampleCount,
    receive_rate_hz: summary.receiveRateHz,
    drop_count_5mv: summary.dropCount5mv,
    drop_count_10mv: summary.dropCount10mv,
    drop_count_20mv: summary.dropCount20mv,
    max_down_mv: summary.maxDownMv,
    ads_crc_error_delta: summary.adsCrcErrorDelta,
    ads_duplicate_delta: summary.adsDuplicateDelta,
    ads_transient_drop_delta: summary.adsTransientDropDelta,
    counter_reset_detected: summary.counterResetDetected,
  }
  const row = SUMMARY_COLUMNS.map((column) => csvCell(values[column])).join(',')
  return `\uFEFF${SUMMARY_COLUMNS.join(',')}\r\n${row}\r\n`
}

export function spikeTestSummaryFilename(exportedAt = new Date()): string {
  const timestamp = [
    exportedAt.getFullYear(),
    String(exportedAt.getMonth() + 1).padStart(2, '0'),
    String(exportedAt.getDate()).padStart(2, '0'),
    '_',
    String(exportedAt.getHours()).padStart(2, '0'),
    String(exportedAt.getMinutes()).padStart(2, '0'),
    String(exportedAt.getSeconds()).padStart(2, '0'),
  ].join('')
  return `spectrometer-spike-test_${timestamp}.csv`
}

function normalizeCounters(counters: SpikeTestCounters): SpikeTestCounters {
  return {
    crcError: finiteCounter(counters.crcError),
    duplicate: finiteCounter(counters.duplicate),
    transientDrop: finiteCounter(counters.transientDrop),
  }
}

function finiteCounter(value: number | null | undefined): number | null {
  return typeof value === 'number' && Number.isFinite(value) ? Math.trunc(value) : null
}

function counterDelta(
  baseline: number | null | undefined,
  current: number | null | undefined,
): { value: number | null; reset: boolean } {
  if (baseline === null || baseline === undefined || current === null || current === undefined) {
    return { value: null, reset: false }
  }
  if (current < baseline) return { value: null, reset: true }
  return { value: current - baseline, reset: false }
}

function csvCell(value: string | number | boolean | null): string {
  if (value === null || value === undefined) return ''
  const text = String(value)
  return /[",\r\n]/.test(text) ? `"${text.replaceAll('"', '""')}"` : text
}

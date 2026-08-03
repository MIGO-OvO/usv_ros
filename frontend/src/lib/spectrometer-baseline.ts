export const BASELINE_STABILIZATION_MS = 5 * 60 * 1000
export const BASELINE_AVERAGING_MS = 60 * 1000

export function formatBaselineDuration(milliseconds: number): string {
  const totalSeconds = Math.max(0, Math.round(milliseconds / 1000))
  if (totalSeconds < 60) return `${totalSeconds} 秒`
  const minutes = totalSeconds / 60
  if (minutes % 1 === 0) return `${minutes} 分钟`
  return `${minutes.toFixed(1)} 分钟`
}

export interface BaselineVoltagePoint {
  readonly receivedAtMs: number
  readonly voltage: number
  readonly valid: boolean
}

export interface BaselineAcquisitionSession {
  readonly startedAtMs: number
  readonly averagingStartedAtMs: number
  readonly endsAtMs: number
}

export interface BaselineAcquisitionSummary {
  readonly phase: 'stabilizing' | 'averaging' | 'complete'
  readonly remainingMs: number
  readonly progressPercent: number
  readonly validSampleCount: number
  readonly averageVoltage: number | null
}

export interface AbsorbanceReference {
  readonly referenceVoltage: number
  readonly baselineVoltage: number
}

export function createBaselineAcquisitionSession(
  startedAtMs: number,
  stabilizationMs = BASELINE_STABILIZATION_MS,
  averagingMs = BASELINE_AVERAGING_MS,
): BaselineAcquisitionSession {
  const averagingStartedAtMs = startedAtMs + stabilizationMs
  return {
    startedAtMs,
    averagingStartedAtMs,
    endsAtMs: averagingStartedAtMs + averagingMs,
  }
}

export function summarizeBaselineAcquisition(
  points: readonly BaselineVoltagePoint[],
  session: BaselineAcquisitionSession,
  nowMs: number,
): BaselineAcquisitionSummary {
  const phase = nowMs < session.averagingStartedAtMs
    ? 'stabilizing'
    : nowMs < session.endsAtMs
      ? 'averaging'
      : 'complete'
  const sampleCutoffMs = Math.min(Math.max(nowMs, session.averagingStartedAtMs), session.endsAtMs)

  let voltageSum = 0
  let validSampleCount = 0
  if (phase !== 'stabilizing') {
    for (const point of points) {
      if (point.receivedAtMs < session.averagingStartedAtMs || point.receivedAtMs > sampleCutoffMs) continue
      if (!point.valid || !Number.isFinite(point.voltage) || point.voltage <= 0) continue
      voltageSum += point.voltage
      validSampleCount += 1
    }
  }

  const totalDurationMs = session.endsAtMs - session.startedAtMs
  const elapsedMs = Math.min(Math.max(nowMs - session.startedAtMs, 0), totalDurationMs)
  const phaseDeadlineMs = phase === 'stabilizing' ? session.averagingStartedAtMs : session.endsAtMs

  return {
    phase,
    remainingMs: Math.max(0, phaseDeadlineMs - nowMs),
    progressPercent: totalDurationMs > 0 ? elapsedMs / totalDurationMs * 100 : 100,
    validSampleCount,
    averageVoltage: validSampleCount > 0 ? voltageSum / validSampleCount : null,
  }
}

export function calculateAbsorbance(
  voltage: number,
  reference: AbsorbanceReference,
): number | null {
  if (
    !Number.isFinite(voltage)
    || !Number.isFinite(reference.referenceVoltage)
    || !Number.isFinite(reference.baselineVoltage)
  ) {
    return null
  }

  const correctedReference = reference.referenceVoltage - reference.baselineVoltage
  if (correctedReference <= 1e-6) return null

  const correctedSample = Math.max(voltage - reference.baselineVoltage, 1e-6)
  return Math.round(Math.log10(correctedReference / correctedSample) * 1e6) / 1e6
}

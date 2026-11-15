export const BASELINE_AVERAGING_MS = 20_000

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
  readonly endsAtMs: number
}

export interface BaselineAcquisitionSummary {
  readonly phase: 'averaging' | 'complete'
  readonly remainingMs: number
  readonly progressPercent: number
  readonly validSampleCount: number
  readonly averageVoltage: number | null
}

export interface AbsorbanceReference {
  readonly referenceVoltage: number
  readonly baselineVoltage: number
}

export interface AbsorbanceEmptyState {
  readonly title: string
  readonly description: string
}

export function getAbsorbanceEmptyState(baselineSet: boolean): AbsorbanceEmptyState {
  return baselineSet
    ? {
        title: '暂无吸光度历史数据',
        description: '新数据到达后将自动恢复绘制',
      }
    : {
        title: '暂无吸光度数据',
        description: '请先完成分光计参考基线获取',
      }
}

export function createBaselineAcquisitionSession(startedAtMs: number): BaselineAcquisitionSession {
  return {
    startedAtMs,
    endsAtMs: startedAtMs + BASELINE_AVERAGING_MS,
  }
}

export function summarizeBaselineAcquisition(
  points: readonly BaselineVoltagePoint[],
  session: BaselineAcquisitionSession,
  nowMs: number,
): BaselineAcquisitionSummary {
  const phase = nowMs < session.endsAtMs ? 'averaging' : 'complete'
  const sampleCutoffMs = Math.min(nowMs, session.endsAtMs)

  let voltageSum = 0
  let validSampleCount = 0
  for (const point of points) {
    if (point.receivedAtMs < session.startedAtMs || point.receivedAtMs > sampleCutoffMs) continue
    if (!point.valid || !Number.isFinite(point.voltage) || point.voltage <= 0) continue
    voltageSum += point.voltage
    validSampleCount += 1
  }

  const totalDurationMs = session.endsAtMs - session.startedAtMs
  const elapsedMs = Math.min(Math.max(nowMs - session.startedAtMs, 0), totalDurationMs)

  return {
    phase,
    remainingMs: Math.max(0, session.endsAtMs - nowMs),
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

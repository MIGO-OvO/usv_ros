export const PUMP_AXES = ['X', 'Y', 'Z', 'A'] as const
export type PumpAxis = typeof PUMP_AXES[number]
export interface PreflightConfig {
  oil_axis: PumpAxis | null
  separation_turns: number
  separation_rpm: number
}
export interface PreflightStatus {
  active: boolean
  phase: string
  motors?: PumpAxis[]
  pending_motors?: PumpAxis[]
  error?: string
  travel_degrees?: number
  target_degrees?: number
}
export const DEFAULT_PREFLIGHT: PreflightConfig = {
  oil_axis: null, separation_turns: 0, separation_rpm: 5,
}
export const PREFLIGHT_PHASES = [
  ['checking', '校验配置'],
  ['homing', '回到相对零点'],
  ['compensating', '正转 360° 补偿'],
  ['separating', '油相分隔'],
  ['injection', '进样泵预启动'],
  ['complete', '进入采样序列'],
] as const
export function getInvolvedAxes(steps: Partial<Record<PumpAxis, { enable: string }>>[]): PumpAxis[] {
  return PUMP_AXES.filter((axis) => steps.some((step) => step[axis]?.enable === 'E'))
}
export function getPreflightLabel(status: PreflightStatus | null): string {
  if (!status) return '尚未开始'
  if (status.phase === 'failed') return '准备失败'
  if (status.phase === 'cancelled') return '准备已停止'
  return PREFLIGHT_PHASES.find(([phase]) => phase === status.phase)?.[1] || '尚未开始'
}

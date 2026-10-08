/* eslint-disable @typescript-eslint/no-explicit-any */
import { useEffect, useRef, useState } from 'react'
import { persistGpsPolicy } from '@/lib/gps-policy'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Switch } from '@/components/ui/switch'
import { NumericInput } from '@/components/ui/numeric-input'
import { Play, Square, Pause, Save, FolderOpen, Plus, Trash2, ArrowUp, ArrowDown, Download, Upload, Loader2 } from 'lucide-react'
import { useAppStore } from '@/store'
import { InjectionPumpCard } from '@/components/injection-pump-card'
import { SpectrometerDiagnosticsCard } from '@/components/spectrometer-diagnostics-card'
import { AutomationPreflightCard } from '@/components/automation-preflight-card'
import { DEFAULT_PREFLIGHT, PUMP_AXES, PREFLIGHT_PHASES, getInvolvedAxes, getPreflightLabel,
  type PumpAxis, type PreflightConfig } from '@/lib/automation-preflight'
import { WaypointSamplingCard } from '@/components/waypoint-sampling-card'
import { toast } from '@/hooks/use-toast'
import {
  getAutomationControlAvailability,
  resolveAutomationAction,
  type AutomationAction,
} from '@/lib/automation-controls'

interface PumpConfig {
  enable: string
  direction: string
  speed: string
  angle: string
}

interface Step {
  name: string
  interval: number
  X: PumpConfig
  Y: PumpConfig
  Z: PumpConfig
  A: PumpConfig
}

interface InjectionPumpPolicy {
  mode: 'manual' | 'automation' | 'survey'
  speed: number
  lead_time_s: number
  stop_on_finish: boolean
}

interface PumpSettings {
  pid_mode?: boolean
  pid_precision?: number
  default_speed: number
  injection_pump_policy: InjectionPumpPolicy
  preflight: PreflightConfig
}

const DEFAULT_PUMP: PumpConfig = { enable: 'D', direction: 'F', speed: '5', angle: '0' }
const DEFAULT_STEP: Step = {
  name: '新步骤',
  interval: 1000,
  X: { ...DEFAULT_PUMP },
  Y: { ...DEFAULT_PUMP },
  Z: { ...DEFAULT_PUMP },
  A: { ...DEFAULT_PUMP },
}
const DEFAULT_PUMP_SETTINGS: PumpSettings = {
  default_speed: 60,
  preflight: { ...DEFAULT_PREFLIGHT },
  injection_pump_policy: {
    mode: 'manual',
    speed: 60,
    lead_time_s: 0,
    stop_on_finish: true,
  },
}

const normalizeStep = (step?: Partial<Step>): Step => ({
  name: step?.name || '新步骤',
  interval: Number(step?.interval ?? 1000) || 1000,
  X: { ...DEFAULT_PUMP, ...(step?.X || {}) },
  Y: { ...DEFAULT_PUMP, ...(step?.Y || {}) },
  Z: { ...DEFAULT_PUMP, ...(step?.Z || {}) },
  A: { ...DEFAULT_PUMP, ...(step?.A || {}) },
})

const normalizeSteps = (rawSteps?: Partial<Step>[]): Step[] =>
  Array.isArray(rawSteps) ? rawSteps.map((step) => normalizeStep(step)) : []

export default function Automation() {
  const {
    automationRunning,
    automationPreflight,
    automationPaused,
    automationStep,
    automationTotal,
    currentLoop,
    totalLoops,
    automationTerminalReason,
    automationLastError,
    automationControllerFault,
    automationSpectroState,
    automationSpectroAgeS,
    automationOwnerAgeS,
  } = useAppStore()
  const preparing = Boolean(automationPreflight?.active)
  const automationState = { running: automationRunning, paused: automationPaused, preflight: preparing }
  const controls = getAutomationControlAvailability(automationState)
  const [steps, setSteps] = useState<Step[]>([])
  const [loopCount, setLoopCount] = useState(1)
  // 服务器持久化 policy：初始 true 仅是 HTML 首帧兜底，fetchConfig 会立即
  // 用 GET /api/config 的 automation_policy.require_gps 覆盖。
  const [requireGps, setRequireGps] = useState(true)
  const [savingGps, setSavingGps] = useState(false)
  const gpsSavePending = useRef(false)
  const [pumpSettings, setPumpSettings] = useState<PumpSettings>({ ...DEFAULT_PUMP_SETTINGS })
  const [presetName, setPresetName] = useState('')
  const [relativeZeros, setRelativeZeros] = useState<Partial<Record<PumpAxis, string>>>({})
  const [dirtyZeros, setDirtyZeros] = useState<Partial<Record<PumpAxis, string>>>({})
  const [pendingAction, setPendingAction] = useState(false)
  const involvedMotors = getInvolvedAxes(steps)

  useEffect(() => {
    void fetchConfig()
  }, [])

  const fetchConfig = async () => {
    try {
      const [res, zerosResponse] = await Promise.all([fetch('/api/config'), fetch('/api/calibration/offsets')])
      const data = await res.json()
      const zeroData = await zerosResponse.json()
      if (zerosResponse.ok && zeroData.success) {
        const configured = zeroData.configured_axes ?? Object.keys(zeroData.data ?? {})
        setRelativeZeros(Object.fromEntries(PUMP_AXES.filter((axis) => configured.includes(axis))
          .map((axis) => [axis, String(zeroData.data[axis])])))
        setDirtyZeros({})
      }
      if (data.sampling_sequence) {
        setSteps(normalizeSteps(data.sampling_sequence.steps))
        setLoopCount(data.sampling_sequence.loop_count ?? 1)
      }
      if (data.automation_policy) {
        setRequireGps(data.automation_policy.require_gps ?? true)
      }
      if (data.pump_settings) {
        const policy = data.pump_settings.injection_pump_policy || {}
        const defaultSpeed = Number(data.pump_settings.default_speed ?? DEFAULT_PUMP_SETTINGS.default_speed) || 0
        setPumpSettings({
          ...DEFAULT_PUMP_SETTINGS,
          ...data.pump_settings,
          default_speed: defaultSpeed,
          preflight: { ...DEFAULT_PREFLIGHT, ...data.pump_settings.preflight },
          injection_pump_policy: {
            ...DEFAULT_PUMP_SETTINGS.injection_pump_policy,
            ...policy,
            speed: Number(policy.speed ?? defaultSpeed) || 0,
            lead_time_s: Number(policy.lead_time_s ?? 0) || 0,
            stop_on_finish: policy.stop_on_finish ?? true,
          },
        })
      }
    } catch (error) {
      console.error(error)
    }
  }

  const persistRequireGps = async (next: boolean) => {
    if (gpsSavePending.current) return
    gpsSavePending.current = true
    setSavingGps(true)
    try {
      await persistGpsPolicy(next, requireGps, setRequireGps)
    } catch (error) {
      console.error(error)
      toast({ title: 'GPS 策略保存失败', description: '已恢复保存前的设置，请重试', variant: 'destructive' })
    } finally {
      gpsSavePending.current = false
      setSavingGps(false)
    }
  }

  const saveRelativeZeros = async () => {
    if (!Object.keys(dirtyZeros).length) return
    const offsets: Partial<Record<PumpAxis, number>> = {}
    for (const axis of PUMP_AXES) {
      const raw = dirtyZeros[axis]
      if (raw === undefined) continue
      const value = Number(raw)
      if (raw.trim() === '' || !Number.isFinite(value) || value < 0 || value >= 360) {
        throw new Error(`${axis} 轴相对零点应为 0–360°（不含 360°）`)
      }
      offsets[axis] = value
    }
    const response = await fetch('/api/calibration/offsets', {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ offsets }),
    })
    const result = await response.json()
    if (!response.ok || !result.success) throw new Error(result.message || '相对零点保存失败')
    setDirtyZeros({})
  }

  const validatePreparation = () => {
    if (pumpSettings.preflight.separation_turns > 0 && !pumpSettings.preflight.oil_axis) {
      throw new Error('请先选择油相泵轴')
    }
  }

  const saveConfig = async () => {
    try {
      validatePreparation()
      await saveRelativeZeros()
      const response = await fetch('/api/config', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          sampling_sequence: {
            steps,
            loop_count: loopCount,
          },
          pump_settings: pumpSettings,
        }),
      })
      const result = await response.json()
      if (!response.ok || !result.success) throw new Error(result.message || '配置保存失败')
      toast({ title: '启动配置已保存', variant: 'success' })
    } catch (error) {
      toast({ title: '配置保存失败', description: String(error), variant: 'destructive' })
    }
  }

  const handleAction = async (requestedAction: AutomationAction) => {
    const action = resolveAutomationAction(requestedAction, automationState)
    const options: RequestInit = { method: 'POST' }

    if (action === 'start') {
      try {
        validatePreparation()
        await saveRelativeZeros()
      } catch (error) {
        toast({ title: '启动配置未就绪', description: String(error), variant: 'destructive' })
        return
      }
      let wpSampling: Record<string, unknown> | null = null
      try {
        const wpRes = await fetch('/api/waypoint-sampling')
        const wpJson = await wpRes.json()
        if (wpJson.success && wpJson.data) wpSampling = wpJson.data
      } catch {
        wpSampling = null
      }

      const body: Record<string, unknown> = {
        require_gps: requireGps,
        sampling_sequence: { steps, loop_count: loopCount },
        pump_settings: pumpSettings,
      }
      if (wpSampling !== null) body.waypoint_sampling = wpSampling

      options.headers = { 'Content-Type': 'application/json' }
      options.body = JSON.stringify(body)
    }

    try {
      setPendingAction(true)
      const response = await fetch(`/api/mission/${action}`, options)
      const result = await response.json()
      if (!response.ok || !result.success) {
        toast({ title: '操作失败', description: result.message || `任务${action}失败`, variant: 'destructive' })
        return
      }
      const successTitles: Record<AutomationAction, string> = {
        start: '已开始启动前准备',
        pause: '任务已暂停',
        resume: '任务已恢复',
        stop: '任务已停止',
      }
      toast({ title: successTitles[action], description: result.message || undefined, variant: 'success' })
    } catch (error) {
      console.error(error)
      toast({ title: '请求失败', description: `任务${action}请求异常`, variant: 'destructive' })
    } finally {
      setPendingAction(false)
    }
  }

  const savePreset = async () => {
    if (!presetName) return
    await fetch(`/api/preset/auto/${presetName}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ steps, loop_count: loopCount }),
    })
    toast({ title: '预设已保存', variant: 'success' })
  }

  const loadPreset = async () => {
    if (!presetName) return
    try {
      const res = await fetch(`/api/preset/auto/${presetName}`)
      if (res.ok) {
        const data = await res.json()
        if (data.success) {
          setSteps(normalizeSteps(data.data.steps))
          setLoopCount(data.data.loop_count)
        }
      } else {
        toast({ title: '预设不存在', variant: 'destructive' })
      }
    } catch (error) {
      console.error(error)
    }
  }

  const handleExport = () => {
    window.open('/api/mission-config/export', '_blank')
  }

  const handleImport = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0]
    if (!file) return
    try {
      const text = await file.text()
      const data = JSON.parse(text)
      const res = await fetch('/api/mission-config/import', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(data),
      })
      const result = await res.json()
      if (result.success) {
        toast({ title: '任务配置已导入', description: `已导入 ${(result.fields || []).join(', ')}` })
        void fetchConfig()
      } else {
        toast({ title: '导入失败', description: result.message, variant: 'destructive' })
      }
    } catch {
      toast({ title: '导入失败', description: '文件解析错误', variant: 'destructive' })
    }
    e.target.value = ''
  }

  const moveStep = (index: number, direction: -1 | 1) => {
    if (index + direction < 0 || index + direction >= steps.length) return
    const newSteps = [...steps]
    const temp = newSteps[index]
    newSteps[index] = newSteps[index + direction]
    newSteps[index + direction] = temp
    setSteps(newSteps)
  }

  const deleteStep = (index: number) => {
    const newSteps = [...steps]
    newSteps.splice(index, 1)
    setSteps(newSteps)
  }

  const addStep = () => {
    setSteps([...steps, JSON.parse(JSON.stringify(DEFAULT_STEP))])
  }

  const updateInjectionPolicy = (patch: Partial<InjectionPumpPolicy>) => {
    setPumpSettings((current) => ({
      ...current,
      injection_pump_policy: {
        ...current.injection_pump_policy,
        ...patch,
      },
    }))
  }

  const handleInjectionPolicyModeChange = (value: string) => {
    if (value === 'manual' || value === 'automation' || value === 'survey') {
      updateInjectionPolicy({ mode: value })
    }
  }

  const updateStep = (index: number, field: string, value: any) => {
    const newSteps = [...steps]
    if (field.includes('.')) {
      const [parent, child] = field.split('.')
      ;(newSteps[index] as any)[parent][child] = value
    } else {
      ;(newSteps[index] as any)[field] = value
    }
    setSteps(newSteps)
  }

  const TERMINAL_REASON_TEXT: Record<string, string> = {
    completed: '任务全部完成',
    operator_stop: '操作员手动停止',
    operator_pause: '操作员暂停',
    pid_timeout: 'PID 等待超时',
    pid_fail: 'PID 执行失败',
    spectrometer_start_failed: '分光仪启动失败',
    spectrometer_frame_timeout: '分光启动后未收到有效数据帧',
    spectrometer_stale: '分光数据超时',
    watchdog_tripped: 'ESP32 硬件看门狗触发',
    owner_lost: '控制所有权心跳丢失',
    serial_disconnected: '检测装置串口断开',
    controller_fault: '控制器故障',
    configuration_failed: '任务配置无效',
    cleanup_failed: '停止清理失败（泵可能仍在运行，请检查）',
    unknown_error: '未知错误',
  }
  const idle = !automationRunning && !automationPaused && !preparing
  const showTerminal = idle && Boolean(automationTerminalReason)
  const terminalTitle =
    automationTerminalReason === 'completed' ? '任务已完成' : '任务已停止'
  const terminalVariantOk = automationTerminalReason === 'completed'

  return (
    <div className="p-4 md:p-8 space-y-6 max-w-7xl mx-auto pb-32">
      <header className="flex flex-col md:flex-row md:items-center justify-between gap-4">
        <div>
          <h1 className="text-3xl font-bold tracking-tight">自动化控制</h1>
          <p className="text-muted-foreground">配置并执行采样序列任务。</p>
        </div>
        <div className="flex w-full flex-wrap items-center gap-2 md:w-auto">
          {(automationRunning || automationPaused || preparing) && automationTotal > 0 && (
            <div className="flex items-center gap-2 rounded-lg border border-emerald-500/30 bg-emerald-500/10 px-3 py-2">
              <Loader2 className="w-4 h-4 animate-spin text-emerald-500" />
              <span className="text-sm font-semibold tabular-nums whitespace-nowrap">
                {preparing ? getPreflightLabel(automationPreflight) : `${automationPaused ? '已暂停' : '运行中'}：步骤 ${automationStep} / ${automationTotal}`}
              </span>
              {!preparing && (totalLoops === 0 || totalLoops > 1) && (
                <span className="text-xs text-muted-foreground tabular-nums whitespace-nowrap">
                  第 {currentLoop} / {totalLoops === 0 ? '∞' : totalLoops} 圈
                </span>
              )}
            </div>
          )}
          <Button variant="outline" onClick={() => handleAction('start')} disabled={!controls.start || savingGps || pendingAction}>
            <Play className="w-4 h-4 mr-2 text-emerald-500" /> 启动
          </Button>
          <Button variant="outline" onClick={() => handleAction('pause')} disabled={!controls.pause}>
            <Pause className="w-4 h-4 mr-2 text-amber-500" /> 暂停
          </Button>
          <Button variant="outline" onClick={() => handleAction('resume')} disabled={!controls.resume}>
            <Play className="w-4 h-4 mr-2 text-blue-500" /> 恢复
          </Button>
          <Button variant="destructive" onClick={() => handleAction('stop')} disabled={!controls.stop && !pendingAction}>
            <Square className="w-4 h-4 mr-2" /> 停止
          </Button>
        </div>
      </header>

      {automationPreflight && automationPreflight.phase !== 'idle' && (
        <section aria-label="启动前准备进度" aria-live="polite" className="space-y-3 rounded-lg border p-4">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <h2 className="text-sm font-semibold">Preflight · {getPreflightLabel(automationPreflight)}</h2>
            <p className="text-xs text-muted-foreground">
              相关泵：{automationPreflight.motors?.join(' / ') || '无'}
              {automationPreflight.pending_motors?.length ? ` · 等待 ${automationPreflight.pending_motors.join(' / ')}` : ''}
            </p>
          </div>
          <ol className="grid grid-cols-1 gap-2 sm:grid-cols-3 xl:grid-cols-6">
            {PREFLIGHT_PHASES.map(([phase, label], index) => (
              <li key={phase} aria-current={automationPreflight.phase === phase ? 'step' : undefined}
                className={automationPreflight.phase === phase ? 'text-sm font-semibold text-foreground' : 'text-sm text-muted-foreground'}>
                <span className="mr-2 tabular-nums">{index + 1}.</span>{label}
              </li>
            ))}
          </ol>
          {automationPreflight.error && <p className="text-sm text-destructive break-words">{automationPreflight.error}</p>}
        </section>
      )}

      <AutomationPreflightCard motors={involvedMotors} zeros={relativeZeros} config={pumpSettings.preflight}
        disabled={!idle || pendingAction}
        onZero={(axis, value) => {
          setRelativeZeros((current) => ({ ...current, [axis]: value }))
          setDirtyZeros((current) => ({ ...current, [axis]: value }))
        }}
        onConfig={(patch) => setPumpSettings((current) => ({ ...current, preflight: { ...current.preflight, ...patch } }))} />

      <SpectrometerDiagnosticsCard />

      {showTerminal && (
        <Card className={terminalVariantOk ? 'border-emerald-500/40' : 'border-amber-500/50'}>
          <CardContent className="py-4 space-y-2">
            <div className="flex items-center gap-2">
              <span
                className={
                  terminalVariantOk
                    ? 'text-sm font-semibold text-emerald-500'
                    : 'text-sm font-semibold text-amber-500'
                }
              >
                {terminalTitle}
              </span>
              <span className="text-sm">
                原因：{TERMINAL_REASON_TEXT[automationTerminalReason || ''] || automationTerminalReason}
              </span>
            </div>
            {automationLastError && (
              <p className="text-xs text-muted-foreground break-all">错误详情：{automationLastError}</p>
            )}
            <div className="flex flex-wrap gap-x-4 gap-y-1 text-xs text-muted-foreground">
              {(automationStep > 0 || automationTotal > 0) && (
                <span className="tabular-nums">步骤：{automationStep} / {automationTotal}</span>
              )}
              {totalLoops > 0 && (
                <span className="tabular-nums">循环：{currentLoop} / {totalLoops === 0 ? '∞' : totalLoops}</span>
              )}
              {automationSpectroState && <span>分光状态：{automationSpectroState}</span>}
              {typeof automationSpectroAgeS === 'number' && (
                <span className="tabular-nums">最后有效分光数据：{automationSpectroAgeS.toFixed(1)} 秒前</span>
              )}
              {typeof automationOwnerAgeS === 'number' && (
                <span className="tabular-nums">最后心跳：{automationOwnerAgeS.toFixed(1)} 秒前</span>
              )}
              {automationControllerFault && <span>控制器故障：{automationControllerFault}</span>}
            </div>
          </CardContent>
        </Card>
      )}

      <div className="grid grid-cols-1 gap-6 xl:grid-cols-[minmax(18rem,22rem)_minmax(0,1fr)] xl:items-start">
        <div className="space-y-6">
          <Card className="h-fit">
            <CardHeader>
              <CardTitle>全局配置</CardTitle>
            </CardHeader>
            <CardContent className="space-y-4">
              <div className="space-y-2">
                <div className="flex items-center justify-between gap-3">
                  <Label htmlFor="require-gps">启动时要求 GPS</Label>
                  <Switch id="require-gps" checked={requireGps} onCheckedChange={(v) => void persistRequireGps(v)}
                    disabled={automationRunning || automationPaused || savingGps} aria-describedby="require-gps-help" />
                </div>
                <p id="require-gps-help" className="text-xs text-muted-foreground">
                  {requireGps ? '启动前校验有效定位；室内台架测试可关闭。' : '室内测试：允许无 GPS 执行真实泵控；无定位记录不进入地图。'}
                  该开关为服务器持久化配置，切换页面、刷新或重启后仍保持；不改变航点采样限制。
                </p>
              </div>
              <div className="space-y-2">
                <Label>循环次数 (0 = 无限循环)</Label>
                <NumericInput value={loopCount} onValueChange={(v) => setLoopCount(Math.max(0, v))} integer min={0} className="h-9" />
              </div>
              <div className="space-y-2">
                <Label>进样泵联动</Label>
                <select
                  value={pumpSettings.injection_pump_policy.mode}
                  onChange={(e) => handleInjectionPolicyModeChange(e.target.value)}
                  className="flex h-9 w-full rounded-md border border-input bg-background px-3 text-sm"
                >
                  <option value="manual">手动控制</option>
                  <option value="automation">随采样任务</option>
                  <option value="survey">随走航任务</option>
                </select>
              </div>
              <div className="grid grid-cols-2 gap-3">
                <div className="space-y-2">
                  <Label>联动转速 (%)</Label>
                  <NumericInput
                    value={pumpSettings.injection_pump_policy.speed}
                    onValueChange={(v) => updateInjectionPolicy({ speed: Math.max(0, Math.min(100, v)) })}
                    integer
                    min={0}
                    max={100}
                    className="h-9"
                  />
                </div>
                <div className="space-y-2">
                  <Label>提前启动 (s)</Label>
                  <NumericInput
                    value={pumpSettings.injection_pump_policy.lead_time_s}
                    onValueChange={(v) => updateInjectionPolicy({ lead_time_s: Math.max(0, v) })}
                    min={0}
                    className="h-9"
                  />
                </div>
              </div>
              <label className="flex items-center justify-between gap-3 text-sm">
                <span className="font-medium">任务结束关闭进样泵</span>
                <Switch
                  checked={pumpSettings.injection_pump_policy.stop_on_finish}
                  onCheckedChange={(v) => updateInjectionPolicy({ stop_on_finish: v })}
                />
              </label>
              <div className="space-y-2">
                <Label>默认转速 (%)</Label>
                <NumericInput
                  value={pumpSettings.default_speed}
                  onValueChange={(v) => {
                    const speed = Math.max(0, Math.min(100, v))
                    setPumpSettings((current) => ({
                      ...current,
                      default_speed: speed,
                      injection_pump_policy: {
                        ...current.injection_pump_policy,
                        speed,
                      },
                    }))
                  }}
                  integer
                  min={0}
                  max={100}
                  className="h-9"
                />
              </div>
              <div className="space-y-2">
                <Label>预设名称</Label>
                <div className="flex min-w-0 gap-2">
                  <Input value={presetName} onChange={(e) => setPresetName(e.target.value)} placeholder="default" className="min-w-0 flex-1" />
                  <Button size="icon" variant="ghost" onClick={loadPreset} title="加载" aria-label="加载预设">
                    <FolderOpen className="w-4 h-4" />
                  </Button>
                  <Button size="icon" variant="ghost" onClick={savePreset} title="保存" aria-label="保存预设">
                    <Save className="w-4 h-4" />
                  </Button>
                </div>
              </div>
              <Button className="w-full" onClick={saveConfig} disabled={!idle || pendingAction}>保存启动配置</Button>
              <div className="flex gap-2 pt-2">
                <Button variant="outline" size="sm" className="flex-1" onClick={handleExport}>
                  <Download className="w-4 h-4 mr-1" />导出
                </Button>
                <Button variant="outline" size="sm" className="flex-1 relative" asChild>
                  <label>
                    <Upload className="w-4 h-4 mr-1" />导入
                    <input type="file" accept=".json" className="absolute inset-0 opacity-0 cursor-pointer" onChange={handleImport} />
                  </label>
                </Button>
              </div>
            </CardContent>
          </Card>
          <InjectionPumpCard />
        </div>

        <div className="min-w-0">
          <WaypointSamplingCard />
        </div>

        <Card className="min-w-0 xl:col-span-2">
          <CardHeader className="flex flex-row items-center justify-between">
            <CardTitle>序列步骤</CardTitle>
            <Button size="sm" onClick={addStep}><Plus className="w-4 h-4 mr-2" /> 添加步骤</Button>
          </CardHeader>
          <CardContent className="space-y-4">
            {steps.map((step, index) => (
              <div key={index} className="p-4 border rounded-lg bg-card/50 space-y-4">
                <div className="flex flex-wrap items-center gap-3">
                  <div className="font-mono text-muted-foreground w-6">{index + 1}</div>
                  <Input value={step.name} onChange={(e) => updateStep(index, 'name', e.target.value)} className="min-w-0 flex-[1_1_12rem]" />
                  <div className="ml-auto flex items-center gap-2">
                    <Label className="whitespace-nowrap text-xs text-muted-foreground">完成后等待</Label>
                    <NumericInput value={step.interval} onValueChange={(v) => updateStep(index, 'interval', v)} integer className="w-24 h-7 text-xs px-2" />
                    <span className="text-xs text-muted-foreground">ms</span>
                  </div>
                  <div className="flex gap-1">
                    <Button size="icon" variant="ghost" onClick={() => moveStep(index, -1)} aria-label={`上移步骤 ${index + 1}`}><ArrowUp className="w-4 h-4" /></Button>
                    <Button size="icon" variant="ghost" onClick={() => moveStep(index, 1)} aria-label={`下移步骤 ${index + 1}`}><ArrowDown className="w-4 h-4" /></Button>
                    <Button size="icon" variant="ghost" className="text-destructive" onClick={() => deleteStep(index)} aria-label={`删除步骤 ${index + 1}`}><Trash2 className="w-4 h-4" /></Button>
                  </div>
                </div>

                <div className="grid grid-cols-[repeat(auto-fit,minmax(min(100%,12rem),1fr))] gap-3 pt-2">
                  {(['X', 'Y', 'Z', 'A'] as const).map((axis) => (
                    <div key={axis} className="space-y-2 p-3 rounded-lg border border-border/60 bg-muted/20">
                      <div className="flex items-center justify-between">
                        <span className="font-bold text-xs">{axis} 轴</span>
                        <Switch
                          checked={(step as any)[axis].enable === 'E'}
                          onCheckedChange={(checked) => updateStep(index, `${axis}.enable`, checked ? 'E' : 'D')}
                          aria-label={`步骤 ${index + 1}：启用 ${axis} 轴`}
                        />
                      </div>
                      {(step as any)[axis].enable === 'E' && (
                        <>
                          <div className="space-y-1">
                            <Label className="text-xs text-muted-foreground">角度</Label>
                            <NumericInput className="h-7 text-xs px-2" value={(step as any)[axis].angle} onValueChange={(v) => updateStep(index, `${axis}.angle`, String(v))} />
                          </div>
                          <div className="space-y-1">
                            <Label className="text-xs text-muted-foreground">速度</Label>
                            <NumericInput className="h-7 text-xs px-2" value={(step as any)[axis].speed} onValueChange={(v) => updateStep(index, `${axis}.speed`, String(v))} integer />
                          </div>
                        </>
                      )}
                    </div>
                  ))}
                </div>
              </div>
            ))}
          </CardContent>
        </Card>
      </div>
    </div>
  )
}

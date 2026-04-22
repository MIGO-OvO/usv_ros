import { useEffect, useState } from 'react'
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Switch } from "@/components/ui/switch"
import { NumericInput } from "@/components/ui/numeric-input"
import { Play, Square, Pause, Save, FolderOpen, Plus, Trash2, ArrowUp, ArrowDown, Download, Upload } from 'lucide-react'
import { useAppStore } from '@/store'
import { InjectionPumpCard } from '@/components/injection-pump-card'
import { WaypointSamplingCard } from '@/components/waypoint-sampling-card'
import { toast } from '@/hooks/use-toast'

interface PumpConfig {
  enable: string
  direction: string
  speed: string
  angle: string
}

interface InjectionPumpConfig {
  enable: boolean
  speed: number
  duration_ms: number
}

interface Step {
  name: string
  interval: number
  X: PumpConfig
  Y: PumpConfig
  Z: PumpConfig
  A: PumpConfig
  pump?: InjectionPumpConfig
}

const DEFAULT_PUMP: PumpConfig = { enable: 'D', direction: 'F', speed: '5', angle: '0' }
const DEFAULT_INJECTION_PUMP: InjectionPumpConfig = { enable: false, speed: 0, duration_ms: 0 }
const DEFAULT_STEP: Step = {
  name: '新步骤',
  interval: 1000,
  X: { ...DEFAULT_PUMP },
  Y: { ...DEFAULT_PUMP },
  Z: { ...DEFAULT_PUMP },
  A: { ...DEFAULT_PUMP },
  pump: { ...DEFAULT_INJECTION_PUMP },
}

const normalizeStep = (step?: Partial<Step>): Step => ({
  name: step?.name || '新步骤',
  interval: Number(step?.interval ?? 1000) || 1000,
  X: { ...DEFAULT_PUMP, ...(step?.X || {}) },
  Y: { ...DEFAULT_PUMP, ...(step?.Y || {}) },
  Z: { ...DEFAULT_PUMP, ...(step?.Z || {}) },
  A: { ...DEFAULT_PUMP, ...(step?.A || {}) },
  pump: {
    enable: !!step?.pump?.enable,
    speed: Math.max(0, Math.min(100, Number(step?.pump?.speed ?? 0) || 0)),
    duration_ms: Math.max(0, Number(step?.pump?.duration_ms ?? 0) || 0),
  },
})

const normalizeSteps = (rawSteps?: Partial<Step>[]): Step[] =>
  Array.isArray(rawSteps) ? rawSteps.map((step) => normalizeStep(step)) : []

export default function Automation() {
  const { automationRunning } = useAppStore()
  const [steps, setSteps] = useState<Step[]>([])
  const [loopCount, setLoopCount] = useState(1)
  const [presetName, setPresetName] = useState("")

  useEffect(() => {
    fetchConfig()
  }, [])

  const fetchConfig = async () => {
    try {
        const res = await fetch('/api/config')
        const data = await res.json()
        if (data.sampling_sequence) {
            setSteps(normalizeSteps(data.sampling_sequence.steps))
            setLoopCount(data.sampling_sequence.loop_count || 1)
        }
    } catch (e) {
        console.error(e)
    }
  }

  const saveConfig = async () => {
    try {
        await fetch('/api/config', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({
                sampling_sequence: {
                    steps,
                    loop_count: loopCount
                }
            })
        })
    } catch (e) {
        console.error(e)
    }
  }

  const handleAction = async (action: string) => {
    const options: RequestInit = { method: 'POST' }

    if (action === 'start') {
      // 启动前先获取最新 waypoint_sampling 并一并下发
      let wpSampling: Record<string, unknown> | null = null
      try {
        const wpRes = await fetch('/api/waypoint-sampling')
        const wpJson = await wpRes.json()
        if (wpJson.success && wpJson.data) wpSampling = wpJson.data
      } catch (_) { /* 获取失败时不传该字段，后端会用 config 中已有值 */ }

      const body: Record<string, unknown> = {
        sampling_sequence: { steps, loop_count: loopCount },
      }
      if (wpSampling !== null) body.waypoint_sampling = wpSampling

      options.headers = { 'Content-Type': 'application/json' }
      options.body = JSON.stringify(body)
    }

    try {
      const response = await fetch(`/api/mission/${action}`, options)
      const result = await response.json()
      if (!response.ok || !result.success) {
        toast({ title: '操作失败', description: result.message || `任务${action}失败`, variant: 'destructive' })
      }
    } catch (error) {
      console.error(error)
      toast({ title: '请求失败', description: `任务${action}请求异常`, variant: 'destructive' })
    }
  }

  // Preset Logic
  const savePreset = async () => {
    if (!presetName) return
    await fetch(`/api/preset/auto/${presetName}`, {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({ steps, loop_count: loopCount })
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
    } catch (e) {
        console.error(e)
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
        fetchConfig()
      } else {
        toast({ title: '导入失败', description: result.message, variant: 'destructive' })
      }
    } catch (err) {
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

  const updateStep = (index: number, field: string, value: any) => {
      const newSteps = [...steps]
      if (field.includes('.')) {
          const [parent, child] = field.split('.')
          if (parent === 'pump') {
              const pump = newSteps[index].pump || { ...DEFAULT_INJECTION_PUMP }
              ;(pump as any)[child] = value
              newSteps[index].pump = pump
          } else {
              ;(newSteps[index] as any)[parent][child] = value
          }
      } else {
          ;(newSteps[index] as any)[field] = value
      }
      setSteps(newSteps)
  }

  return (
    <div className="p-4 md:p-8 space-y-6 max-w-7xl mx-auto pb-32">
       <header className="flex flex-col md:flex-row md:items-center justify-between gap-4">
        <div>
            <h1 className="text-3xl font-bold tracking-tight">自动化控制</h1>
            <p className="text-muted-foreground">配置并执行采样序列任务。</p>
        </div>
        <div className="flex items-center gap-2">
            <Button variant="outline" onClick={() => handleAction('start')} disabled={automationRunning}>
                <Play className="w-4 h-4 mr-2 text-emerald-500" /> 启动
            </Button>
            <Button variant="outline" onClick={() => handleAction('pause')} disabled={!automationRunning}>
                <Pause className="w-4 h-4 mr-2 text-amber-500" /> 暂停
            </Button>
            <Button variant="outline" onClick={() => handleAction('resume')} disabled={!automationRunning}>
                <Play className="w-4 h-4 mr-2 text-blue-500" /> 恢复
            </Button>
            <Button variant="destructive" onClick={() => handleAction('stop')} disabled={!automationRunning}>
                <Square className="w-4 h-4 mr-2" /> 停止
            </Button>
        </div>
      </header>

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        <div className="lg:col-span-1 space-y-6">
          <Card className="h-fit">
            <CardHeader>
                <CardTitle>全局配置</CardTitle>
            </CardHeader>
            <CardContent className="space-y-4">
                <div className="space-y-2">
                    <Label>循环次数 (0 = 无限循环)</Label>
                    <NumericInput value={loopCount} onValueChange={setLoopCount} integer className="h-9" />
                </div>
                <div className="space-y-2">
                    <Label>预设名称</Label>
                    <div className="flex gap-2">
                        <Input value={presetName} onChange={(e) => setPresetName(e.target.value)} placeholder="default" />
                        <Button size="icon" variant="ghost" onClick={loadPreset} title="加载">
                            <FolderOpen className="w-4 h-4" />
                        </Button>
                        <Button size="icon" variant="ghost" onClick={savePreset} title="保存">
                            <Save className="w-4 h-4" />
                        </Button>
                    </div>
                </div>
                <Button className="w-full" onClick={saveConfig}>应用配置</Button>
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

        <div className="lg:col-span-2">
          <WaypointSamplingCard />
        </div>

        <Card className="lg:col-span-3">
            <CardHeader className="flex flex-row items-center justify-between">
                <CardTitle>序列步骤</CardTitle>
                <Button size="sm" onClick={addStep}><Plus className="w-4 h-4 mr-2" /> 添加步骤</Button>
            </CardHeader>
            <CardContent className="space-y-4">
                {steps.map((step, index) => (
                    <div key={index} className="p-4 border rounded-lg bg-card/50 space-y-4">
                        <div className="flex items-center gap-4">
                            <div className="font-mono text-muted-foreground w-6">{index + 1}</div>
                            <Input value={step.name} onChange={(e) => updateStep(index, 'name', e.target.value)} className="w-40" />
                            <div className="flex-1" />
                            <div className="flex items-center gap-2">
                                <Label className="whitespace-nowrap text-xs text-muted-foreground">完成后等待</Label>
                                <NumericInput value={step.interval} onValueChange={(v) => updateStep(index, 'interval', v)} integer className="w-24 h-7 text-xs px-2" />
                                <span className="text-xs text-muted-foreground">ms</span>
                            </div>
                            <div className="flex gap-1">
                                <Button size="icon" variant="ghost" onClick={() => moveStep(index, -1)}><ArrowUp className="w-4 h-4" /></Button>
                                <Button size="icon" variant="ghost" onClick={() => moveStep(index, 1)}><ArrowDown className="w-4 h-4" /></Button>
                                <Button size="icon" variant="ghost" className="text-destructive" onClick={() => deleteStep(index)}><Trash2 className="w-4 h-4" /></Button>
                            </div>
                        </div>

                        <div className="grid grid-cols-2 xl:grid-cols-5 gap-3 pt-2">
                            {(['X', 'Y', 'Z', 'A'] as const).map((axis) => (
                                <div key={axis} className="space-y-2 p-3 rounded-lg border border-border/60 bg-muted/20">
                                    <div className="flex items-center justify-between">
                                        <span className="font-bold text-xs">{axis} 轴</span>
                                        <Switch
                                            checked={(step as any)[axis].enable === 'E'}
                                            onCheckedChange={(checked) => updateStep(index, `${axis}.enable`, checked ? 'E' : 'D')}
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

                            <div className="space-y-2 p-3 rounded-lg border border-cyan-500/20 bg-cyan-500/5">
                                <div className="flex items-center justify-between">
                                    <span className="font-bold text-xs text-cyan-600 dark:text-cyan-400">进样泵</span>
                                    <Switch
                                      checked={!!step.pump?.enable}
                                      onCheckedChange={(checked) => updateStep(index, 'pump.enable', checked)}
                                    />
                                </div>
                                {step.pump?.enable && (
                                    <>
                                        <div className="space-y-1">
                                            <Label className="text-xs text-muted-foreground">转速%</Label>
                                            <NumericInput
                                              className="h-7 text-xs px-2"
                                              integer
                                              min={0}
                                              max={100}
                                              value={step.pump?.speed ?? 0}
                                              onValueChange={(v) => updateStep(index, 'pump.speed', v)}
                                            />
                                        </div>
                                        <div className="space-y-1">
                                            <Label className="text-xs text-muted-foreground">时长ms</Label>
                                            <NumericInput
                                              className="h-7 text-xs px-2"
                                              integer
                                              min={0}
                                              value={step.pump?.duration_ms ?? 0}
                                              onValueChange={(v) => updateStep(index, 'pump.duration_ms', v)}
                                            />
                                        </div>
                                    </>
                                )}
                                <p className="text-xs leading-4 text-muted-foreground">
                                  进样泵按设定时长运行后自动关闭。
                                </p>
                            </div>
                        </div>
                    </div>
                ))}
            </CardContent>
        </Card>
      </div>
    </div>
  )
}

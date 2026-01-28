import { useEffect, useState } from 'react'
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Play, Square, Pause, Save, FolderOpen, Plus, Trash2, ArrowUp, ArrowDown } from 'lucide-react'
import { useAppStore } from '@/store'

interface PumpConfig {
  enable: string // "E" or "D"
  direction: string // "F" or "B"
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

const DEFAULT_PUMP: PumpConfig = { enable: "D", direction: "F", speed: "5", angle: "0" }
const DEFAULT_STEP: Step = {
    name: "新步骤",
    interval: 1000,
    X: { ...DEFAULT_PUMP },
    Y: { ...DEFAULT_PUMP },
    Z: { ...DEFAULT_PUMP },
    A: { ...DEFAULT_PUMP }
}

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
            setSteps(data.sampling_sequence.steps || [])
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
    await fetch(`/api/mission/${action}`, { method: 'POST' })
  }

  // Preset Logic
  const savePreset = async () => {
    if (!presetName) return
    await fetch(`/api/preset/auto/${presetName}`, {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({ steps, loop_count: loopCount })
    })
    alert('预设已保存')
  }

  const loadPreset = async () => {
    if (!presetName) return
    try {
        const res = await fetch(`/api/preset/auto/${presetName}`)
        if (res.ok) {
            const data = await res.json()
            if (data.success) {
                setSteps(data.data.steps)
                setLoopCount(data.data.loop_count)
            }
        } else {
            alert('预设不存在')
        }
    } catch (e) {
        console.error(e)
    }
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
      // Support nested updates like "X.angle"
      if (field.includes('.')) {
          const [parent, child] = field.split('.')
          // @ts-ignore
          newSteps[index][parent][child] = value
      } else {
          // @ts-ignore
          newSteps[index][field] = value
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
        {/* Presets & Config */}
        <Card className="lg:col-span-1 h-fit">
            <CardHeader>
                <CardTitle>全局配置</CardTitle>
            </CardHeader>
            <CardContent className="space-y-4">
                <div className="space-y-2">
                    <Label>循环次数 (0 = 无限循环)</Label>
                    <Input type="number" value={loopCount} onChange={(e) => setLoopCount(parseInt(e.target.value))} />
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
            </CardContent>
        </Card>

        {/* Steps Editor */}
        <Card className="lg:col-span-2">
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
                                <Label>间隔 (ms)</Label>
                                <Input type="number" value={step.interval} onChange={(e) => updateStep(index, 'interval', parseInt(e.target.value))} className="w-24" />
                            </div>
                            <div className="flex gap-1">
                                <Button size="icon" variant="ghost" onClick={() => moveStep(index, -1)}><ArrowUp className="w-4 h-4" /></Button>
                                <Button size="icon" variant="ghost" onClick={() => moveStep(index, 1)}><ArrowDown className="w-4 h-4" /></Button>
                                <Button size="icon" variant="ghost" className="text-destructive" onClick={() => deleteStep(index)}><Trash2 className="w-4 h-4" /></Button>
                            </div>
                        </div>
                        
                        {/* Pump Config Grid */}
                        <div className="grid grid-cols-4 gap-4 pt-2">
                            {['X', 'Y', 'Z', 'A'].map((axis) => (
                                <div key={axis} className="space-y-2 p-2 rounded bg-muted/20">
                                    <div className="font-bold text-center text-xs mb-2">{axis} 轴</div>
                                    <div className="flex items-center justify-between">
                                        <Label className="text-[10px]">启用</Label>
                                        <input type="checkbox" 
                                            // @ts-ignore
                                            checked={step[axis].enable === 'E'} 
                                            // @ts-ignore
                                            onChange={(e) => updateStep(index, `${axis}.enable`, e.target.checked ? 'E' : 'D')} 
                                        />
                                    </div>
                                    {/* @ts-ignore */}
                                    {step[axis].enable === 'E' && (
                                        <>
                                            <div className="grid grid-cols-2 gap-1">
                                                <Label className="text-[10px]">角度</Label>
                                                {/* @ts-ignore */}
                                                <Input className="h-6 text-[10px] px-1" value={step[axis].angle} onChange={(e) => updateStep(index, `${axis}.angle`, e.target.value)} />
                                            </div>
                                            <div className="grid grid-cols-2 gap-1">
                                                <Label className="text-[10px]">速度</Label>
                                                {/* @ts-ignore */}
                                                <Input className="h-6 text-[10px] px-1" value={step[axis].speed} onChange={(e) => updateStep(index, `${axis}.speed`, e.target.value)} />
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

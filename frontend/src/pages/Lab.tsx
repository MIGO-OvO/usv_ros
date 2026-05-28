import { useEffect, useState } from 'react'
import { Activity, FlaskConical, Pause, Play, RotateCcw, Save, Square } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Switch } from '@/components/ui/switch'

interface LabConfig {
  enabled: boolean
  profile: string
  position_source: string
  allow_no_gps: boolean
  bypass_pid_wait: boolean
  real_propulsion_enabled: boolean
  include_lab_data_by_default: boolean
  sim: {
    start_lat: number
    start_lng: number
    heading_deg: number
    max_speed_mps: number
    wheel_base_m: number
  }
}

interface LabStatus {
  enabled: boolean
  running: boolean
  speed_mps: number
  heading_deg: number
  virtual_propulsion: {
    left: number
    right: number
    real_output_enabled: boolean
  }
}

const fallbackConfig: LabConfig = {
  enabled: false,
  profile: 'semi_hardware',
  position_source: 'lab_sim',
  allow_no_gps: true,
  bypass_pid_wait: true,
  real_propulsion_enabled: false,
  include_lab_data_by_default: false,
  sim: {
    start_lat: 30,
    start_lng: 120,
    heading_deg: 0,
    max_speed_mps: 1,
    wheel_base_m: 0.6,
  },
}

const fallbackStatus: LabStatus = {
  enabled: false,
  running: false,
  speed_mps: 0,
  heading_deg: 0,
  virtual_propulsion: {
    left: 0,
    right: 0,
    real_output_enabled: false,
  },
}

export default function Lab() {
  const [config, setConfig] = useState<LabConfig>(fallbackConfig)
  const [status, setStatus] = useState<LabStatus>(fallbackStatus)
  const [pending, setPending] = useState('')
  const [message, setMessage] = useState('')

  const refresh = async () => {
    const res = await fetch('/api/lab/status')
    const json = await res.json()
    if (json.data?.config) setConfig(json.data.config)
    if (json.data?.status) setStatus({ ...fallbackStatus, ...json.data.status })
  }

  useEffect(() => {
    refresh().catch(() => {})
    const timer = window.setInterval(() => refresh().catch(() => {}), 1000)
    return () => window.clearInterval(timer)
  }, [])

  const updateConfig = (patch: Partial<LabConfig>) => {
    setConfig((current) => ({ ...current, ...patch }))
  }

  const updateSim = (key: keyof LabConfig['sim'], value: string) => {
    setConfig((current) => ({
      ...current,
      sim: { ...current.sim, [key]: Number(value) || 0 },
    }))
  }

  const run = async (key: string, action: () => Promise<void>) => {
    if (pending) return
    setPending(key)
    setMessage('')
    try {
      await action()
    } finally {
      setPending('')
    }
  }

  const saveConfig = async () => {
    const res = await fetch('/api/lab/config', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(config),
    })
    const json = await res.json()
    setMessage(json.message || (json.success ? '已保存' : '保存失败'))
    if (json.data) setConfig(json.data)
  }

  const start = async () => {
    const res = await fetch('/api/lab/start', { method: 'POST' })
    const json = await res.json()
    setMessage(json.message || '已启动')
    await refresh()
  }

  const stop = async () => {
    const res = await fetch('/api/lab/stop', { method: 'POST' })
    const json = await res.json()
    setMessage(json.message || '已停止')
    await refresh()
  }

  const propulsion = status.virtual_propulsion || fallbackStatus.virtual_propulsion

  return (
    <div className="p-4 md:p-8 space-y-6 max-w-7xl mx-auto">
      <header className="flex flex-col md:flex-row md:items-center justify-between gap-4">
        <div>
          <h1 className="text-3xl font-bold tracking-tight">实验室测试</h1>
          <p className="text-muted-foreground">半实物采样、模拟走航与虚拟差速输出</p>
        </div>
        <div className="flex items-center gap-2">
          <Button variant="outline" disabled={!!pending} onClick={() => run('save', saveConfig)}>
            <Save className="w-4 h-4 mr-2" />
            保存
          </Button>
          <Button disabled={!!pending || status.running} onClick={() => run('start', start)}>
            <Play className="w-4 h-4 mr-2" />
            启动
          </Button>
          <Button variant="secondary" disabled={!!pending || !status.running} onClick={() => run('stop', stop)}>
            <Square className="w-4 h-4 mr-2" />
            停止
          </Button>
        </div>
      </header>

      {message && <div className="rounded-md border bg-card px-4 py-3 text-sm">{message}</div>}

      <div className="grid grid-cols-1 xl:grid-cols-[1fr_360px] gap-6">
        <div className="space-y-6">
          <Card>
            <CardHeader>
              <CardTitle className="flex items-center gap-2">
                <FlaskConical className="h-5 w-5" />
                模式
              </CardTitle>
            </CardHeader>
            <CardContent className="grid grid-cols-1 md:grid-cols-2 gap-4">
              <label className="flex items-center justify-between rounded-md border p-3">
                <span>启用实验模式</span>
                <Switch checked={config.enabled} onCheckedChange={(enabled) => updateConfig({ enabled })} />
              </label>
              <label className="flex items-center justify-between rounded-md border p-3">
                <span>跳过 PID 角度等待</span>
                <Switch checked={config.bypass_pid_wait} onCheckedChange={(bypass_pid_wait) => updateConfig({ bypass_pid_wait })} />
              </label>
              <label className="flex items-center justify-between rounded-md border p-3">
                <span>允许无 GPS</span>
                <Switch checked={config.allow_no_gps} onCheckedChange={(allow_no_gps) => updateConfig({ allow_no_gps })} />
              </label>
              <label className="flex items-center justify-between rounded-md border p-3">
                <span>真实推进输出</span>
                <Switch checked={config.real_propulsion_enabled} disabled onCheckedChange={() => {}} />
              </label>
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle>模拟走航参数</CardTitle>
            </CardHeader>
            <CardContent className="grid grid-cols-1 md:grid-cols-3 gap-4">
              {([
                ['start_lat', '起始纬度'],
                ['start_lng', '起始经度'],
                ['heading_deg', '航向'],
                ['max_speed_mps', '最大航速'],
                ['wheel_base_m', '差速轴距'],
              ] as const).map(([key, label]) => (
                <div key={key} className="space-y-2">
                  <Label>{label}</Label>
                  <Input type="number" value={config.sim[key]} onChange={(event) => updateSim(key, event.target.value)} />
                </div>
              ))}
            </CardContent>
          </Card>
        </div>

        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <Activity className="h-5 w-5" />
              状态
            </CardTitle>
          </CardHeader>
          <CardContent className="space-y-4">
            <div className="grid grid-cols-2 gap-3 text-sm">
              <div className="rounded-md border p-3">
                <div className="text-muted-foreground">运行</div>
                <div className="font-medium">{status.running ? '是' : '否'}</div>
              </div>
              <div className="rounded-md border p-3">
                <div className="text-muted-foreground">航速</div>
                <div className="font-medium">{(status.speed_mps || 0).toFixed(2)} m/s</div>
              </div>
              <div className="rounded-md border p-3">
                <div className="text-muted-foreground">航向</div>
                <div className="font-medium">{(status.heading_deg || 0).toFixed(1)} deg</div>
              </div>
              <div className="rounded-md border p-3">
                <div className="text-muted-foreground">实物推进</div>
                <div className="font-medium">{propulsion.real_output_enabled ? '启用' : '禁用'}</div>
              </div>
            </div>
            <div className="rounded-md border p-3 space-y-2">
              <div className="flex justify-between text-sm">
                <span>左推进</span>
                <span className="font-mono">{(propulsion.left || 0).toFixed(2)}</span>
              </div>
              <div className="h-2 rounded bg-muted">
                <div className="h-2 rounded bg-primary" style={{ width: `${Math.abs(propulsion.left || 0) * 100}%` }} />
              </div>
              <div className="flex justify-between text-sm">
                <span>右推进</span>
                <span className="font-mono">{(propulsion.right || 0).toFixed(2)}</span>
              </div>
              <div className="h-2 rounded bg-muted">
                <div className="h-2 rounded bg-primary" style={{ width: `${Math.abs(propulsion.right || 0) * 100}%` }} />
              </div>
            </div>
            <Button variant="outline" className="w-full" onClick={() => refresh().catch(() => {})}>
              <RotateCcw className="w-4 h-4 mr-2" />
              刷新
            </Button>
            <Button variant="secondary" className="w-full" disabled>
              <Pause className="w-4 h-4 mr-2" />
              推进台架输出未开放
            </Button>
          </CardContent>
        </Card>
      </div>
    </div>
  )
}

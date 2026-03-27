import { useEffect, useState } from 'react'
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Save, RefreshCw, Zap, Target, RotateCcw, Usb, Activity } from 'lucide-react'
import { useAppStore } from '@/store'

interface SerialPort {
  path: string
  description: string
  hwid: string
  by_id?: string
}

interface HardwareConfig {
  pump_serial_port: string
  pump_baudrate: number
  pump_timeout: number
  spectrometer_auto_start: boolean
}

const DEFAULT_HW: HardwareConfig = {
  pump_serial_port: '/dev/ttyUSB0',
  pump_baudrate: 115200,
  pump_timeout: 1.0,
  spectrometer_auto_start: false,
}

export default function Settings() {
  const { pumpAngles, rawAngles } = useAppStore()
  const [config, setConfig] = useState({
      Kp: 0, Ki: 0, Kd: 0, output_min: -255, output_max: 255
  })

  const [hw, setHw] = useState<HardwareConfig>({ ...DEFAULT_HW })
  const [serialPorts, setSerialPorts] = useState<SerialPort[]>([])
  const [hwLoading, setHwLoading] = useState(false)
  const [hwMsg, setHwMsg] = useState('')

  useEffect(() => {
    fetchConfig()
    fetchHardwareConfig()
  }, [])

  const fetchHardwareConfig = async () => {
    try {
      const res = await fetch('/api/hardware/config')
      const data = await res.json()
      if (data.success) setHw({ ...DEFAULT_HW, ...data.data })
    } catch (e) { console.error(e) }
  }

  const refreshDevices = async () => {
    try {
      const sp = await fetch('/api/hardware/serial-ports').then(r => r.json())
      if (sp.success) setSerialPorts(sp.ports || [])
    } catch (e) { console.error(e) }
  }

  const testPumpPort = async () => {
    setHwMsg('')
    try {
      const res = await fetch('/api/hardware/test-pump-port', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ serial_port: hw.pump_serial_port, baudrate: hw.pump_baudrate, timeout: hw.pump_timeout })
      })
      const data = await res.json()
      setHwMsg(data.message)
    } catch (e: any) { setHwMsg(e.message) }
  }



  const saveAndApplyHardware = async () => {
    setHwLoading(true)
    setHwMsg('')
    try {
      const res = await fetch('/api/hardware/apply', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(hw)
      })
      const data = await res.json()
      const msgs: string[] = [data.message || '']
      if (data.results?.pump) msgs.push('泵控: ' + data.results.pump.message)
      setHwMsg(msgs.join(' | '))
    } catch (e: any) { setHwMsg(e.message) }
    setHwLoading(false)
  }

  const saveHardwareOnly = async () => {
    setHwMsg('')
    try {
      const res = await fetch('/api/hardware/config', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(hw)
      })
      const data = await res.json()
      setHwMsg(data.message || '已保存')
    } catch (e: any) { setHwMsg(e.message) }
  }

  const fetchConfig = async () => {
    try {
        const res = await fetch('/api/pid/config')
        const data = await res.json()
        if (data.success) {
            setConfig(data.data)
        }
    } catch (e) {
        console.error(e)
    }
  }

/*
  const fetchOffsets = async () => {
      try {
          const res = await fetch('/api/calibration/offsets')
          const data = await res.json()
          if (data.success) setOffsets(data.data)
      } catch (e) {
          console.error(e)
      }
  }
*/
  const saveConfig = async () => {
      try {
          await fetch('/api/pid/config', {
              method: 'POST',
              headers: {'Content-Type': 'application/json'},
              body: JSON.stringify(config)
          })
          alert("PID 参数已更新")
      } catch (e) {
          console.error(e)
      }
  }

  const setZero = async (axis?: string) => {
      if (!confirm(axis ? `确定要将 ${axis} 轴当前位置设为零点吗？` : "确定要将所有轴设为零点吗？")) return
      await fetch('/api/calibration/zero', {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({ axis })
      })
      // fetchOffsets()
  }

  const resetZero = async (axis?: string) => {
      if (!confirm("确定要重置零点偏移吗？")) return
      await fetch('/api/calibration/reset', {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({ axis })
      })
      // fetchOffsets()
  }

  const handleChange = (key: string, value: string) => {
      setConfig(prev => ({ ...prev, [key]: parseFloat(value) }))
  }

  return (
    <div className="p-4 md:p-8 space-y-6 max-w-7xl mx-auto">
      <header>
          <h1 className="text-3xl font-bold tracking-tight">系统设置</h1>
          <p className="text-muted-foreground">底层控制参数与校准。</p>
      </header>

      <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
        <Card>
            <CardHeader>
                <CardTitle className="flex items-center gap-2">
                    <Zap className="w-5 h-5 text-yellow-500" />
                    PID 参数
                </CardTitle>
            </CardHeader>
            <CardContent className="space-y-4">
                <div className="grid grid-cols-3 gap-4">
                    <div className="space-y-2">
                        <Label>Kp (比例)</Label>
                        <Input type="number" step="0.001" value={config.Kp} onChange={(e) => handleChange('Kp', e.target.value)} />
                    </div>
                    <div className="space-y-2">
                        <Label>Ki (积分)</Label>
                        <Input type="number" step="0.001" value={config.Ki} onChange={(e) => handleChange('Ki', e.target.value)} />
                    </div>
                    <div className="space-y-2">
                        <Label>Kd (微分)</Label>
                        <Input type="number" step="0.001" value={config.Kd} onChange={(e) => handleChange('Kd', e.target.value)} />
                    </div>
                </div>

                <div className="grid grid-cols-2 gap-4">
                     <div className="space-y-2">
                        <Label>最小输出 (V)</Label>
                        <Input type="number" step="0.1" value={config.output_min} onChange={(e) => handleChange('output_min', e.target.value)} />
                    </div>
                    <div className="space-y-2">
                        <Label>最大输出 (V)</Label>
                        <Input type="number" step="0.1" value={config.output_max} onChange={(e) => handleChange('output_max', e.target.value)} />
                    </div>
                </div>

                <div className="flex gap-2 pt-4">
                    <Button className="flex-1" onClick={saveConfig}><Save className="w-4 h-4 mr-2" /> 保存参数</Button>
                    <Button variant="outline" onClick={fetchConfig}><RefreshCw className="w-4 h-4" /></Button>
                </div>
            </CardContent>
        </Card>

        <Card>
            <CardHeader>
                <CardTitle className="flex items-center gap-2">
                    <Target className="w-5 h-5 text-blue-500" />
                    零点校准
                </CardTitle>
            </CardHeader>
            <CardContent className="space-y-6">
                <div className="grid grid-cols-4 gap-4 text-center text-sm font-medium text-muted-foreground mb-2">
                    <div>轴</div>
                    <div>原始角度</div>
                    <div>校准角度</div>
                    <div>操作</div>
                </div>
                
                {['X', 'Y', 'Z', 'A'].map((axis) => (
                    <div key={axis} className="grid grid-cols-4 gap-4 items-center text-sm">
                        <div className="font-bold text-center bg-muted/30 py-2 rounded">{axis}</div>
                        {/* @ts-ignore */}
                        <div className="text-center font-mono text-muted-foreground">{rawAngles[axis]?.toFixed(2)}°</div>
                        {/* @ts-ignore */}
                        <div className="text-center font-mono font-bold text-primary">{pumpAngles[axis]?.toFixed(2)}°</div>
                        <div className="flex gap-1 justify-center">
                            <Button size="icon" variant="outline" className="h-8 w-8" onClick={() => setZero(axis)} title="设为零点">
                                <Target className="w-3 h-3" />
                            </Button>
                            <Button size="icon" variant="ghost" className="h-8 w-8 text-muted-foreground" onClick={() => resetZero(axis)} title="重置">
                                <RotateCcw className="w-3 h-3" />
                            </Button>
                        </div>
                    </div>
                ))}

                <div className="pt-4 border-t flex gap-2">
                     <Button variant="secondary" className="flex-1" onClick={() => setZero()}>全部设为零点</Button>
                     <Button variant="ghost" className="flex-1" onClick={() => resetZero()}>全部重置</Button>
                </div>

                <div className="text-xs text-muted-foreground bg-muted/50 p-3 rounded">
                    <p>校准原理：当前位置 = 原始位置 - 偏移量。</p>
                    <p>点击“设为零点”将当前位置记录为偏移量，使读数归零。</p>
                </div>
            </CardContent>
        </Card>
      </div>

      {/* ==================== 硬件连接设置 ==================== */}
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <Usb className="w-5 h-5 text-blue-500" />
            硬件连接设置
          </CardTitle>
        </CardHeader>
        <CardContent className="space-y-6">
          <div className="flex gap-2 mb-2">
            <Button variant="outline" size="sm" onClick={refreshDevices}><RefreshCw className="w-4 h-4 mr-1" />刷新设备</Button>
          </div>

          {/* 泵控板串口 */}
          <div className="space-y-3">
            <h4 className="font-medium text-sm">泵控板串口</h4>
            <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
              <div className="space-y-2">
                <Label>串口路径</Label>
                <select
                  className="flex h-10 w-full rounded-md border border-input bg-background px-3 py-2 text-sm"
                  value={hw.pump_serial_port}
                  onChange={e => setHw(p => ({ ...p, pump_serial_port: e.target.value }))}
                >
                  <option value={hw.pump_serial_port}>{hw.pump_serial_port}</option>
                  {serialPorts.filter(p => p.path !== hw.pump_serial_port).map(p => (
                    <option key={p.by_id || p.path} value={p.by_id || p.path}>
                      {p.by_id || p.path}{p.description ? ` (${p.description})` : ''}
                    </option>
                  ))}
                </select>
              </div>
              <div className="space-y-2">
                <Label>波特率</Label>
                <Input type="number" value={hw.pump_baudrate} onChange={e => setHw(p => ({ ...p, pump_baudrate: parseInt(e.target.value) || 115200 }))} />
              </div>
              <div className="space-y-2">
                <Label>超时 (秒)</Label>
                <Input type="number" step="0.1" value={hw.pump_timeout} onChange={e => setHw(p => ({ ...p, pump_timeout: parseFloat(e.target.value) || 1.0 }))} />
              </div>
            </div>
            <Button variant="outline" size="sm" onClick={testPumpPort}><Activity className="w-4 h-4 mr-1" />测试泵控连接</Button>
          </div>



          {hwMsg && (
            <div className="text-sm p-3 rounded bg-muted/50">{hwMsg}</div>
          )}

          <div className="flex gap-2">
            <Button onClick={saveHardwareOnly} variant="outline"><Save className="w-4 h-4 mr-1" />仅保存</Button>
            <Button onClick={saveAndApplyHardware} disabled={hwLoading}>
              {hwLoading ? <RefreshCw className="w-4 h-4 mr-1 animate-spin" /> : <Zap className="w-4 h-4 mr-1" />}
              保存并应用
            </Button>
          </div>

          <div className="text-xs text-muted-foreground bg-muted/50 p-3 rounded">
            <p>"仅保存"将配置写入文件，下次重启生效。</p>
            <p>"保存并应用"将立即通知节点重连设备，无需重启系统。</p>
            <p>串口建议优先选择 /dev/serial/by-id/... 路径，避免 USB 口漂移。</p>
          </div>
        </CardContent>
      </Card>
    </div>
  )
}

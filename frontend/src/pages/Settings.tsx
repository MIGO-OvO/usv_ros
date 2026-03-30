import { useEffect, useState } from 'react'
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Save, RefreshCw, Zap, Target, RotateCcw, Usb, Activity } from 'lucide-react'
import { cn } from '@/lib/utils'
import { useAppStore } from '@/store'
import { toast } from '@/hooks/use-toast'
import { useConfirm } from '@/hooks/use-confirm'

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
  ads_address: string
  spectro_channel: number
  mux: string
  gain: number
  vref_mode: string
  adc_rate: number
  publish_rate: number
  continuous_mode: boolean
  auto_start: boolean
  reference_voltage: number
  baseline_voltage: number
  i2c_mapping: Record<'X' | 'Y' | 'Z' | 'A', number>
}

const DEFAULT_HW: HardwareConfig = {
  pump_serial_port: '/dev/ttyUSB0',
  pump_baudrate: 115200,
  pump_timeout: 1.0,
  ads_address: '0x40',
  spectro_channel: 2,
  mux: 'AIN0',
  gain: 1,
  vref_mode: 'AVDD',
  adc_rate: 90,
  publish_rate: 20,
  continuous_mode: true,
  auto_start: false,
  reference_voltage: 2.5,
  baseline_voltage: 0.0,
  i2c_mapping: { X: 0, Y: 3, Z: 4, A: 7 },
}

export default function Settings() {
  const { pumpAngles, rawAngles } = useAppStore()
  const confirm = useConfirm()
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
      if (data.success) setHw({ ...DEFAULT_HW, ...data.data, i2c_mapping: { ...DEFAULT_HW.i2c_mapping, ...(data.data?.i2c_mapping || {}) } })
    } catch (e) { console.error(e) }
  }

  const setMapChannel = (axis: 'X' | 'Y' | 'Z' | 'A', value: string) => {
    setHw(prev => ({
      ...prev,
      i2c_mapping: { ...prev.i2c_mapping, [axis]: parseInt(value) || 0 },
    }))
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
      if (data.results?.spectrometer) msgs.push('分光: ' + data.results.spectrometer.message)
      if (data.results?.i2c_mapping) msgs.push('映射: ' + data.results.i2c_mapping.message)
      setHwMsg(msgs.filter(Boolean).join(' | '))
      if (data.data) setHw({ ...DEFAULT_HW, ...data.data, i2c_mapping: { ...DEFAULT_HW.i2c_mapping, ...(data.data.i2c_mapping || {}) } })
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
      if (data.data) setHw({ ...DEFAULT_HW, ...data.data, i2c_mapping: { ...DEFAULT_HW.i2c_mapping, ...(data.data.i2c_mapping || {}) } })
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

  const saveConfig = async () => {
      try {
          await fetch('/api/pid/config', {
              method: 'POST',
              headers: {'Content-Type': 'application/json'},
              body: JSON.stringify(config)
          })
          toast({ title: 'PID 参数已更新', variant: 'success' })
      } catch (e) {
          console.error(e)
          toast({ title: 'PID 参数更新失败', variant: 'destructive' })
      }
  }

  const setZero = async (axis?: string) => {
      const ok = await confirm({
        title: '设置零点',
        description: axis ? `确定要将 ${axis} 轴当前位置设为零点吗？` : '确定要将所有轴设为零点吗？',
      })
      if (!ok) return
      await fetch('/api/calibration/zero', {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({ axis })
      })
  }

  const resetZero = async (axis?: string) => {
      const ok = await confirm({
        title: '重置零点',
        description: axis ? `确定要重置 ${axis} 轴零点偏移吗？` : '确定要重置所有轴零点偏移吗？',
      })
      if (!ok) return
      await fetch('/api/calibration/reset', {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({ axis })
      })
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
                
                {(['X', 'Y', 'Z', 'A'] as const).map((axis) => (
                    <div key={axis} className="grid grid-cols-4 gap-4 items-center text-sm">
                        <div className="font-bold text-center bg-muted/30 py-2 rounded">{axis}</div>
                        <div className="text-center font-mono text-muted-foreground">{rawAngles[axis]?.toFixed(2)}°</div>
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
                <select className="flex h-10 w-full rounded-md border border-input bg-background px-3 py-2 text-sm" value={hw.pump_serial_port} onChange={e => setHw(p => ({ ...p, pump_serial_port: e.target.value }))}>
                  <option value={hw.pump_serial_port}>{hw.pump_serial_port}</option>
                  {serialPorts.filter(p => p.path !== hw.pump_serial_port).map(p => (
                    <option key={p.by_id || p.path} value={p.by_id || p.path}>{p.by_id || p.path}{p.description ? ` (${p.description})` : ''}</option>
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

          <div className="space-y-3">
            <h4 className="font-medium text-sm">ADS122C04 分光参数</h4>
            <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
              <div className="space-y-2"><Label>ADS 地址</Label><Input value={hw.ads_address} onChange={e => setHw(p => ({ ...p, ads_address: e.target.value }))} /></div>
              <div className="space-y-2"><Label>TCA 分光通道</Label><Input type="number" min="0" max="7" value={hw.spectro_channel} onChange={e => setHw(p => ({ ...p, spectro_channel: parseInt(e.target.value) || 0 }))} /></div>
              <div className="space-y-2"><Label>AIN 通道</Label><select className="flex h-10 w-full rounded-md border border-input bg-background px-3 py-2 text-sm" value={hw.mux} onChange={e => setHw(p => ({ ...p, mux: e.target.value }))}><option value="AIN0">AIN0</option><option value="AIN1">AIN1</option><option value="AIN2">AIN2</option><option value="AIN3">AIN3</option></select></div>
              <div className="space-y-2"><Label>增益</Label><select className="flex h-10 w-full rounded-md border border-input bg-background px-3 py-2 text-sm" value={hw.gain} onChange={e => setHw(p => ({ ...p, gain: parseInt(e.target.value) || 1 }))}><option value="1">1</option><option value="2">2</option><option value="4">4</option><option value="8">8</option><option value="16">16</option><option value="32">32</option><option value="64">64</option><option value="128">128</option></select></div>
              <div className="space-y-2"><Label>参考电压</Label><select className="flex h-10 w-full rounded-md border border-input bg-background px-3 py-2 text-sm" value={hw.vref_mode} onChange={e => setHw(p => ({ ...p, vref_mode: e.target.value }))}><option value="AVDD">AVDD</option><option value="INTERNAL">INTERNAL</option><option value="EXTERNAL">EXTERNAL</option></select></div>
              <div className="space-y-2"><Label>ADC 速率</Label><Input type="number" value={hw.adc_rate} onChange={e => setHw(p => ({ ...p, adc_rate: parseInt(e.target.value) || 90 }))} /></div>
              <div className="space-y-2"><Label>发布频率</Label><Input type="number" value={hw.publish_rate} onChange={e => setHw(p => ({ ...p, publish_rate: parseInt(e.target.value) || 20 }))} /></div>
              <div className="space-y-2"><Label>参考电压值</Label><Input type="number" step="0.001" value={hw.reference_voltage} onChange={e => setHw(p => ({ ...p, reference_voltage: parseFloat(e.target.value) || 0 }))} /></div>
              <div className="space-y-2"><Label>基线电压</Label><Input type="number" step="0.001" value={hw.baseline_voltage} onChange={e => setHw(p => ({ ...p, baseline_voltage: parseFloat(e.target.value) || 0 }))} /></div>
            </div>
            <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
              <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={hw.continuous_mode} onChange={e => setHw(p => ({ ...p, continuous_mode: e.target.checked }))} />连续采样模式</label>
              <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={hw.auto_start} onChange={e => setHw(p => ({ ...p, auto_start: e.target.checked }))} />启动后自动开启分光</label>
            </div>
          </div>

          <div className="space-y-3">
            <h4 className="font-medium text-sm">角度 / 分光 TCA 通道映射</h4>
            <div className="grid grid-cols-2 md:grid-cols-5 gap-4">
              {(['X', 'Y', 'Z', 'A'] as const).map(axis => (
                <div key={axis} className="space-y-2">
                  <Label>{axis} 轴通道</Label>
                  <Input type="number" min="0" max="7" value={hw.i2c_mapping[axis]} onChange={e => setMapChannel(axis, e.target.value)} />
                </div>
              ))}
              <div className="space-y-2">
                <Label>分光通道</Label>
                <Input type="number" min="0" max="7" value={hw.spectro_channel} onChange={e => setHw(p => ({ ...p, spectro_channel: parseInt(e.target.value) || 0 }))} />
              </div>
            </div>
          </div>

          {hwMsg && (
            <div className={cn(
              "text-sm p-3 rounded",
              hwMsg.includes('失败') || hwMsg.includes('error') || hwMsg.includes('Error')
                ? "bg-red-500/10 text-red-700 dark:text-red-400"
                : "bg-emerald-500/10 text-emerald-700 dark:text-emerald-400"
            )}>{hwMsg}</div>
          )}

          <div className="flex gap-2">
            <Button onClick={saveHardwareOnly} variant="outline"><Save className="w-4 h-4 mr-1" />仅保存</Button>
            <Button onClick={saveAndApplyHardware} disabled={hwLoading}>
              {hwLoading ? <RefreshCw className="w-4 h-4 mr-1 animate-spin" /> : <Zap className="w-4 h-4 mr-1" />}
              保存并应用
            </Button>
          </div>

          <div className="text-xs text-muted-foreground bg-muted/50 p-3 rounded">
            <p>支持在 Web 端直接修改 ADS 参数、分光通道以及四个角度采样通道映射。</p>
            <p>“仅保存”写入配置文件；“保存并应用”会同时重连泵控并下发分光/I2C 映射。</p>
            <p>串口建议优先选择 /dev/serial/by-id/... 路径，避免 USB 口漂移。</p>
          </div>
        </CardContent>
      </Card>
    </div>
  )
}

import { useEffect, useState } from 'react'
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Switch } from "@/components/ui/switch"
import { Save, RefreshCw, Zap, Target, RotateCcw, Usb, Activity, Route } from 'lucide-react'
import { cn } from '@/lib/utils'
import { useAppStore } from '@/store'
import { toast } from '@/hooks/use-toast'
import { useConfirm } from '@/hooks/use-confirm'
import { SystemLogViewer } from '@/components/system-log-viewer'

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

interface PollutionMetricConfig {
  enabled: boolean
  slope: number
  intercept: number
  unit: string
  display_name: string
  pollutant_name: string
  method_name: string
  wavelength_nm: number | null
  calibration_id: string
  calibrated_at: string
  min_valid: number | null
  max_valid: number | null
  lod: number | null
  loq: number | null
  clamp_negative: boolean
}

interface MappingProfileConfig {
  survey_min_distance_m: number
  survey_min_speed_mps: number
  survey_max_speed_mps: number
  survey_require_valid_spectrometer: boolean
  survey_require_gps: boolean
  survey_max_position_age_s: number
}

type PollutionMetricOptionalNumberKey = 'wavelength_nm' | 'min_valid' | 'max_valid' | 'lod' | 'loq'
type MappingProfileNumberKey = 'survey_min_distance_m' | 'survey_min_speed_mps' | 'survey_max_speed_mps' | 'survey_max_position_age_s'

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
  reference_voltage: 0.0,
  baseline_voltage: 0.0,
  i2c_mapping: { X: 0, Y: 3, Z: 4, A: 7 },
}

const DEFAULT_POLLUTION_METRIC: PollutionMetricConfig = {
  enabled: false,
  slope: 1.0,
  intercept: 0.0,
  unit: 'mg/L',
  display_name: '浓度',
  pollutant_name: '浓度',
  method_name: '吸光度线性工作曲线',
  wavelength_nm: null,
  calibration_id: '',
  calibrated_at: '',
  min_valid: null,
  max_valid: null,
  lod: null,
  loq: null,
  clamp_negative: false,
}

const DEFAULT_MAPPING_PROFILE: MappingProfileConfig = {
  survey_min_distance_m: 5.0,
  survey_min_speed_mps: 0.0,
  survey_max_speed_mps: 0.0,
  survey_require_valid_spectrometer: true,
  survey_require_gps: true,
  survey_max_position_age_s: 5.0,
}

const parseOptionalMetricNumber = (value: string): number | null => {
  if (value.trim() === '') return null
  const parsed = Number(value)
  return Number.isFinite(parsed) ? parsed : null
}

export default function Settings() {
  const pumpAngles = useAppStore((state) => state.pumpAngles)
  const rawAngles = useAppStore((state) => state.rawAngles)
  const confirm = useConfirm()
  const [config, setConfig] = useState({
      Kp: 0, Ki: 0, Kd: 0, output_min: -255, output_max: 255
  })

  const [hw, setHw] = useState<HardwareConfig>({ ...DEFAULT_HW })
  const [pollutionMetric, setPollutionMetric] = useState<PollutionMetricConfig>({ ...DEFAULT_POLLUTION_METRIC })
  const [mappingProfile, setMappingProfile] = useState<MappingProfileConfig>({ ...DEFAULT_MAPPING_PROFILE })
  const [serialPorts, setSerialPorts] = useState<SerialPort[]>([])
  const [hwLoading, setHwLoading] = useState(false)
  const [hwMsg, setHwMsg] = useState('')
  const [homing, setHoming] = useState<string | null>(null)

  useEffect(() => {
    fetchConfig()
    fetchPollutionConfig()
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
    } catch (e: unknown) { setHwMsg(e instanceof Error ? e.message : String(e)) }
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
    } catch (e: unknown) { setHwMsg(e instanceof Error ? e.message : String(e)) }
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
    } catch (e: unknown) { setHwMsg(e instanceof Error ? e.message : String(e)) }
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

  const fetchPollutionConfig = async () => {
    try {
      const res = await fetch('/api/config')
      const data = await res.json()
      setPollutionMetric({ ...DEFAULT_POLLUTION_METRIC, ...(data.pollution_metric || {}) })
      setMappingProfile({ ...DEFAULT_MAPPING_PROFILE, ...(data.mapping_profile || {}) })
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

  const savePollutionMetric = async () => {
    try {
      const res = await fetch('/api/config', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ pollution_metric: pollutionMetric }),
      })
      const data = await res.json()
      if (!data.success) throw new Error(data.message || '保存失败')
      toast({ title: '污染指标已更新', variant: 'success' })
    } catch (e) {
      console.error(e)
      toast({ title: '污染指标保存失败', variant: 'destructive' })
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

  const moveToZero = async (axis?: string) => {
    const target = axis || 'XYZA'
    const ok = await confirm({
      title: axis ? `${axis} 轴回到校准零点` : '全部泵回到校准零点',
      description: axis
        ? `将驱动 ${axis} 轴自动转到校准后的 0°，不会修改零点偏移。确认继续吗？`
        : '将驱动 X/Y/Z/A 四轴自动转到各自校准后的 0°，不会修改零点偏移。确认继续吗？',
    })
    if (!ok) return
    setHoming(target)
    try {
      const res = await fetch('/api/calibration/start', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ motors: target }),
      })
      const data = await res.json()
      if (!res.ok || !data.success) throw new Error(data.message || '运动回零失败')
      toast({ title: axis ? `${axis} 轴已开始回到校准零点` : '四轴已开始回到校准零点', description: '请等待校准角度稳定到 0°。', variant: 'success' })
    } catch (e) {
      toast({ title: '运动回零失败', description: e instanceof Error ? e.message : String(e), variant: 'destructive' })
    } finally {
      setHoming(null)
    }
  }

  const handleChange = (key: string, value: string) => {
      setConfig(prev => ({ ...prev, [key]: parseFloat(value) }))
  }

  const saveMappingProfile = async () => {
    try {
      const res = await fetch('/api/config', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ mapping_profile: mappingProfile }),
      })
      const data = await res.json()
      if (!data.success) throw new Error(data.message || '保存失败')
      toast({ title: '走航门控已更新', variant: 'success' })
    } catch (e) {
      console.error(e)
      toast({ title: '走航门控保存失败', variant: 'destructive' })
    }
  }

  const setMetricOptionalNumber = (key: PollutionMetricOptionalNumberKey, value: string) => {
    setPollutionMetric(prev => ({ ...prev, [key]: parseOptionalMetricNumber(value) }))
  }

  const setMappingNumber = (key: MappingProfileNumberKey, value: string) => {
    const parsed = Number(value)
    setMappingProfile(prev => ({ ...prev, [key]: Number.isFinite(parsed) ? Math.max(0, parsed) : 0 }))
  }

  return (
    <div className="settings-page px-3 pb-28 pt-4 sm:px-4 md:px-6 lg:px-8 lg:pb-8 space-y-5 sm:space-y-6 max-w-7xl mx-auto">
      <header>
          <h1 className="text-2xl font-bold tracking-tight sm:text-3xl">系统设置</h1>
          <p className="text-sm text-muted-foreground sm:text-base">底层控制参数与校准。</p>
      </header>

      <div className="grid grid-cols-1 gap-4 xl:grid-cols-[minmax(0,1.35fr)_minmax(20rem,0.75fr)] xl:items-start xl:gap-6">
        <Card className="xl:col-start-2 xl:row-start-1">
            <CardHeader>
                <CardTitle className="flex items-center gap-2">
                    <Zap className="w-5 h-5 text-yellow-500" />
                    PID 参数
                </CardTitle>
            </CardHeader>
            <CardContent className="space-y-4">
                <div className="grid grid-cols-1 gap-3 sm:grid-cols-3 sm:gap-4">
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

                <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 sm:gap-4">
                     <div className="space-y-2">
                        <Label>最小输出 (V)</Label>
                        <Input type="number" step="0.1" value={config.output_min} onChange={(e) => handleChange('output_min', e.target.value)} />
                    </div>
                    <div className="space-y-2">
                        <Label>最大输出 (V)</Label>
                        <Input type="number" step="0.1" value={config.output_max} onChange={(e) => handleChange('output_max', e.target.value)} />
                    </div>
                </div>

                <div className="flex flex-col gap-2 pt-4 sm:flex-row">
                    <Button className="flex-1" onClick={saveConfig}><Save className="w-4 h-4 mr-2" /> 保存参数</Button>
                    <Button variant="outline" className="w-full sm:w-11" onClick={fetchConfig}><RefreshCw className="w-4 h-4" /></Button>
                </div>
            </CardContent>
        </Card>

        <Card className="xl:col-start-1 xl:row-span-3 xl:row-start-1">
            <CardHeader>
                <CardTitle className="flex items-center gap-2">
                    <Activity className="w-5 h-5 text-emerald-500" />
                    污染指标
                </CardTitle>
            </CardHeader>
            <CardContent className="space-y-4">
                <label className="flex items-center justify-between gap-3 rounded-md border bg-background/60 p-3 text-sm sm:border-0 sm:bg-transparent sm:p-0">
                    <span>
                      <span className="font-medium block">启用浓度工作曲线</span>
                      <span className="text-xs text-muted-foreground">未启用时地图自动使用吸光度。</span>
                    </span>
                    <Switch checked={pollutionMetric.enabled} onCheckedChange={v => setPollutionMetric(p => ({ ...p, enabled: v }))} />
                </label>
                <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 sm:gap-4">
                    <div className="space-y-2">
                        <Label>污染物名称</Label>
                        <Input value={pollutionMetric.pollutant_name} onChange={e => setPollutionMetric(p => ({ ...p, pollutant_name: e.target.value }))} />
                    </div>
                    <div className="space-y-2">
                        <Label>方法名称</Label>
                        <Input value={pollutionMetric.method_name} onChange={e => setPollutionMetric(p => ({ ...p, method_name: e.target.value }))} />
                    </div>
                    <div className="space-y-2">
                        <Label>斜率 slope</Label>
                        <Input type="number" step="0.0001" value={pollutionMetric.slope} onChange={e => setPollutionMetric(p => ({ ...p, slope: parseFloat(e.target.value) || 0 }))} />
                    </div>
                    <div className="space-y-2">
                        <Label>截距 intercept</Label>
                        <Input type="number" step="0.0001" value={pollutionMetric.intercept} onChange={e => setPollutionMetric(p => ({ ...p, intercept: parseFloat(e.target.value) || 0 }))} />
                    </div>
                    <div className="space-y-2">
                        <Label>显示名称</Label>
                        <Input value={pollutionMetric.display_name} onChange={e => setPollutionMetric(p => ({ ...p, display_name: e.target.value }))} />
                    </div>
                    <div className="space-y-2">
                        <Label>单位</Label>
                        <Input value={pollutionMetric.unit} onChange={e => setPollutionMetric(p => ({ ...p, unit: e.target.value }))} />
                    </div>
                    <div className="space-y-2">
                        <Label>波长 (nm)</Label>
                        <Input type="number" step="0.1" value={pollutionMetric.wavelength_nm ?? ''} onChange={e => setMetricOptionalNumber('wavelength_nm', e.target.value)} />
                    </div>
                    <div className="space-y-2">
                        <Label>校准编号</Label>
                        <Input value={pollutionMetric.calibration_id} onChange={e => setPollutionMetric(p => ({ ...p, calibration_id: e.target.value }))} />
                    </div>
                    <div className="space-y-2 sm:col-span-2">
                        <Label>校准时间</Label>
                        <Input value={pollutionMetric.calibrated_at} onChange={e => setPollutionMetric(p => ({ ...p, calibrated_at: e.target.value }))} />
                    </div>
                    <div className="space-y-2">
                        <Label>有效下限</Label>
                        <Input type="number" step="0.0001" value={pollutionMetric.min_valid ?? ''} onChange={e => setMetricOptionalNumber('min_valid', e.target.value)} />
                    </div>
                    <div className="space-y-2">
                        <Label>有效上限</Label>
                        <Input type="number" step="0.0001" value={pollutionMetric.max_valid ?? ''} onChange={e => setMetricOptionalNumber('max_valid', e.target.value)} />
                    </div>
                    <div className="space-y-2">
                        <Label>检出限 LOD</Label>
                        <Input type="number" step="0.0001" value={pollutionMetric.lod ?? ''} onChange={e => setMetricOptionalNumber('lod', e.target.value)} />
                    </div>
                    <div className="space-y-2">
                        <Label>定量限 LOQ</Label>
                        <Input type="number" step="0.0001" value={pollutionMetric.loq ?? ''} onChange={e => setMetricOptionalNumber('loq', e.target.value)} />
                    </div>
                    <label className="flex items-center justify-between gap-3 rounded-md border bg-background/60 p-3 text-sm sm:col-span-2 sm:border-0 sm:bg-transparent sm:p-0">
                        <span>
                          <span className="font-medium block">负浓度钳制为 0</span>
                          <span className="text-xs text-muted-foreground">仅在线性公式计算出负值时生效。</span>
                        </span>
                        <Switch checked={pollutionMetric.clamp_negative} onCheckedChange={v => setPollutionMetric(p => ({ ...p, clamp_negative: v }))} />
                    </label>
                </div>
                <div className="rounded-md bg-muted/50 p-3 text-xs text-muted-foreground">
                  当前公式：{pollutionMetric.display_name || '浓度'} = {pollutionMetric.slope || 0} × 吸光度 + {pollutionMetric.intercept || 0}
                </div>
                <div className="flex flex-col gap-2 sm:flex-row">
                    <Button className="flex-1" onClick={savePollutionMetric}><Save className="w-4 h-4 mr-2" /> 保存指标</Button>
                    <Button variant="outline" className="w-full sm:w-11" onClick={fetchPollutionConfig}><RefreshCw className="w-4 h-4" /></Button>
                </div>
            </CardContent>
        </Card>

        <Card className="xl:col-start-2 xl:row-start-2">
            <CardHeader>
                <CardTitle className="flex items-center gap-2">
                    <Route className="w-5 h-5 text-cyan-500" />
                    走航门控配置
                </CardTitle>
            </CardHeader>
            <CardContent className="space-y-4">
                <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 sm:gap-4">
                    <div className="space-y-2">
                        <Label>最小距离 (m)</Label>
                        <Input type="number" min="0" step="0.1" value={mappingProfile.survey_min_distance_m} onChange={e => setMappingNumber('survey_min_distance_m', e.target.value)} />
                    </div>
                    <div className="space-y-2">
                        <Label>位置最大年龄 (s)</Label>
                        <Input type="number" min="0" step="0.1" value={mappingProfile.survey_max_position_age_s} onChange={e => setMappingNumber('survey_max_position_age_s', e.target.value)} />
                    </div>
                    <div className="space-y-2">
                        <Label>最小速度 (m/s)</Label>
                        <Input type="number" min="0" step="0.1" value={mappingProfile.survey_min_speed_mps} onChange={e => setMappingNumber('survey_min_speed_mps', e.target.value)} />
                    </div>
                    <div className="space-y-2">
                        <Label>最大速度 (m/s)</Label>
                        <Input type="number" min="0" step="0.1" value={mappingProfile.survey_max_speed_mps} onChange={e => setMappingNumber('survey_max_speed_mps', e.target.value)} />
                    </div>
                    <label className="flex items-center justify-between gap-3 rounded-md border bg-background/60 p-3 text-sm sm:border-0 sm:bg-transparent sm:p-0">
                        <span>
                          <span className="font-medium block">需要 GPS</span>
                          <span className="text-xs text-muted-foreground">缺少或过期位置时跳过本次走航采样。</span>
                        </span>
                        <Switch checked={mappingProfile.survey_require_gps} onCheckedChange={v => setMappingProfile(p => ({ ...p, survey_require_gps: v }))} />
                    </label>
                    <label className="flex items-center justify-between gap-3 rounded-md border bg-background/60 p-3 text-sm sm:border-0 sm:bg-transparent sm:p-0">
                        <span>
                          <span className="font-medium block">需要有效分光</span>
                          <span className="text-xs text-muted-foreground">分光状态无效时只记录跳过原因。</span>
                        </span>
                        <Switch checked={mappingProfile.survey_require_valid_spectrometer} onCheckedChange={v => setMappingProfile(p => ({ ...p, survey_require_valid_spectrometer: v }))} />
                    </label>
                </div>
                <div className="rounded-md bg-muted/50 p-3 text-xs text-muted-foreground">
                  门控发生在采样启动前；跳过只发布原因，不结束走航任务。
                </div>
                <div className="flex flex-col gap-2 sm:flex-row">
                    <Button className="flex-1" onClick={saveMappingProfile}><Save className="w-4 h-4 mr-2" /> 保存门控</Button>
                    <Button variant="outline" className="w-full sm:w-11" onClick={fetchPollutionConfig}><RefreshCw className="w-4 h-4" /></Button>
                </div>
            </CardContent>
        </Card>

        <Card className="xl:col-start-2 xl:row-start-3">
            <CardHeader>
                <CardTitle className="flex items-center gap-2">
                    <Target className="w-5 h-5 text-blue-500" />
                    零点校准
                </CardTitle>
            </CardHeader>
            <CardContent className="space-y-6">
                <div className="mb-2 hidden grid-cols-4 gap-4 text-center text-sm font-medium text-muted-foreground sm:grid">
                    <div>轴</div>
                    <div>原始角度</div>
                    <div>校准角度</div>
                    <div>操作</div>
                </div>

                {(['X', 'Y', 'Z', 'A'] as const).map((axis) => (
                    <div key={axis} className="grid grid-cols-[3rem_minmax(0,1fr)_minmax(0,1fr)] gap-3 rounded-md border bg-background/70 p-3 text-sm sm:grid-cols-4 sm:items-center sm:border-0 sm:bg-transparent sm:p-0">
                        <div className="self-stretch rounded bg-muted/30 py-2 text-center font-bold sm:self-auto">{axis}</div>
                        <div className="min-w-0 text-center font-mono text-muted-foreground">
                            <span className="block text-[11px] font-sans text-muted-foreground sm:hidden">原始</span>
                            {rawAngles[axis]?.toFixed(2)}°
                        </div>
                        <div className="min-w-0 text-center font-mono font-bold text-primary">
                            <span className="block text-[11px] font-sans font-normal text-muted-foreground sm:hidden">校准</span>
                            {pumpAngles[axis]?.toFixed(2)}°
                        </div>
                        <div className="col-span-3 flex gap-2 sm:col-span-1 sm:justify-center">
                            <Button size="icon" variant="outline" className="h-11 w-full sm:w-12" onClick={() => moveToZero(axis)} disabled={homing !== null} title="回到校准零点">
                                <RotateCcw className="w-4 h-4 sm:w-3 sm:h-3" />
                            </Button>
                            <Button size="icon" variant="outline" className="h-11 w-full sm:w-12" onClick={() => setZero(axis)} title="设为零点">
                                <Target className="w-4 h-4 sm:w-3 sm:h-3" />
                            </Button>
                            <Button size="icon" variant="ghost" className="h-11 w-full text-muted-foreground sm:w-12" onClick={() => resetZero(axis)} title="重置">
                                <RotateCcw className="w-4 h-4 sm:w-3 sm:h-3" />
                            </Button>
                        </div>
                    </div>
                ))}

                <div className="flex flex-col gap-2 border-t pt-4 sm:flex-row">
                     <Button variant="default" className="flex-1" onClick={() => moveToZero()} disabled={homing !== null}>
                       <RotateCcw className="w-4 h-4 mr-2" />
                       {homing === 'XYZA' ? '回到校准零点中…' : '全部回到校准零点'}
                     </Button>
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
        <CardContent className="space-y-5 sm:space-y-6">
          <div className="mb-2 flex flex-col gap-2 sm:flex-row sm:items-center">
            <Button variant="outline" size="sm" className="w-full sm:w-auto" onClick={refreshDevices}><RefreshCw className="w-4 h-4 mr-1" />刷新设备</Button>
          </div>

          {/* 泵控板串口 */}
          <div className="space-y-3">
            <h4 className="font-medium text-sm">泵控板串口</h4>
            <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 sm:gap-4 xl:grid-cols-3">
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
            <Button variant="outline" size="sm" className="w-full sm:w-auto" onClick={testPumpPort}><Activity className="w-4 h-4 mr-1" />测试泵控连接</Button>
          </div>

          <div className="space-y-3">
            <h4 className="font-medium text-sm">ADS122C04 分光参数</h4>
            <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 sm:gap-4 xl:grid-cols-3">
              <div className="space-y-2"><Label>ADS 地址</Label><Input value={hw.ads_address} onChange={e => setHw(p => ({ ...p, ads_address: e.target.value }))} /></div>
              <div className="space-y-2"><Label>TCA 分光通道</Label><Input type="number" min="0" max="7" value={hw.spectro_channel} onChange={e => setHw(p => ({ ...p, spectro_channel: parseInt(e.target.value) || 0 }))} /></div>
              <div className="space-y-2"><Label>AIN 通道</Label><select className="flex h-10 w-full rounded-md border border-input bg-background px-3 py-2 text-sm" value={hw.mux} onChange={e => setHw(p => ({ ...p, mux: e.target.value }))}><option value="AIN0">AIN0</option><option value="AIN1">AIN1</option><option value="AIN2">AIN2</option><option value="AIN3">AIN3</option></select></div>
              <div className="space-y-2"><Label>增益</Label><select className="flex h-10 w-full rounded-md border border-input bg-background px-3 py-2 text-sm" value={hw.gain} onChange={e => setHw(p => ({ ...p, gain: parseInt(e.target.value) || 1 }))}><option value="1">1</option><option value="2">2</option><option value="4">4</option><option value="8">8</option><option value="16">16</option><option value="32">32</option><option value="64">64</option><option value="128">128</option></select></div>
              <div className="space-y-2"><Label>参考电压</Label><select className="flex h-10 w-full rounded-md border border-input bg-background px-3 py-2 text-sm" value={hw.vref_mode} onChange={e => setHw(p => ({ ...p, vref_mode: e.target.value }))}><option value="AVDD">AVDD</option><option value="INTERNAL">INTERNAL</option><option value="EXTERNAL">EXTERNAL</option></select></div>
              <div className="space-y-2"><Label>ADC 速率</Label><Input type="number" value={hw.adc_rate} onChange={e => setHw(p => ({ ...p, adc_rate: parseInt(e.target.value) || 90 }))} /></div>
              <div className="space-y-2"><Label>发布频率</Label><Input type="number" value={hw.publish_rate} onChange={e => setHw(p => ({ ...p, publish_rate: parseInt(e.target.value) || 20 }))} /></div>
              <div className="space-y-2"><Label>基线参考电压</Label><Input type="number" step="0.001" value={hw.reference_voltage} readOnly /></div>
              <div className="space-y-2"><Label>基线电压</Label><Input type="number" step="0.001" value={hw.baseline_voltage} onChange={e => setHw(p => ({ ...p, baseline_voltage: parseFloat(e.target.value) || 0 }))} /></div>
            </div>
            <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 sm:gap-4">
              <label className="flex items-center justify-between gap-3 rounded-md border bg-background/60 p-3 text-sm"><span>连续采样模式</span><Switch checked={hw.continuous_mode} onCheckedChange={v => setHw(p => ({ ...p, continuous_mode: v }))} /></label>
              <label className="flex items-center justify-between gap-3 rounded-md border bg-background/60 p-3 text-sm"><span>启动后自动开启分光</span><Switch checked={hw.auto_start} onCheckedChange={v => setHw(p => ({ ...p, auto_start: v }))} /></label>
            </div>
          </div>

          <div className="space-y-3">
            <h4 className="font-medium text-sm">角度 / 分光 TCA 通道映射</h4>
            <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 sm:gap-4 xl:grid-cols-5">
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
              "break-words rounded p-3 text-sm",
              hwMsg.includes('失败') || hwMsg.includes('error') || hwMsg.includes('Error')
                ? "bg-red-500/10 text-red-700 dark:text-red-400"
                : "bg-emerald-500/10 text-emerald-700 dark:text-emerald-400"
            )}>{hwMsg}</div>
          )}

          <div className="flex flex-col gap-2 sm:flex-row">
            <Button onClick={saveHardwareOnly} variant="outline" className="w-full sm:w-auto"><Save className="w-4 h-4 mr-1" />仅保存</Button>
            <Button onClick={saveAndApplyHardware} disabled={hwLoading} className="w-full sm:w-auto">
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

      {/* 系统日志 */}
      <SystemLogViewer />
    </div>
  )
}

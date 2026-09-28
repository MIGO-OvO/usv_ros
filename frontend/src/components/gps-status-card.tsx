import { useEffect, useState } from 'react'
import { Circle } from 'lucide-react'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'

type Maybe = number | null
interface Position {
  available: boolean; latitude: Maybe; longitude: Maybe; altitude: Maybe
  age_s: Maybe; received_at: Maybe; stale?: boolean
}
interface Diagnostics {
  overall: { state: string; severity: string; message: string }
  fcu: { heartbeat_valid: boolean; system_id: Maybe; component_id: Maybe; heartbeat_age_s: Maybe
    heartbeat_received_at: Maybe; mavros_connected: boolean | null; mavros_age_s: Maybe }
  gps_config: { gps1_type: Maybe; auto_config: Maybe; serial_protocol: Maybe; fresh: boolean }
  gps_raw: Position & { valid: boolean; fix_type: Maybe; fix_label: string; satellites: Maybe
    hdop: Maybe; vdop: Maybe; horizontal_accuracy_m: Maybe; vertical_accuracy_m: Maybe }
  global_position: Position
  navsat: Position & { status: Maybe; valid_navsat_fix: boolean }
  sampling_position: { valid: boolean; reason: string | null }
  autopilot_version: { firmware_version?: string | null; git_hash?: string | null }
  last_valid_gps_at: Maybe
  statustext: { text: string; received_at: number }[]
  warnings: string[]
}
const num = (value: Maybe | undefined, digits = 1) =>
  typeof value === 'number' && Number.isFinite(value) ? value.toFixed(digits) : '—'
const date = (value: Maybe | undefined) => value ? new Date(value * 1000).toLocaleString() : 'Unavailable'
const link = (value: boolean | null | undefined) => value === true ? '正常' : value === false ? '异常' : '未知 / 已过期'
const colors: Record<string, string> = {
  success: 'text-emerald-700 dark:text-emerald-400', warning: 'text-amber-700 dark:text-amber-400',
  orange: 'text-orange-700 dark:text-orange-400', error: 'text-red-700 dark:text-red-400', neutral: 'text-muted-foreground',
}
function Coordinates({ title, position }: { title: string; position?: Position }) {
  return <section className="min-w-0 space-y-2">
    <h3 className="font-semibold">{title}</h3>
    <dl className="grid grid-cols-[auto_minmax(0,1fr)] gap-x-4 gap-y-1 tabular-nums">
      <dt>纬度</dt><dd className="break-all">{num(position?.latitude, 7)}</dd>
      <dt>经度</dt><dd className="break-all">{num(position?.longitude, 7)}</dd>
      <dt>高度</dt><dd>{num(position?.altitude, 2)} m</dd>
      <dt>数据 age</dt><dd>{num(position?.age_s)} s</dd>
    </dl>
  </section>
}

export function GpsStatusCard() {
  const [gps, setGps] = useState<Diagnostics | null>(null)
  const [failed, setFailed] = useState(false)
  useEffect(() => {
    let disposed = false
    let timer: ReturnType<typeof setTimeout>
    let controller: AbortController
    const poll = async () => {
      controller = new AbortController()
      const timeout = setTimeout(() => controller.abort(), 4000)
      try {
        const response = await fetch('/api/gps/diagnostics', { signal: controller.signal, cache: 'no-store' })
        if (!response.ok) throw new Error('GPS diagnostics unavailable')
        const data: Diagnostics = await response.json()
        if (!disposed) { setGps(data); setFailed(false) }
      } catch {
        if (!disposed) { setGps(null); setFailed(true) }
      } finally {
        clearTimeout(timeout)
        if (!disposed) timer = setTimeout(poll, 1000)
      }
    }
    void poll()
    return () => { disposed = true; controller?.abort(); clearTimeout(timer) }
  }, [])
  const raw = gps?.gps_raw
  const details: [string, string | number][] = [
    ['FCU sysid / compid（预期 1/1）', `${gps?.fcu.system_id ?? '—'} / ${gps?.fcu.component_id ?? '—'}`],
    ['Heartbeat age', `${num(gps?.fcu.heartbeat_age_s)} s`],
    ['MAVROS 状态 age', `${num(gps?.fcu.mavros_age_s)} s`],
    ['GPS1_TYPE', `${gps?.gps_config.gps1_type ?? 'Unavailable'}${gps?.gps_config.fresh ? '' : '（未知或已过期）'}`],
    ['GPS_AUTO_CONFIG', gps?.gps_config.auto_config ?? 'Unavailable'],
    ['SERIAL3_PROTOCOL', gps?.gps_config.serial_protocol ?? 'Unavailable'],
    ['GPS driver', 'Unavailable（日志仅作线索）'],
    ['Fix type / VDOP', `${raw?.fix_type ?? '—'} / ${num(raw?.vdop)}`],
    ['水平 / 垂直精度', `${num(raw?.horizontal_accuracy_m)} / ${num(raw?.vertical_accuracy_m)} m`],
    ['最近有效 GPS', date(gps?.last_valid_gps_at)],
    ['最近 GPS_RAW_INT', date(raw?.received_at)],
    ['最近 GLOBAL_POSITION_INT', date(gps?.global_position.received_at)],
    ['最近 FCU heartbeat', date(gps?.fcu.heartbeat_received_at)],
    ['Firmware', gps?.autopilot_version.firmware_version || 'Unavailable'],
    ['Git hash', gps?.autopilot_version.git_hash || 'Unavailable'],
  ]
  return <Card className="min-w-0 shadow-none">
    <CardHeader><CardTitle>GPS / GNSS 诊断</CardTitle></CardHeader>
    <CardContent className="space-y-5 text-sm">
      <div>
        <p role="status" className={`flex items-start gap-2 text-xl font-semibold ${colors[gps?.overall.severity || 'neutral']}`}>
          <Circle aria-hidden="true" className="mt-1 h-4 w-4 shrink-0 fill-current" />
          {failed ? '诊断连接失败' : gps?.overall.message || '正在读取诊断'}
        </p>
        <p className="mt-2 flex flex-wrap gap-x-4 gap-y-1 tabular-nums">
          <strong>SAT {num(raw?.satellites, 0)}</strong><span>HDOP {num(raw?.hdop)}</span>
          <span>{num(raw?.age_s)} s ago</span><span>Fix: {raw?.fix_label || 'Unknown'}</span>
        </p>
      </div>
      <div className="flex flex-wrap gap-x-4 gap-y-2 border-y py-3">
        <span>FCU：{link(gps?.fcu.heartbeat_valid)}</span>
        <span>MAVROS：{link(gps?.fcu.mavros_connected)}</span>
        <span>GPS RAW：{!raw?.available ? '未收到 GPS_RAW_INT' : raw.stale ? '已过期' : '正在接收'}</span>
      </div>
      {gps?.warnings.map((warning) => <p key={warning} className="text-orange-700 dark:text-orange-400">{warning}</p>)}
      <div className="grid min-w-0 grid-cols-1 gap-5 sm:grid-cols-2">
        <Coordinates title="原始 GNSS · GPS_RAW_INT" position={raw} />
        <Coordinates title="融合位置 · GLOBAL_POSITION_INT" position={gps?.global_position} />
      </div>
      <p className="text-muted-foreground">融合/全局位置不代表原始 GNSS 已获得 Fix。高度基准可能不同，不用于直接比较。</p>
      <p className="font-medium">采样坐标：{!gps ? '未知' : gps.sampling_position.valid ? '有效（严格 NavSatFix 校验）' : `无效 · ${gps.sampling_position.reason}`}</p>
      <details className="border-t pt-3">
        <summary className="cursor-pointer rounded-sm py-2 font-medium focus-visible:outline focus-visible:outline-2 focus-visible:outline-ring">详细诊断</summary>
        <div className="mt-3 space-y-4 break-words">
          <Coordinates title="MAVROS · /global_position/global" position={gps?.navsat} />
          <p>NavSatFix status：{gps?.navsat.status ?? 'Unavailable'} · 与原始 GNSS 独立判定</p>
          <dl className="space-y-2 tabular-nums">{details.map(([label, value]) =>
            <div key={label}><dt className="text-muted-foreground">{label}</dt><dd className="break-all">{value}</dd></div>)}</dl>
          <section><h3 className="font-semibold">最近 GPS 相关 STATUSTEXT</h3>
            {gps?.statustext.length ? <ul className="mt-2 space-y-2">{gps.statustext.map((entry, index) =>
              <li key={`${entry.received_at}-${index}`}><time>{date(entry.received_at)}</time> · {entry.text}</li>)}</ul> : <p>暂无日志</p>}
          </section>
          <p className="text-muted-foreground">此面板只读。缺少原始帧可能是接收机、配置或消息流问题；无法仅凭软件判断天线或硬件损坏。</p>
        </div>
      </details>
    </CardContent>
  </Card>
}

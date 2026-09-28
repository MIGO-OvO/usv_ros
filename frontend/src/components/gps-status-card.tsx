import { Circle } from 'lucide-react'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { summarizeGps } from '@/lib/monitor-gps'

type Maybe = number | null
interface Position {
  available: boolean; latitude: Maybe; longitude: Maybe; altitude: Maybe
  age_s: Maybe; received_at: Maybe; stale?: boolean
}
export interface Diagnostics {
  freshness_threshold_s?: number
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


const reasonText: Record<string, string> = {
  gps_missing: '无数据',
  gps_no_fix: '无 Fix',
  gps_stale: '数据过期',
  gps_invalid_coordinates: '坐标非法',
  gps_missing_receive_time: '缺少接收时间',
}

/** Three independent layers: raw GNSS telemetry, MAVROS fix, sampling admission. */
function layerStatus(gps: Diagnostics | null) {
  const raw = gps?.gps_raw
  const navsat = gps?.navsat
  const limit = Math.max(10, gps?.freshness_threshold_s ?? 2)
  const ageText = (value: Maybe | undefined) =>
    typeof value === 'number' && Number.isFinite(value) ? `${value.toFixed(1)}s` : '—'
  const receiver = !raw?.available ? '未收到 GPS_RAW_INT'
    : raw.stale ? '原始 GNSS 遥测陈旧'
      : `${raw.fix_label || 'Unknown'} · ${num(raw.satellites, 0)} SAT · HDOP ${num(raw.hdop)}`
  const mavros = !navsat?.available ? '未收到 NavSatFix'
    : (navsat.age_s ?? Infinity) > limit ? `NavSatFix 陈旧 · ${ageText(navsat.age_s)}`
      : `正常 · ${ageText(navsat.age_s)}`
  const sampling = !gps ? '未知'
    : gps.sampling_position.valid ? `可用于采样 · ${ageText(navsat?.age_s)}`
      : `不可用 · ${reasonText[gps.sampling_position.reason ?? ''] ?? gps.sampling_position.reason}`
  return { receiver, mavros, sampling }
}

export function GpsStatusCard({ gps, failed, detailed = false }: {
  gps: Diagnostics | null; failed: boolean; detailed?: boolean
}) {
  const raw = gps?.gps_raw
  const summary = summarizeGps(gps)
  const layer = layerStatus(gps)
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
    <CardHeader className="p-4 pb-2"><CardTitle className="text-base">GPS / GNSS {detailed ? '详细诊断' : ''}</CardTitle></CardHeader>
    <CardContent className="space-y-2 p-4 pt-0 text-sm">
      <div>
        <p role="status" className={`flex items-start gap-2 text-lg font-semibold ${colors[summary.tone]}`}>
          <Circle aria-hidden="true" className="mt-1 h-4 w-4 shrink-0 fill-current" />
          {failed ? '定位未知 · 诊断连接失败' : summary.label}
        </p>
        {detailed && <p className="mt-2 flex flex-wrap gap-x-4 gap-y-1 tabular-nums">
          <strong>SAT {num(raw?.satellites, 0)}</strong><span>HDOP {num(raw?.hdop)}</span>
          <span>{num(raw?.age_s)} s ago</span><span>Fix: {raw?.fix_label || 'Unknown'}</span>
        </p>}
        {!detailed && <>
          <div className="mt-2 flex flex-wrap gap-x-6 gap-y-2 tabular-nums">
            <span>纬度 {num(summary.position?.latitude, 7)}°</span>
            <span>经度 {num(summary.position?.longitude, 7)}°</span>
            <span>高度 {num(summary.position?.altitude)} m</span>
            <span>数据年龄 {num(summary.position?.age_s)} s</span>
          </div>
          <p className="mt-2 break-words text-xs text-muted-foreground">显示坐标来源：{summary.source}</p>
        </>}
      </div>
      <div className="flex flex-wrap gap-x-4 gap-y-2 border-y py-2 text-xs">
        <span>FCU：{link(gps?.fcu.heartbeat_valid)}</span>
        <span>MAVROS：{link(gps?.fcu.mavros_connected)}</span>
        <span className="break-all">GLOBAL_POSITION_INT：{!gps?.global_position.available ? '未收到' : gps.global_position.stale ? '数据较旧' : '正常'}</span>
        <span>GPS RAW：{!raw?.available ? '未收到 GPS_RAW_INT' : raw.stale ? '原始 GNSS 遥测陈旧' : '正在接收'}</span>
      </div>
      <div className="grid gap-x-6 gap-y-1 border-y py-2 text-xs sm:grid-cols-[auto_minmax(0,1fr)]" role="list">
        <span className="font-medium" role="listitem">GPS Receiver</span>
        <span role="listitem" className={layer.receiver.includes('陈旧') || layer.receiver.includes('未收到') ? colors.orange : undefined}>{layer.receiver}</span>
        <span className="font-medium" role="listitem">MAVROS Position</span>
        <span role="listitem" className={layer.mavros.includes('陈旧') || layer.mavros.includes('未收到') ? colors.orange : undefined}>{layer.mavros}</span>
        <span className="font-medium" role="listitem">Sampling GPS</span>
        <span role="listitem" className={layer.sampling.includes('不可用') ? colors.error : colors.success}>{layer.sampling}</span>
      </div>
      {detailed && gps?.warnings.map((warning) => <p key={warning} className="text-orange-700 dark:text-orange-400">{warning}</p>)}
      {detailed && <>
      <div className="grid min-w-0 grid-cols-1 gap-5 sm:grid-cols-2">
        <Coordinates title="原始 GNSS · GPS_RAW_INT" position={raw} />
        <Coordinates title="融合位置 · GLOBAL_POSITION_INT" position={gps?.global_position} />
      </div>
      <p className="text-muted-foreground">融合/全局位置不代表原始 GNSS 已获得 Fix。高度基准可能不同，不用于直接比较。</p>
      <p>{gps?.overall.message}</p>
      </>}
      <p className="text-xs font-medium">采样准入：{!gps ? '未知' : gps.sampling_position.valid ? '有效（严格 NavSatFix 校验）' : `未通过 · ${gps.sampling_position.reason}`}</p>
      <p className="text-xs text-muted-foreground">采样坐标源：MAVROS /global_position/global（后端严格 NavSatFix 校验，不以显示坐标替代）</p>
      {!detailed && <a className="inline-block py-2 text-blue-700 underline dark:text-blue-400" href="#gps-diagnostics" onClick={() => { const panel = document.getElementById('gps-diagnostics'); if (panel instanceof HTMLDetailsElement) panel.open = true }}>详细诊断</a>}
      {detailed && <div className="border-t pt-3">
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
          <p className="text-muted-foreground">显示层：超过后端新鲜度阈值的坐标标记为降级；超过 {Math.max(10, gps?.freshness_threshold_s ?? 2)} 秒不再用作主坐标。采样准入始终以服务器严格校验为准。</p>
        </div>
      </div>}
    </CardContent>
  </Card>
}

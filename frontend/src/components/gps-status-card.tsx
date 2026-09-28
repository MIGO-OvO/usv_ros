import { useEffect, useState } from 'react'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'

interface GpsStatus {
  valid: boolean
  reason: string | null
  latitude: number | null
  longitude: number | null
  altitude: number | null
  position_age_s: number | null
}

const reasons: Record<string, string> = {
  gps_missing: '未收到 GPS', gps_no_fix: '未定位', gps_stale: '定位已过期',
  gps_invalid_coordinates: '坐标无效', gps_missing_timestamp: '定位时间无效',
}
const numberText = (value: number | null | undefined, digits: number) =>
  typeof value === 'number' && Number.isFinite(value) ? value.toFixed(digits) : '—'

export function GpsStatusCard() {
  const [gps, setGps] = useState<GpsStatus | null>(null)
  const [failed, setFailed] = useState(false)
  useEffect(() => {
    let disposed = false
    let timer: ReturnType<typeof setTimeout>
    let controller: AbortController
    const poll = async () => {
      controller = new AbortController()
      const timeout = setTimeout(() => controller.abort(), 4000)
      try {
        const response = await fetch('/api/gps', { signal: controller.signal, cache: 'no-store' })
        if (!response.ok) throw new Error('GPS status unavailable')
        const data: GpsStatus = await response.json()
        if (!disposed) { setGps(data); setFailed(false) }
      } catch {
        if (!disposed) { setGps(null); setFailed(true) }
      } finally {
        clearTimeout(timeout)
        if (!disposed) timer = setTimeout(poll, 1000)
      }
    }
    void poll()
    return () => { disposed = true; controller.abort(); clearTimeout(timer) }
  }, [])
  const status = failed ? '状态获取失败' : !gps ? '正在获取定位' : gps.valid ? '定位有效' : reasons[gps.reason || ''] || '定位不可用'
  return (
    <Card>
      <CardHeader><CardTitle>GPS 定位</CardTitle></CardHeader>
      <CardContent className="space-y-3 text-sm">
        <p role="status" className="font-medium">{status}</p>
        <dl className="grid grid-cols-2 gap-x-3 gap-y-2 tabular-nums">
          <dt className="text-muted-foreground">纬度 · WGS84</dt><dd>{numberText(gps?.latitude, 6)}</dd>
          <dt className="text-muted-foreground">经度 · WGS84</dt><dd>{numberText(gps?.longitude, 6)}</dd>
          <dt className="text-muted-foreground">海拔 (m)</dt><dd>{numberText(gps?.altitude, 2)}</dd>
          <dt className="text-muted-foreground">数据年龄 (s)</dt><dd>{numberText(gps?.position_age_s, 1)}</dd>
        </dl>
        <p className="text-xs text-muted-foreground">硬件 GPS，不使用模拟船位。失效时坐标仅供诊断，不代表当前位置。</p>
      </CardContent>
    </Card>
  )
}

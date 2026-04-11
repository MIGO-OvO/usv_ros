import { useState, useEffect } from 'react'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { useAppStore } from '@/store'
import { cn } from '@/lib/utils'
import { Radio, ChevronDown, ChevronRight, Download } from 'lucide-react'

interface LinkDiagData {
  mavros: { connected: boolean; armed: boolean; mode: string }
  bridge: Record<string, unknown>
  nodes: { name: string; alive: boolean }[]
  link_events_recent: { ts: string; type: string; detail: string }[]
}

export function LinkDiagnosticsCard() {
  const { mavrosState, bridgeDiag, radioStatus, connected } = useAppStore()
  const [expanded, setExpanded] = useState(false)
  const [fullDiag, setFullDiag] = useState<LinkDiagData | null>(null)

  useEffect(() => {
    if (!connected || !expanded) return
    const load = () => {
      fetch('/api/diagnostics/link')
        .then(r => r.json())
        .then(r => { if (r.success) setFullDiag(r.data) })
        .catch(() => {})
    }
    load()
    const t = setInterval(load, 5000)
    return () => clearInterval(t)
  }, [connected, expanded])

  const bridge = bridgeDiag as any
  const mavConnected = mavrosState?.connected ?? false
  const routerAlive = fullDiag?.nodes?.find(n => n.name === 'mavlink-routerd')?.alive ?? false

  return (
    <Card className="bg-card/50 backdrop-blur-sm">
      <CardHeader
        className="flex flex-row items-center justify-between space-y-0 pb-2 cursor-pointer select-none"
        onClick={() => setExpanded(v => !v)}
      >
        <CardTitle className="text-sm font-medium flex items-center gap-2">
          <Radio className="h-4 w-4" />
          通信链路诊断
        </CardTitle>
        <div className="flex items-center gap-2">
          <div className={cn(
            "h-2.5 w-2.5 rounded-full",
            mavConnected && routerAlive ? "bg-emerald-500" : (routerAlive ? "bg-yellow-500" : "bg-red-500")
          )} />
          <span className="text-xs text-muted-foreground">
            {mavConnected && routerAlive ? "链路正常" : (routerAlive ? "MAVROS 断开" : "Router 离线")}
          </span>
          {expanded ? <ChevronDown className="h-4 w-4 text-muted-foreground" /> : <ChevronRight className="h-4 w-4 text-muted-foreground" />}
        </div>
      </CardHeader>

      {expanded && (
        <CardContent className="space-y-3 text-sm">
          {/* MAVROS 状态 */}
          <div className="grid grid-cols-3 gap-2">
            <KV label="模式" value={mavrosState?.mode || '--'} />
            <KV label="解锁" value={mavrosState?.armed ? '是' : '否'} warn={mavrosState?.armed} />
            <KV label="连接" value={mavConnected ? '正常' : '断开'} ok={mavConnected} err={!mavConnected} />
          </div>

          {/* 桥接节点统计 */}
          {bridge ? (
            <div>
              <h4 className="text-xs font-medium text-muted-foreground mb-1">MAVLink 桥接 ({bridge.router_url})</h4>
              <div className="grid grid-cols-3 gap-2">
                <KV label="SysID" value={String(bridge.sysid)} />
                <KV label="CompID" value={String(bridge.compid)} />
                <KV label="速率" value={`${bridge.rate_hz}Hz`} />
                <KV label="TX 总计" value={String(bridge.tx_total)} />
                <KV label="Named" value={String(bridge.tx_named_value)} />
                <KV label="心跳" value={String(bridge.tx_heartbeat)} />
                <KV label="发布错误" value={String(bridge.pub_errors)} err={bridge.pub_errors > 0} />
                <KV label="掉线" value={String(bridge.mavros_drops)} warn={bridge.mavros_drops > 0} />
                <KV label="运行" value={`${bridge.uptime_s}s`} />
              </div>
            </div>
          ) : (
            <p className="text-xs text-muted-foreground">等待桥接节点数据…</p>
          )}

          {/* ROS 节点状态 */}
          {fullDiag?.nodes && fullDiag.nodes.length > 0 && (
            <div>
              <h4 className="text-xs font-medium text-muted-foreground mb-1">ROS 节点</h4>
              <div className="flex flex-wrap gap-1.5">
                {fullDiag.nodes.map(n => (
                  <span key={n.name} className={cn(
                    "px-2 py-0.5 rounded text-xs border",
                    n.alive
                      ? "border-emerald-500/30 text-emerald-500"
                      : "border-red-500/30 text-red-500"
                  )}>
                    {n.alive ? '●' : '✕'} {n.name.replace('/', '')}
                  </span>
                ))}
              </div>
            </div>
          )}

          {/* 电台链路质量 */}
          {radioStatus && (
            <div>
              <h4 className="text-xs font-medium text-muted-foreground mb-1">数传电台</h4>
              <div className="grid grid-cols-3 gap-2">
                <RssiBar label="本地 RSSI" value={radioStatus.rssi} noise={radioStatus.noise} />
                <RssiBar label="远端 RSSI" value={radioStatus.remrssi} noise={radioStatus.remnoise} />
                <KV label="TX 缓冲" value={`${radioStatus.txbuf}%`} warn={radioStatus.txbuf < 30} err={radioStatus.txbuf < 10} />
                <KV label="RX 错误" value={String(radioStatus.rxerrors)} err={radioStatus.rxerrors > 0} />
                <KV label="已纠错" value={String(radioStatus.fixed)} />
                <KV label="噪声底" value={`${radioStatus.noise}/${radioStatus.remnoise}`} />
              </div>
            </div>
          )}

          {/* 导出按钮 */}
          <button
            className="flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground transition-colors"
            onClick={e => { e.stopPropagation(); window.open('/api/diagnostics/export', '_blank') }}
          >
            <Download className="h-3 w-3" /> 导出诊断报告
          </button>
        </CardContent>
      )}
    </Card>
  )
}

function KV({ label, value, ok, warn, err }: {
  label: string; value: string; ok?: boolean; warn?: boolean; err?: boolean
}) {
  return (
    <div className="flex justify-between items-center">
      <span className="text-muted-foreground">{label}</span>
      <span className={cn(
        "font-medium tabular-nums",
        ok && "text-emerald-500",
        warn && "text-orange-500",
        err && "text-red-500",
      )}>{value}</span>
    </div>
  )
}

function RssiBar({ label, value, noise }: { label: string; value: number; noise: number }) {
  // RSSI 0-254, signal margin = rssi - noise
  const margin = value - noise
  const pct = Math.min(100, Math.max(0, (margin / 60) * 100))
  const color = margin > 25 ? 'bg-emerald-500' : margin > 10 ? 'bg-orange-500' : 'bg-red-500'
  return (
    <div className="flex flex-col gap-0.5">
      <div className="flex justify-between text-muted-foreground">
        <span>{label}</span>
        <span className="font-medium tabular-nums">{value}</span>
      </div>
      <div className="h-1.5 rounded-full bg-muted overflow-hidden">
        <div className={cn("h-full rounded-full transition-all", color)} style={{ width: `${pct}%` }} />
      </div>
    </div>
  )
}

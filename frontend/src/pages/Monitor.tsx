import { useEffect, useState, useRef } from 'react'
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { useAppStore } from '@/store'
import { LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer } from 'recharts'
import { Activity, Zap, Play, Square } from 'lucide-react'
import { cn } from '@/lib/utils'
import { ModeToggle } from '@/components/mode-toggle'

// Offset constants
const OFFSETS = { X: 0, Y: 2, Z: 4, A: 6 }
const COLORS = { X: "#ef4444", Y: "#3b82f6", Z: "#10b981", A: "#f59e0b" }

export default function Monitor() {
  const { socket, connected, pumpConnected, automationRunning, missionStatus, pumpAngles, currentVoltage } = useAppStore()
  const [pidData, setPidData] = useState<any[]>([])
  const lastValues = useRef({ X: 0, Y: 0, Z: 0, A: 0 })

  useEffect(() => {
    if (!socket) return

    const handlePidError = (data: any) => {
        // data: { motor: 'X', error: 0.1, timestamp: ... }
        const motor = data.motor as keyof typeof OFFSETS
        const error = data.error || 0
        
        lastValues.current[motor] = error

        const timestamp = new Date().toLocaleTimeString()
        
        setPidData(prev => {
            const newData = [...prev, {
                time: timestamp,
                X: lastValues.current.X + OFFSETS.X,
                Y: lastValues.current.Y + OFFSETS.Y,
                Z: lastValues.current.Z + OFFSETS.Z,
                A: lastValues.current.A + OFFSETS.A,
                rawX: lastValues.current.X,
                rawY: lastValues.current.Y,
                rawZ: lastValues.current.Z,
                rawA: lastValues.current.A,
            }]
            return newData.slice(-100) // Keep last 100 points
        })
    }

    socket.on('pid_error', handlePidError)
    return () => {
        socket.off('pid_error', handlePidError)
    }
  }, [socket])

  return (
    <div className="p-4 md:p-8 space-y-6 max-w-7xl mx-auto">
      <header className="flex flex-col md:flex-row md:items-center justify-between gap-4">
        <div>
            <h1 className="text-3xl font-bold tracking-tight">系统监控</h1>
            <p className="text-muted-foreground">实时遥测数据与系统状态</p>
        </div>
        <div className="flex items-center gap-3">
             <ModeToggle />
             <div className={cn("px-3 py-1 rounded-full text-xs font-medium border flex items-center gap-2", 
                connected ? "bg-emerald-500/10 text-emerald-500 border-emerald-500/20" : "bg-red-500/10 text-red-500 border-red-500/20")}>
                <div className={cn("w-2 h-2 rounded-full", connected ? "bg-emerald-500" : "bg-red-500")} />
                {connected ? "已连接" : "未连接"}
             </div>
             <div className={cn("px-3 py-1 rounded-full text-xs font-medium border flex items-center gap-2", 
                pumpConnected ? "bg-blue-500/10 text-blue-500 border-blue-500/20" : "bg-orange-500/10 text-orange-500 border-orange-500/20")}>
                <Zap className="w-3 h-3" />
                {pumpConnected ? "泵组在线" : "泵组离线"}
             </div>
        </div>
      </header>

      {/* Status Cards */}
      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4">
        <Card className="bg-card/50 backdrop-blur-sm">
            <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
                <CardTitle className="text-sm font-medium">分光计电压</CardTitle>
                <Activity className="h-4 w-4 text-muted-foreground" />
            </CardHeader>
            <CardContent>
                <div className="text-2xl font-bold">{currentVoltage.toFixed(3)} V</div>
                <p className="text-xs text-muted-foreground">实时采样电压</p>
            </CardContent>
        </Card>
        
        <Card className="bg-card/50 backdrop-blur-sm">
            <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
                <CardTitle className="text-sm font-medium">任务状态</CardTitle>
                {automationRunning ? <Play className="h-4 w-4 text-emerald-500" /> : <Square className="h-4 w-4 text-muted-foreground" />}
            </CardHeader>
            <CardContent>
                <div className="text-2xl font-bold">{missionStatus === "IDLE" ? "空闲" : missionStatus}</div>
                <p className="text-xs text-muted-foreground">{automationRunning ? "自动化运行中" : "系统待机"}</p>
            </CardContent>
        </Card>

        {/* Pump Angles Compact */}
        <Card className="bg-card/50 backdrop-blur-sm lg:col-span-2">
            <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
                 <CardTitle className="text-sm font-medium">泵组角度</CardTitle>
            </CardHeader>
            <CardContent className="grid grid-cols-4 gap-2">
                {Object.entries(pumpAngles).map(([axis, angle]) => (
                    <div key={axis} className="text-center">
                        <div className="text-xs text-muted-foreground font-bold mb-1">{axis} 轴</div>
                        <div className="text-lg font-mono">{angle.toFixed(1)}°</div>
                    </div>
                ))}
            </CardContent>
        </Card>
      </div>

      {/* PID Chart */}
      <Card className="col-span-4 h-[500px] flex flex-col">
         <CardHeader>
            <CardTitle>PID 误差追踪</CardTitle>
            <p className="text-sm text-muted-foreground">偏置可视化 (基线: X=0, Y=2, Z=4, A=6)</p>
         </CardHeader>
         <CardContent className="flex-1 min-h-0">
            <ResponsiveContainer width="100%" height="100%">
                <LineChart data={pidData}>
                    <CartesianGrid strokeDasharray="3 3" stroke="hsl(var(--border))" opacity={0.4} />
                    <XAxis dataKey="time" stroke="hsl(var(--muted-foreground))" fontSize={12} tickLine={false} axisLine={false} />
                    <YAxis stroke="hsl(var(--muted-foreground))" fontSize={12} tickLine={false} axisLine={false} domain={[-1, 8]} />
                    <Tooltip 
                        contentStyle={{ backgroundColor: 'hsl(var(--card))', borderColor: 'hsl(var(--border))', borderRadius: '8px' }}
                        formatter={(value: any, name: any, props: any) => {
                             // Show raw value in tooltip
                             const rawKey = `raw${name}`
                             const rawVal = props.payload[rawKey]
                             return [rawVal ? rawVal.toFixed(4) : (typeof value === 'number' ? value.toFixed(4) : value), `${name} 轴误差`]
                        }}
                    />
                    <Line type="monotone" dataKey="X" stroke={COLORS.X} strokeWidth={2} dot={false} isAnimationActive={false} />
                    <Line type="monotone" dataKey="Y" stroke={COLORS.Y} strokeWidth={2} dot={false} isAnimationActive={false} />
                    <Line type="monotone" dataKey="Z" stroke={COLORS.Z} strokeWidth={2} dot={false} isAnimationActive={false} />
                    <Line type="monotone" dataKey="A" stroke={COLORS.A} strokeWidth={2} dot={false} isAnimationActive={false} />
                </LineChart>
            </ResponsiveContainer>
         </CardContent>
      </Card>
    </div>
  )
}

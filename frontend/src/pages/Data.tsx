import { useEffect, useState } from 'react'
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Button } from "@/components/ui/button"
import { ScrollArea } from "@/components/ui/scroll-area"
import { Download, Trash2, RefreshCw, FileText, Calendar } from 'lucide-react'
import { LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer } from 'recharts'
import { cn } from "@/lib/utils"

interface MissionMeta {
    id: string
    name: string
    start_time: string
    end_time: string | null
    point_count: number
}

export default function Data() {
  const [missions, setMissions] = useState<MissionMeta[]>([])
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [chartData, setChartData] = useState<any[]>([])
  const [loading, setLoading] = useState(false)

  useEffect(() => {
    fetchMissions()
  }, [])

  useEffect(() => {
      if (selectedId) {
          fetchMissionData(selectedId)
      } else {
          setChartData([])
      }
  }, [selectedId])

  const fetchMissions = async () => {
    try {
        const res = await fetch('/api/data/missions')
        const json = await res.json()
        if (json.success) {
            setMissions(json.data)
            if (json.data.length > 0 && !selectedId) {
                setSelectedId(json.data[0].id)
            }
        }
    } catch (e) {
        console.error(e)
    }
  }

  const fetchMissionData = async (id: string) => {
      setLoading(true)
      try {
          const res = await fetch(`/api/data/mission/${id}`)
          const json = await res.json()
          if (json.success) {
              setChartData(json.data.data_points)
          }
      } catch (e) {
          console.error(e)
      } finally {
          setLoading(false)
      }
  }

  const deleteMission = async (e: React.MouseEvent, id: string) => {
      e.stopPropagation()
      if (!confirm("确定要删除此任务记录吗？")) return
      
      try {
          const res = await fetch(`/api/data/mission/${id}`, { method: 'DELETE' })
          const json = await res.json()
          if (json.success) {
              setMissions(prev => prev.filter(m => m.id !== id))
              if (selectedId === id) {
                  setSelectedId(null)
              }
          }
      } catch (e) {
          console.error(e)
      }
  }

  const exportMission = (e: React.MouseEvent, id: string) => {
      e.stopPropagation()
      const mission = missions.find(m => m.id === id)
      if (!mission) return

      // Since we might not have data loaded, fetch it first if needed, 
      // but simpler is to assume we export currently viewed or fetch specifically for export.
      // For now, let's export from chartData if selected, or fetch if not.
      
      const doExport = (dataPoints: any[]) => {
          const csvContent = "data:text/csv;charset=utf-8," 
              + "Timestamp,Voltage\n"
              + dataPoints.map(row => `${row.timestamp},${row.voltage}`).join("\n")
          
          const encodedUri = encodeURI(csvContent)
          const link = document.createElement("a")
          link.setAttribute("href", encodedUri)
          link.setAttribute("download", `mission_${id}.csv`)
          document.body.appendChild(link)
          link.click()
          document.body.removeChild(link)
      }

      if (selectedId === id && chartData.length > 0) {
          doExport(chartData)
      } else {
          fetch(`/api/data/mission/${id}`)
            .then(res => res.json())
            .then(json => {
                if (json.success) doExport(json.data.data_points)
            })
      }
  }

  return (
    <div className="p-4 md:p-8 space-y-6 max-w-7xl mx-auto h-[calc(100vh-6rem)] flex flex-col">
      <header className="flex items-center justify-between shrink-0">
        <div>
            <h1 className="text-3xl font-bold tracking-tight">数据中心</h1>
            <p className="text-muted-foreground">历史任务数据管理与分析。</p>
        </div>
        <div className="flex gap-2">
            <Button variant="outline" onClick={fetchMissions}><RefreshCw className="w-4 h-4 mr-2" /> 刷新列表</Button>
        </div>
      </header>

      <div className="grid grid-cols-1 md:grid-cols-3 gap-6 flex-1 min-h-0">
          {/* Mission List */}
          <Card className="md:col-span-1 flex flex-col h-full">
              <CardHeader className="pb-3">
                  <CardTitle className="text-lg">任务列表</CardTitle>
              </CardHeader>
              <CardContent className="flex-1 min-h-0 p-0">
                  <ScrollArea className="h-full">
                      <div className="flex flex-col gap-1 p-3">
                          {missions.length === 0 && (
                              <div className="text-center text-muted-foreground py-8 text-sm">暂无数据记录</div>
                          )}
                          {missions.map((mission) => (
                              <div 
                                key={mission.id}
                                onClick={() => setSelectedId(mission.id)}
                                className={cn(
                                    "flex flex-col gap-1 p-3 rounded-lg cursor-pointer transition-colors border",
                                    selectedId === mission.id 
                                        ? "bg-primary/10 border-primary/20" 
                                        : "hover:bg-muted border-transparent"
                                )}
                              >
                                  <div className="flex items-center justify-between">
                                      <span className="font-medium text-sm truncate">{mission.name}</span>
                                      <div className="flex gap-1">
                                          <Button size="icon" variant="ghost" className="h-6 w-6" onClick={(e) => exportMission(e, mission.id)} title="导出">
                                              <Download className="w-3 h-3" />
                                          </Button>
                                          <Button size="icon" variant="ghost" className="h-6 w-6 text-destructive hover:text-destructive" onClick={(e) => deleteMission(e, mission.id)} title="删除">
                                              <Trash2 className="w-3 h-3" />
                                          </Button>
                                      </div>
                                  </div>
                                  <div className="flex items-center gap-2 text-xs text-muted-foreground">
                                      <Calendar className="w-3 h-3" />
                                      <span>{new Date(mission.start_time).toLocaleString()}</span>
                                  </div>
                                  <div className="flex items-center gap-2 text-xs text-muted-foreground">
                                      <FileText className="w-3 h-3" />
                                      <span>{mission.point_count} 数据点</span>
                                  </div>
                              </div>
                          ))}
                      </div>
                  </ScrollArea>
              </CardContent>
          </Card>

          {/* Chart View */}
          <Card className="md:col-span-2 flex flex-col h-full">
              <CardHeader className="pb-2 border-b">
                  <div className="flex items-center justify-between">
                    <CardTitle>
                        {selectedId ? (missions.find(m => m.id === selectedId)?.name || "任务详情") : "请选择任务"}
                    </CardTitle>
                    {selectedId && (
                        <div className="text-xs text-muted-foreground font-mono">
                            ID: {selectedId}
                        </div>
                    )}
                  </div>
              </CardHeader>
              <CardContent className="flex-1 min-h-0 pt-4">
                 {selectedId ? (
                     loading ? (
                         <div className="h-full flex items-center justify-center text-muted-foreground">加载中...</div>
                     ) : (
                        <ResponsiveContainer width="100%" height="100%">
                            <LineChart data={chartData}>
                                <CartesianGrid strokeDasharray="3 3" opacity={0.2} />
                                <XAxis 
                                    dataKey="timestamp" 
                                    tickFormatter={(t) => new Date(t).toLocaleTimeString()} 
                                    stroke="hsl(var(--muted-foreground))"
                                    fontSize={12}
                                />
                                <YAxis 
                                    domain={[0, 5]} 
                                    stroke="hsl(var(--muted-foreground))"
                                    fontSize={12}
                                    label={{ value: '电压 (V)', angle: -90, position: 'insideLeft', style: { fill: 'hsl(var(--muted-foreground))' } }}
                                />
                                <Tooltip 
                                    contentStyle={{ backgroundColor: 'hsl(var(--card))', borderColor: 'hsl(var(--border))', borderRadius: '8px' }}
                                    labelFormatter={(t) => new Date(t).toLocaleString()} 
                                />
                                <Line 
                                    type="monotone" 
                                    dataKey="voltage" 
                                    stroke="hsl(var(--primary))" 
                                    dot={false} 
                                    strokeWidth={2} 
                                    activeDot={{ r: 4 }}
                                />
                            </LineChart>
                        </ResponsiveContainer>
                     )
                 ) : (
                     <div className="h-full flex items-center justify-center text-muted-foreground flex-col gap-2">
                         <FileText className="w-8 h-8 opacity-20" />
                         <p>从左侧列表选择一个任务以查看详情</p>
                     </div>
                 )}
              </CardContent>
          </Card>
      </div>
    </div>
  )
}

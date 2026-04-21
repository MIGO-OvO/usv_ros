import { useEffect, useState, useCallback } from 'react'
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Switch } from "@/components/ui/switch"
import { NumericInput } from "@/components/ui/numeric-input"
import { Plus, Trash2, Save, RefreshCw, Download } from 'lucide-react'
import { toast } from '@/hooks/use-toast'

interface WaypointSamplingItem {
  enabled: boolean
  loop_count: number
  retry_count: number
  hold_before_sampling_s: number
  on_fail: string
}

type WaypointSamplingMap = Record<string, WaypointSamplingItem>

const DEFAULT_ITEM: WaypointSamplingItem = {
  enabled: true,
  loop_count: 1,
  retry_count: 0,
  hold_before_sampling_s: 3.0,
  on_fail: 'HOLD',
}

export function WaypointSamplingCard() {
  const [data, setData] = useState<WaypointSamplingMap>({})
  const [newSeq, setNewSeq] = useState('')

  const fetchData = useCallback(async () => {
    try {
      const res = await fetch('/api/waypoint-sampling')
      const json = await res.json()
      if (json.success) setData(json.data || {})
    } catch (e) { console.error(e) }
  }, [])

  useEffect(() => { fetchData() }, [fetchData])

  const saveAll = async () => {
    try {
      const res = await fetch('/api/waypoint-sampling', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(data),
      })
      const json = await res.json()
      if (json.success) {
        toast({ title: '航点采样配置已保存' })
        if (json.data) setData(json.data)
      } else {
        toast({ title: '保存失败', description: json.message, variant: 'destructive' })
      }
    } catch (e) {
      toast({ title: '请求失败', variant: 'destructive' })
    }
  }

  const syncFromMavros = async () => {
    try {
      const res = await fetch('/api/waypoint-sampling/sync', { method: 'POST' })
      const json = await res.json()
      if (json.success) {
        toast({ title: '航点已同步', description: json.message })
        if (json.data) setData(json.data)
      } else {
        toast({ title: '同步失败', description: json.message, variant: 'destructive' })
      }
    } catch (e) {
      toast({ title: '同步请求失败', variant: 'destructive' })
    }
  }

  const addWaypoint = () => {
    const seq = newSeq.trim()
    if (!seq || isNaN(Number(seq))) return
    const key = String(parseInt(seq, 10))
    if (data[key]) { toast({ title: `航点 ${key} 已存在` }); return }
    setData({ ...data, [key]: { ...DEFAULT_ITEM } })
    setNewSeq('')
  }

  const removeWaypoint = (key: string) => {
    const next = { ...data }
    delete next[key]
    setData(next)
  }

  const updateField = (key: string, field: keyof WaypointSamplingItem, value: any) => {
    setData({ ...data, [key]: { ...data[key], [field]: value } })
  }

  const sortedKeys = Object.keys(data).sort((a, b) => Number(a) - Number(b))

  return (
    <Card>
      <CardHeader className="flex flex-row items-center justify-between">
        <CardTitle className="text-base">航点采样配置</CardTitle>
        <div className="flex gap-2">
          <Button size="sm" variant="outline" onClick={syncFromMavros} title="从飞控同步航点"><Download className="w-4 h-4 mr-1" />同步飞控</Button>
          <Button size="sm" variant="ghost" onClick={fetchData}><RefreshCw className="w-4 h-4" /></Button>
          <Button size="sm" onClick={saveAll}><Save className="w-4 h-4 mr-1" />保存</Button>
        </div>
      </CardHeader>
      <CardContent className="space-y-4">
        <div className="flex gap-2 items-end">
          <div className="space-y-1 flex-1">
            <Label className="text-xs">新增航点编号</Label>
            <Input value={newSeq} onChange={e => setNewSeq(e.target.value)} placeholder="例: 0" className="h-8" />
          </div>
          <Button size="sm" onClick={addWaypoint}><Plus className="w-4 h-4 mr-1" />添加</Button>
        </div>

        {sortedKeys.length === 0 && (
          <p className="text-sm text-muted-foreground text-center py-4">未配置任何航点采样规则，将对所有航点使用全局默认值。</p>
        )}

        {sortedKeys.map(key => {
          const item = data[key]
          return (
            <div key={key} className="p-3 border rounded-lg bg-card/50 space-y-3">
              <div className="flex items-center justify-between">
                <span className="font-bold text-sm">航点 #{key}</span>
                <div className="flex items-center gap-3">
                  <Label className="text-xs">启用</Label>
                  <Switch checked={item.enabled} onCheckedChange={v => updateField(key, 'enabled', v)} />
                  <Button size="icon" variant="ghost" className="text-destructive h-7 w-7" onClick={() => removeWaypoint(key)}>
                    <Trash2 className="w-3.5 h-3.5" />
                  </Button>
                </div>
              </div>
              {item.enabled && (
                <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
                  <div className="space-y-1">
                    <Label className="text-xs text-muted-foreground">采样轮次</Label>
                    <NumericInput integer min={0} value={item.loop_count} onValueChange={v => updateField(key, 'loop_count', v)} className="h-7 text-xs" />
                  </div>
                  <div className="space-y-1">
                    <Label className="text-xs text-muted-foreground">重试次数</Label>
                    <NumericInput integer min={0} value={item.retry_count} onValueChange={v => updateField(key, 'retry_count', v)} className="h-7 text-xs" />
                  </div>
                  <div className="space-y-1">
                    <Label className="text-xs text-muted-foreground">稳定等待(s)</Label>
                    <NumericInput min={0} value={item.hold_before_sampling_s} onValueChange={v => updateField(key, 'hold_before_sampling_s', v)} className="h-7 text-xs" />
                  </div>
                  <div className="space-y-1">
                    <Label className="text-xs text-muted-foreground">失败策略</Label>
                    <select
                      value={item.on_fail}
                      onChange={e => updateField(key, 'on_fail', e.target.value)}
                      className="h-7 text-xs w-full rounded-md border bg-background px-2"
                    >
                      <option value="HOLD">保持(HOLD)</option>
                      <option value="SKIP">跳过(SKIP)</option>
                      <option value="ABORT">中止(ABORT)</option>
                    </select>
                  </div>
                </div>
              )}
            </div>
          )
        })}
      </CardContent>
    </Card>
  )
}

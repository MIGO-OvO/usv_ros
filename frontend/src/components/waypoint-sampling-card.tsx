import { useEffect, useState, useCallback, type RefObject } from 'react'
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Switch } from "@/components/ui/switch"
import { NumericInput } from "@/components/ui/numeric-input"
import { ScrollArea } from "@/components/ui/scroll-area"
import { Plus, Trash2, RefreshCw, Download } from 'lucide-react'
import { toast } from '@/hooks/use-toast'
import { useAutoSave } from '@/hooks/use-auto-save'
import { AutoSaveStatus } from '@/components/auto-save-status'

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

export function WaypointSamplingCard({ saveRef, disabled = false }: {
  saveRef: RefObject<(() => Promise<void>) | null>
  disabled?: boolean
}) {
  const [data, setData] = useState<WaypointSamplingMap>({})
  const [loaded, setLoaded] = useState(false)
  const [newSeq, setNewSeq] = useState('')
  const autoSave = useAutoSave('/api/waypoint-sampling', loaded ? data : null)

  useEffect(() => {
    saveRef.current = autoSave.flush
    return () => { saveRef.current = null }
  }, [autoSave.flush, saveRef])

  const fetchData = useCallback(async () => {
    try {
      const res = await fetch('/api/waypoint-sampling')
      const json = await res.json()
      if (!res.ok || !json.success) throw new Error(json.message || '航点配置读取失败')
      setData(json.data || {})
      setLoaded(true)
    } catch (error) { toast({ title: '航点配置读取失败', description: String(error), variant: 'destructive' }) }
  }, [])

  useEffect(() => { fetchData() }, [fetchData])

  const syncFromMavros = async () => {
    try {
      await autoSave.flush()
      const res = await fetch('/api/waypoint-sampling/sync', { method: 'POST' })
      const json = await res.json()
      if (json.success) {
        toast({ title: '航点已同步', description: json.message })
        if (json.data) setData(json.data)
      } else {
        toast({ title: '同步失败', description: json.message, variant: 'destructive' })
      }
    } catch {
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

  const updateField = (key: string, field: keyof WaypointSamplingItem, value: WaypointSamplingItem[keyof WaypointSamplingItem]) => {
    setData({ ...data, [key]: { ...data[key], [field]: value } })
  }

  const sortedKeys = Object.keys(data).sort((a, b) => Number(a) - Number(b))

  return (
    <Card>
      <CardHeader className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <CardTitle className="text-base">航点采样配置</CardTitle>
        <div className="flex w-full flex-wrap gap-2 sm:w-auto">
          <Button size="sm" variant="outline" onClick={syncFromMavros} disabled={!loaded || disabled} title="从飞控同步航点"><Download className="w-4 h-4 mr-1" />同步飞控</Button>
          <Button size="sm" variant="ghost" onClick={async () => {
            try { await autoSave.flush(); await fetchData() } catch { /* 保存状态显示失败原因。 */ }
          }} disabled={disabled} aria-label="刷新航点配置"><RefreshCw className="w-4 h-4" /></Button>
          {loaded && <AutoSaveStatus {...autoSave} />}
        </div>
      </CardHeader>
      <CardContent><fieldset disabled={!loaded || disabled} className="min-w-0 space-y-4">
        <div className="flex flex-col gap-2 sm:flex-row sm:items-end">
          <div className="min-w-0 flex-1 space-y-1">
            <Label className="text-xs">新增航点编号</Label>
            <Input value={newSeq} onChange={e => setNewSeq(e.target.value)} placeholder="例: 0" className="h-8" />
          </div>
          <Button size="sm" className="w-full sm:w-auto" onClick={addWaypoint}><Plus className="w-4 h-4 mr-1" />添加</Button>
        </div>

        {sortedKeys.length === 0 && (
          <p className="text-sm text-muted-foreground text-center py-4">使用全局配置</p>
        )}

        {sortedKeys.length > 0 && (
          <ScrollArea className="max-h-[min(32rem,65vh)] rounded-lg border bg-muted/20">
            <div className="grid gap-3 p-3">
              {sortedKeys.map(key => {
                const item = data[key]
                return (
                  <div key={key} className="p-3 border rounded-lg bg-card/50 space-y-3">
                    <div className="flex items-center justify-between">
                      <span className="font-bold text-sm">航点 #{key}</span>
                      <div className="flex items-center gap-3">
                        <Label htmlFor={`waypoint-${key}-enabled`} className="text-xs">启用</Label>
                        <Switch
                          id={`waypoint-${key}-enabled`}
                          checked={item.enabled}
                          onCheckedChange={v => updateField(key, 'enabled', v)}
                          aria-label={`启用航点 ${key}`}
                        />
                        <Button
                          size="icon"
                          variant="ghost"
                          className="text-destructive h-7 w-7"
                          onClick={() => removeWaypoint(key)}
                          aria-label={`删除航点 ${key}`}
                        >
                          <Trash2 className="w-3.5 h-3.5" />
                        </Button>
                      </div>
                    </div>
                    {item.enabled && (
                      <div className="grid grid-cols-[repeat(auto-fit,minmax(min(100%,9rem),1fr))] gap-3">
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
            </div>
          </ScrollArea>
        )}
      </fieldset></CardContent>
    </Card>
  )
}

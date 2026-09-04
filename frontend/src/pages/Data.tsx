import { useEffect, useMemo, useState } from 'react'
import type { MouseEvent } from 'react'
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { ScrollArea } from "@/components/ui/scroll-area"
import { Archive, Download, Trash2, RefreshCw, FileText, Calendar, Save } from 'lucide-react'
import { LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer, Legend } from 'recharts'
import { cn } from "@/lib/utils"
import { useConfirm } from '@/hooks/use-confirm'

type MissionMeta = {
  readonly id: string
  readonly name: string
  readonly state?: 'running' | 'completed' | 'interrupted' | string
  readonly start_time: string
  readonly end_time: string | null
  readonly point_count: number
}

type MissionPoint = {
  readonly timestamp?: string
  readonly voltage?: number
  readonly absorbance?: number
}

type SpectrometerSummary = {
  readonly frame_count?: number
  readonly valid_count?: number
  readonly invalid_count?: number
  readonly target_rate_hz?: number | null
  readonly observed_rate_hz?: number | null
  readonly voltage_mean?: number | null
  readonly voltage_min?: number | null
  readonly voltage_max?: number | null
  readonly absorbance_mean?: number | null
  readonly absorbance_min?: number | null
  readonly absorbance_max?: number | null
  readonly raw_code_min?: number | null
  readonly raw_code_max?: number | null
  readonly quality_flags?: readonly string[]
}

type ManualResult = {
  readonly status?: string
  readonly analyte?: string | null
  readonly concentration?: number | null
  readonly unit?: string | null
  readonly method?: string | null
  readonly operator?: string | null
  readonly note?: string | null
}

type SampleWindow = {
  readonly sample_id: string
  readonly mode?: string
  readonly source?: string
  readonly state?: string
  readonly waypoint_seq?: number | null
  readonly mavlink_sample_id?: number | null
  readonly start_time?: string
  readonly end_time?: string | null
  readonly duration_s?: number | null
  readonly gps_start?: { readonly lat?: number; readonly lng?: number; readonly alt?: number | null } | null
  readonly gps_end?: { readonly lat?: number; readonly lng?: number; readonly alt?: number | null } | null
  readonly spectrometer?: SpectrometerSummary
  readonly manual_result?: ManualResult
}

type RawFrame = {
  readonly received_at?: number
  readonly timestamp_ms?: number | null
  readonly raw_code?: number | null
  readonly voltage?: number | null
  readonly absorbance?: number | null
  readonly valid?: boolean
  readonly status?: string | number | null
  readonly received_at_ms?: number | null
}

type RawSeries = {
  readonly raw_count: number
  readonly returned_count: number
  readonly covered: boolean
  readonly samples: readonly RawFrame[]
}

type MissionData = {
  readonly data_points?: readonly MissionPoint[]
}

type ApiResponse<T> = {
  readonly success: boolean
  readonly data: T
  readonly error?: string
  readonly message?: string
}

type ManualDraft = {
  analyte: string
  concentration: string
  unit: string
  method: string
  operator: string
  note: string
}

const emptyManualDraft: ManualDraft = {
  analyte: '',
  concentration: '',
  unit: 'mg/L',
  method: '',
  operator: '',
  note: '',
}

async function fetchJson<T>(url: string, init?: RequestInit): Promise<ApiResponse<T>> {
  const res = await fetch(url, init)
  return await res.json() as ApiResponse<T>
}

function fmt(value: number | null | undefined, digits = 4): string {
  if (typeof value !== 'number' || !Number.isFinite(value)) return '-'
  return Number.parseFloat(digits === 4 ? value.toPrecision(4) : value.toPrecision(digits)).toString()
}

function fmtAxis(value: number | string | undefined): string {
  const numeric = typeof value === 'number' ? value : Number(value)
  return Number.isFinite(numeric) ? Number.parseFloat(numeric.toPrecision(4)).toString() : String(value ?? '')
}

function gpsText(gps: SampleWindow['gps_start']): string {
  if (!gps || typeof gps.lat !== 'number' || typeof gps.lng !== 'number') return '-'
  return `${gps.lat.toFixed(6)}, ${gps.lng.toFixed(6)}`
}

export default function Data() {
  const confirm = useConfirm()
  const [missions, setMissions] = useState<readonly MissionMeta[]>([])
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [chartData, setChartData] = useState<readonly MissionPoint[]>([])
  const [samples, setSamples] = useState<readonly SampleWindow[]>([])
  const [selectedSampleId, setSelectedSampleId] = useState<string | null>(null)
  const [sampleDetail, setSampleDetail] = useState<SampleWindow | null>(null)
  const [rawFrames, setRawFrames] = useState<readonly RawFrame[]>([])
  const [rawSeries, setRawSeries] = useState<RawSeries | null>(null)
  const [manualDraft, setManualDraft] = useState<ManualDraft>(emptyManualDraft)
  const [loading, setLoading] = useState(false)
  const [saving, setSaving] = useState(false)

  useEffect(() => {
    void fetchMissions()
  }, [])

  useEffect(() => {
    if (selectedId) {
      void fetchMissionData(selectedId)
    } else {
      setChartData([])
      setSamples([])
      setSelectedSampleId(null)
    }
  }, [selectedId])

  useEffect(() => {
    const selected = samples[0]?.sample_id ?? null
    setSelectedSampleId(selected)
  }, [samples])

  useEffect(() => {
    if (selectedId && selectedSampleId) {
      void fetchSample(selectedId, selectedSampleId)
    } else {
      setSampleDetail(null)
      setRawFrames([])
      setRawSeries(null)
    }
  }, [selectedId, selectedSampleId])

  useEffect(() => {
    const result = sampleDetail?.manual_result
    setManualDraft({
      analyte: result?.analyte ?? '',
      concentration: result?.concentration == null ? '' : String(result.concentration),
      unit: result?.unit ?? 'mg/L',
      method: result?.method ?? '',
      operator: result?.operator ?? '',
      note: result?.note ?? '',
    })
  }, [sampleDetail])

  const fetchMissions = async () => {
    const json = await fetchJson<readonly MissionMeta[]>('/api/data/missions')
    if (json.success) {
      setMissions(json.data)
      if (json.data.length > 0 && !selectedId) setSelectedId(json.data[0].id)
    }
  }

  const fetchMissionData = async (id: string) => {
    setLoading(true)
    try {
      const [mission, sampleList] = await Promise.all([
        fetchJson<MissionData>(`/api/data/mission/${id}`),
        fetchJson<{ readonly samples: readonly SampleWindow[] }>(`/api/data/mission/${id}/samples`),
      ])
      setChartData(mission.success ? mission.data.data_points ?? [] : [])
      setSamples(sampleList.success ? sampleList.data.samples : [])
    } finally {
      setLoading(false)
    }
  }

  const fetchSample = async (missionId: string, sampleId: string) => {
    const [detail, raw] = await Promise.all([
      fetchJson<SampleWindow>(`/api/data/mission/${missionId}/sample/${sampleId}`),
      fetchJson<RawSeries>(`/api/data/voltage-series?mission_id=${encodeURIComponent(missionId)}&sample_id=${encodeURIComponent(sampleId)}&max_points=2000`),
    ])
    setSampleDetail(detail.success ? detail.data : null)
    setRawFrames(raw.success ? raw.data.samples : [])
    setRawSeries(raw.success ? raw.data : null)
  }

  const deleteMission = async (e: MouseEvent, id: string) => {
    e.stopPropagation()
    const ok = await confirm({ title: '删除任务', description: '确定要删除此任务记录吗？此操作不可撤销。' })
    if (!ok) return
    const json = await fetchJson<{ readonly message?: string }>(`/api/data/mission/${id}`, { method: 'DELETE' })
    if (json.success) {
      setMissions(prev => prev.filter(m => m.id !== id))
      if (selectedId === id) setSelectedId(null)
    }
  }

  const saveManualResult = async () => {
    if (!selectedId || !selectedSampleId) return
    setSaving(true)
    try {
      const payload = {
        analyte: manualDraft.analyte,
        concentration: manualDraft.concentration.trim() === '' ? null : Number(manualDraft.concentration),
        unit: manualDraft.unit,
        method: manualDraft.method,
        operator: manualDraft.operator,
        note: manualDraft.note,
      }
      const json = await fetchJson<SampleWindow>(`/api/data/mission/${selectedId}/sample/${selectedSampleId}/manual-result`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      })
      if (json.success) {
        setSampleDetail(json.data)
        setSamples(prev => prev.map(sample => sample.sample_id === json.data.sample_id ? json.data : sample))
      }
    } finally {
      setSaving(false)
    }
  }

  const openDownload = (path: string) => {
    window.open(path, '_blank', 'noopener,noreferrer')
  }

  const exportMissionSummaryCsv = (id: string) => {
    openDownload(`/api/data/mission/${encodeURIComponent(id)}/csv`)
  }

  const exportMissionRawCsv = (id: string) => {
    openDownload(`/api/data/mission/${encodeURIComponent(id)}/raw.csv`)
  }

  const exportMissionArchive = (id: string) => {
    openDownload(`/api/data/mission/${encodeURIComponent(id)}/archive`)
  }

  const exportRawCsv = () => {
    if (!selectedId || !sampleDetail) return
    openDownload(`/api/data/mission/${encodeURIComponent(selectedId)}/sample/${encodeURIComponent(sampleDetail.sample_id)}/raw.csv`)
  }

  const chartStats = useMemo(() => {
    const voltages = chartData.map(d => d.voltage).filter((v): v is number => typeof v === 'number' && Number.isFinite(v))
    if (voltages.length === 0) return null
    const min = Math.min(...voltages)
    const max = Math.max(...voltages)
    const margin = max > min ? (max - min) * 0.1 : 0.001
    return { count: chartData.length, vMin: min, vMax: max, yMin: min - margin, yMax: max + margin }
  }, [chartData])

  const rawChartData = useMemo(
    () => rawFrames.map((frame, index) => ({ ...frame, frame_index: index })),
    [rawFrames]
  )
  const rawHasAbsorbance = rawFrames.some(frame => typeof frame.absorbance === 'number')
  const selectedMission = missions.find(m => m.id === selectedId)
  const spectrometer = sampleDetail?.spectrometer
  const manual = sampleDetail?.manual_result
  const rawRateBelowTarget = spectrometer?.quality_flags?.includes('raw_rate_below_target') ?? false
  const rawChartNotice = !rawSeries
    ? '正在加载原始帧；CSV 始终包含全部原始帧。'
    : rawSeries.raw_count > rawSeries.returned_count
      ? `图表为性能显示 ${rawSeries.returned_count}/${rawSeries.raw_count} 帧（保留峰值）；CSV 包含全部原始帧。`
      : `图表显示全部 ${rawSeries.raw_count} 帧；CSV 始终包含全部原始帧。`

  const tooltipStyle = {
    backgroundColor: 'hsl(var(--card))',
    borderColor: 'hsl(var(--border))',
    borderRadius: '8px',
  }
  const formatTooltipValue = (value: unknown) => fmt(typeof value === 'number' ? value : Number(value))

  return (
    <div className="mx-auto flex min-h-[calc(100vh-5rem)] max-w-[1600px] flex-col gap-6 p-4 pb-24 md:p-8 xl:h-[calc(100vh-4rem)] xl:pb-8">
      <header className="flex shrink-0 flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
        <div>
          <h1 className="text-3xl font-bold tracking-tight">数据中心</h1>
          <p className="text-muted-foreground">历史任务、采样窗口与分光原始信号。</p>
        </div>
        <div className="flex flex-col items-start gap-2 sm:items-end">
          {selectedMission && (
            <div className="flex flex-wrap items-center gap-2">
              <Button size="sm" onClick={() => exportMissionRawCsv(selectedMission.id)}>
                <Download className="mr-2 h-4 w-4" />下载高频原始分光 CSV
              </Button>
              <Button size="sm" variant="outline" onClick={() => exportMissionSummaryCsv(selectedMission.id)}>
                <FileText className="mr-2 h-4 w-4" />下载任务摘要 CSV
              </Button>
              <Button size="sm" variant="outline" onClick={() => exportMissionArchive(selectedMission.id)}>
                <Archive className="mr-2 h-4 w-4" />完整任务 ZIP
              </Button>
            </div>
          )}
          {selectedMission && <p className="max-w-md text-xs text-muted-foreground sm:text-right">原始 CSV 是目标至少 20 Hz 的逐帧数据；任务摘要 CSV 仅用于地图和趋势展示。</p>}
          <Button className="self-start sm:self-auto" variant="outline" onClick={fetchMissions}><RefreshCw className="w-4 h-4 mr-2" />刷新列表</Button>
        </div>
      </header>

      <div className="grid grid-cols-1 gap-4 xl:min-h-0 xl:flex-1 xl:grid-cols-[minmax(18rem,22rem)_minmax(0,1fr)]">
        <aside className="grid gap-4 xl:min-h-0 xl:grid-rows-2" aria-label="任务与采样列表">
        <Card className="flex min-h-[280px] flex-col xl:min-h-0">
          <CardHeader className="pb-3">
            <CardTitle className="text-lg">任务列表</CardTitle>
          </CardHeader>
          <CardContent className="flex-1 min-h-0 p-0">
            <ScrollArea className="h-full">
              <div className="flex flex-col gap-1 p-3">
                {missions.length === 0 && <div className="text-center text-muted-foreground py-8 text-sm">暂无数据记录</div>}
                {missions.map((mission) => (
                  <div
                    key={mission.id}
                    className={cn(
                      "flex items-start gap-1 rounded-lg border p-1 transition-colors",
                      selectedId === mission.id ? "bg-primary/10 border-primary/20" : "hover:bg-muted border-transparent"
                    )}
                  >
                    <button
                      type="button"
                      onClick={() => setSelectedId(mission.id)}
                      aria-pressed={selectedId === mission.id}
                      className="flex min-w-0 flex-1 flex-col gap-1 rounded-md p-2 text-left outline-none focus-visible:ring-2 focus-visible:ring-ring"
                    >
                      <span className="truncate text-sm font-medium">{mission.name}</span>
                      <span className="flex items-center gap-2 text-xs text-muted-foreground">
                        <Calendar className="w-3 h-3 shrink-0" />
                        <span className="truncate">{new Date(mission.start_time).toLocaleString()}</span>
                      </span>
                      <span className="flex items-center gap-2 text-xs text-muted-foreground">
                        <FileText className="w-3 h-3" />
                        <span>{mission.point_count} 个摘要点</span>
                        {mission.state === 'running' && <span className="text-blue-600">记录中</span>}
                        {mission.state === 'interrupted' && <span className="text-amber-600">异常中断</span>}
                      </span>
                    </button>
                    <div className="flex shrink-0 gap-1 pr-1 pt-2">
                      <Button size="icon" variant="ghost" className="h-6 w-6 text-destructive hover:text-destructive" onClick={(e) => deleteMission(e, mission.id)} title="删除" aria-label={`删除任务 ${mission.name}`}>
                        <Trash2 className="w-3 h-3" />
                      </Button>
                    </div>
                  </div>
                ))}
              </div>
            </ScrollArea>
          </CardContent>
        </Card>

        <Card className="flex min-h-[280px] flex-col xl:min-h-0">
          <CardHeader className="pb-3">
            <CardTitle className="text-lg">采样窗口</CardTitle>
          </CardHeader>
          <CardContent className="flex-1 min-h-0 p-0">
            <ScrollArea className="h-full">
              <div className="flex flex-col gap-2 p-3">
                {loading && <div className="text-sm text-muted-foreground py-6 text-center">加载中...</div>}
                {!loading && samples.length === 0 && (
                  <div className="text-sm text-muted-foreground py-6 text-center">该任务暂无航点级分光切片</div>
                )}
                {samples.map(sample => (
                  <button
                    key={sample.sample_id}
                    type="button"
                    onClick={() => setSelectedSampleId(sample.sample_id)}
                    className={cn(
                      "text-left rounded-lg border p-3 transition-colors",
                      selectedSampleId === sample.sample_id ? "border-primary bg-primary/10" : "border-border hover:bg-muted"
                    )}
                  >
                    <div className="flex items-center justify-between gap-2">
                      <span className="font-medium text-sm truncate">#{sample.waypoint_seq ?? '-'} · {sample.mode ?? '-'}</span>
                      <span className="text-xs text-muted-foreground">{sample.spectrometer?.frame_count ?? 0} 帧</span>
                    </div>
                    <div className="mt-1 text-xs text-muted-foreground truncate">{sample.sample_id}</div>
                    <div className="mt-2 text-xs text-muted-foreground">
                      有效 {sample.spectrometer?.valid_count ?? 0}/{sample.spectrometer?.frame_count ?? 0}
                      {sample.manual_result?.status === 'recorded' && ` · ${sample.manual_result.analyte ?? '结果'} ${fmt(sample.manual_result.concentration)} ${sample.manual_result.unit ?? ''}`}
                    </div>
                  </button>
                ))}
              </div>
            </ScrollArea>
          </CardContent>
        </Card>
        </aside>

        <Card className="flex min-h-[420px] min-w-0 flex-col">
          <CardHeader className="pb-2 border-b">
            <div className="flex items-center justify-between gap-3">
              <CardTitle className="truncate">{sampleDetail ? sampleDetail.sample_id : selectedMission?.name ?? '请选择任务'}</CardTitle>
              {sampleDetail && <Button size="sm" variant="outline" onClick={exportRawCsv}><Download className="w-4 h-4 mr-2" />下载该窗口原始分光 CSV</Button>}
            </div>
          </CardHeader>
          <CardContent className="flex-1 min-h-0 pt-4 overflow-auto">
            {sampleDetail ? (
              <div className="space-y-4">
                <div className="grid grid-cols-2 md:grid-cols-4 gap-3 text-sm">
                  <div><div className="text-muted-foreground">模式/来源</div><div>{sampleDetail.mode ?? '-'} / {sampleDetail.source ?? '-'}</div></div>
                  <div><div className="text-muted-foreground">航点</div><div>#{sampleDetail.waypoint_seq ?? '-'}</div></div>
                  <div><div className="text-muted-foreground">时长</div><div>{fmt(sampleDetail.duration_s, 3)} s</div></div>
                  <div><div className="text-muted-foreground">状态</div><div>{sampleDetail.state ?? '-'}</div></div>
                  <div className="md:col-span-2"><div className="text-muted-foreground">GPS 起点</div><div>{gpsText(sampleDetail.gps_start)}</div></div>
                  <div className="md:col-span-2"><div className="text-muted-foreground">GPS 终点</div><div>{gpsText(sampleDetail.gps_end)}</div></div>
                </div>

                <div className="grid grid-cols-2 md:grid-cols-4 gap-3 rounded-lg border p-3 text-sm">
                  <div><div className="text-muted-foreground">帧数</div><div>{spectrometer?.frame_count ?? 0}</div></div>
                  <div><div className="text-muted-foreground">有效</div><div>{spectrometer?.valid_count ?? 0}</div></div>
                  <div><div className="text-muted-foreground">目标记录率</div><div>{spectrometer?.target_rate_hz == null ? '未记录' : `≥ ${fmt(spectrometer.target_rate_hz, 3)} Hz`}</div></div>
                  <div><div className="text-muted-foreground">实测帧率</div><div className={rawRateBelowTarget ? 'text-amber-600 dark:text-amber-400' : undefined}>{spectrometer?.observed_rate_hz == null ? '未记录' : `${fmt(spectrometer.observed_rate_hz, 3)} Hz`}{rawRateBelowTarget && '（低于目标）'}</div></div>
                  <div><div className="text-muted-foreground">电压均值</div><div>{fmt(spectrometer?.voltage_mean)} V</div></div>
                  <div><div className="text-muted-foreground">吸光度均值</div><div>{fmt(spectrometer?.absorbance_mean)}</div></div>
                  <div><div className="text-muted-foreground">电压范围</div><div>{fmt(spectrometer?.voltage_min)} ~ {fmt(spectrometer?.voltage_max)}</div></div>
                  <div><div className="text-muted-foreground">raw_code</div><div>{fmt(spectrometer?.raw_code_min, 6)} ~ {fmt(spectrometer?.raw_code_max, 6)}</div></div>
                  <div className="col-span-2"><div className="text-muted-foreground">质量标记</div><div>{spectrometer?.quality_flags?.join(', ') || '-'}</div></div>
                </div>

                <div className="space-y-2">
                  <p className="text-xs text-muted-foreground">{rawChartNotice}</p>
                  <div className="h-80 rounded-lg border p-3">
                  {rawChartData.length === 0 ? (
                    <div className="h-full flex items-center justify-center text-muted-foreground">该窗口暂无原始分光帧</div>
                  ) : (
                    <ResponsiveContainer width="100%" height="100%">
                      <LineChart data={rawChartData}>
                        <CartesianGrid strokeDasharray="3 3" opacity={0.2} />
                        <XAxis dataKey="frame_index" fontSize={11} tickLine={false} axisLine={false} />
                        <YAxis yAxisId="voltage" fontSize={11} tickLine={false} axisLine={false} tickFormatter={fmtAxis} width={48} />
                        {rawHasAbsorbance && <YAxis yAxisId="absorbance" orientation="right" fontSize={11} tickLine={false} axisLine={false} tickFormatter={fmtAxis} width={48} />}
                        <Tooltip contentStyle={tooltipStyle} formatter={formatTooltipValue} />
                        <Legend />
                        <Line yAxisId="voltage" type="monotone" dataKey="voltage" name="电压" stroke="hsl(var(--chart-1))" dot={false} isAnimationActive={false} />
                        {rawHasAbsorbance && <Line yAxisId="absorbance" type="monotone" dataKey="absorbance" name="吸光度" stroke="hsl(var(--chart-2))" dot={false} isAnimationActive={false} />}
                      </LineChart>
                    </ResponsiveContainer>
                  )}
                  </div>
                </div>

                <div className="rounded-lg border p-3 space-y-3">
                  <div className="font-medium text-sm">人工浓度记录 {manual?.status === 'recorded' && <span className="text-xs text-muted-foreground">已记录</span>}</div>
                  <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
                    <div className="space-y-1"><Label>污染物</Label><Input value={manualDraft.analyte} onChange={e => setManualDraft({ ...manualDraft, analyte: e.target.value })} /></div>
                    <div className="space-y-1"><Label>浓度</Label><Input type="number" step="any" value={manualDraft.concentration} onChange={e => setManualDraft({ ...manualDraft, concentration: e.target.value })} /></div>
                    <div className="space-y-1"><Label>单位</Label><Input value={manualDraft.unit} onChange={e => setManualDraft({ ...manualDraft, unit: e.target.value })} /></div>
                    <div className="space-y-1"><Label>方法</Label><Input value={manualDraft.method} onChange={e => setManualDraft({ ...manualDraft, method: e.target.value })} /></div>
                    <div className="space-y-1"><Label>记录人</Label><Input value={manualDraft.operator} onChange={e => setManualDraft({ ...manualDraft, operator: e.target.value })} /></div>
                    <div className="space-y-1 md:col-span-3">
                      <Label>备注</Label>
                      <textarea className="w-full min-h-20 rounded-md border border-input bg-background px-3 py-2 text-sm" value={manualDraft.note} onChange={e => setManualDraft({ ...manualDraft, note: e.target.value })} />
                    </div>
                  </div>
                  <Button onClick={saveManualResult} disabled={saving}><Save className="w-4 h-4 mr-2" />{saving ? '保存中...' : '保存结果'}</Button>
                </div>
              </div>
            ) : chartData.length > 0 ? (
              <div className="space-y-2">
                <p className="text-xs text-muted-foreground">任务摘要趋势：{chartStats?.count ?? 0} 个低频地图点。需要逐帧数据请使用“下载高频原始分光 CSV”。</p>
                <div className="h-[clamp(320px,48vh,520px)] min-h-80">
                  <ResponsiveContainer width="100%" height="100%">
                    <LineChart data={chartData}>
                      <CartesianGrid strokeDasharray="3 3" opacity={0.2} />
                      <XAxis dataKey="timestamp" tickFormatter={(t) => new Date(String(t)).toLocaleTimeString()} fontSize={11} tickLine={false} axisLine={false} />
                      <YAxis yAxisId="voltage" domain={chartStats ? [chartStats.yMin, chartStats.yMax] : ['auto', 'auto']} fontSize={11} tickLine={false} axisLine={false} tickFormatter={fmtAxis} width={48} />
                      <Tooltip contentStyle={tooltipStyle} labelFormatter={(t) => new Date(String(t)).toLocaleString()} formatter={formatTooltipValue} />
                      <Legend />
                      <Line yAxisId="voltage" type="monotone" dataKey="voltage" name="电压" stroke="hsl(var(--chart-1))" dot={false} isAnimationActive={false} />
                    </LineChart>
                  </ResponsiveContainer>
                </div>
              </div>
            ) : (
              <div className="h-full flex items-center justify-center text-muted-foreground flex-col gap-2">
                <FileText className="w-8 h-8 opacity-20" />
                <p>{selectedId ? '该任务暂无数据点' : '从左侧列表选择任务'}</p>
              </div>
            )}
          </CardContent>
        </Card>
      </div>
    </div>
  )
}

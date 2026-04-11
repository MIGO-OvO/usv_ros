import { useEffect, useRef, useState } from 'react'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { ScrollArea } from '@/components/ui/scroll-area'
import { FileText, RefreshCw, Download } from 'lucide-react'
import { cn } from '@/lib/utils'

interface LogFile {
  name: string
  size: number
  modified: string
}

export function SystemLogViewer() {
  const [files, setFiles] = useState<LogFile[]>([])
  const [selectedFile, setSelectedFile] = useState('')
  const [lines, setLines] = useState<string[]>([])
  const [loading, setLoading] = useState(false)
  const [autoScroll, setAutoScroll] = useState(true)
  const scrollRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    fetchFiles()
  }, [])

  useEffect(() => {
    if (!selectedFile) return
    fetchLog(selectedFile)
    const t = setInterval(() => fetchLog(selectedFile), 5000)
    return () => clearInterval(t)
  }, [selectedFile])

  useEffect(() => {
    if (autoScroll && scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight
    }
  }, [lines, autoScroll])

  const fetchFiles = async () => {
    try {
      const r = await fetch('/api/logs/files')
      const j = await r.json()
      if (j.success) {
        setFiles(j.data)
        if (j.data.length > 0 && !selectedFile) setSelectedFile(j.data[0].name)
      }
    } catch { /* ignore */ }
  }

  const fetchLog = async (name: string) => {
    setLoading(true)
    try {
      const r = await fetch(`/api/logs/${encodeURIComponent(name)}?lines=200`)
      const j = await r.json()
      if (j.success) setLines(j.data)
    } catch { /* ignore */ }
    finally { setLoading(false) }
  }

  const downloadLog = () => {
    if (!selectedFile) return
    const blob = new Blob([lines.join('')], { type: 'text/plain' })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = selectedFile
    a.click()
    URL.revokeObjectURL(url)
  }

  const formatSize = (bytes: number) => {
    if (bytes < 1024) return `${bytes} B`
    if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
    return `${(bytes / 1024 / 1024).toFixed(1)} MB`
  }

  return (
    <Card className="col-span-full">
      <CardHeader className="pb-3">
        <div className="flex items-center justify-between">
          <CardTitle className="flex items-center gap-2">
            <FileText className="w-5 h-5 text-blue-500" />
            系统日志
          </CardTitle>
          <div className="flex items-center gap-2">
            <label className="flex items-center gap-1.5 text-xs text-muted-foreground cursor-pointer">
              <input type="checkbox" checked={autoScroll} onChange={e => setAutoScroll(e.target.checked)}
                     className="rounded border-muted" />
              自动滚动
            </label>
            <Button size="sm" variant="outline" onClick={() => fetchLog(selectedFile)} disabled={!selectedFile}>
              <RefreshCw className={cn("w-3.5 h-3.5 mr-1", loading && "animate-spin")} /> 刷新
            </Button>
            <Button size="sm" variant="outline" onClick={downloadLog} disabled={lines.length === 0}>
              <Download className="w-3.5 h-3.5 mr-1" /> 下载
            </Button>
          </div>
        </div>
        {/* 文件选择 */}
        <div className="flex gap-1.5 pt-2">
          {files.map(f => (
            <button key={f.name} onClick={() => setSelectedFile(f.name)}
              className={cn(
                "px-3 py-1 rounded-md text-xs border transition-colors",
                selectedFile === f.name
                  ? "bg-primary/10 border-primary/30 text-primary font-medium"
                  : "border-transparent hover:bg-muted text-muted-foreground"
              )}>
              {f.name} <span className="opacity-60">({formatSize(f.size)})</span>
            </button>
          ))}
          {files.length === 0 && (
            <span className="text-xs text-muted-foreground">无可用日志文件</span>
          )}
        </div>
      </CardHeader>
      <CardContent>
        <ScrollArea className="h-80 rounded-md border bg-muted/30" ref={scrollRef as any}>
          <div className="p-3 font-mono text-xs leading-relaxed whitespace-pre-wrap">
            {lines.length === 0 ? (
              <span className="text-muted-foreground">暂无日志内容</span>
            ) : (
              lines.map((line, i) => (
                <div key={i} className={cn(
                  /\bERROR\b/i.test(line) && "text-red-500",
                  /\bWARN/i.test(line) && "text-orange-500",
                )}>
                  {line}
                </div>
              ))
            )}
          </div>
        </ScrollArea>
      </CardContent>
    </Card>
  )
}

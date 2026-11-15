import { Check, Loader2, RotateCcw } from 'lucide-react'
import { Button } from '@/components/ui/button'

interface Props {
  saving: boolean
  pending: boolean
  error: string | null
  flush: () => Promise<void>
}

export function AutoSaveStatus({ saving, pending, error, flush }: Props) {
  if (error) return <span role="alert">
    <Button size="sm" variant="ghost" className="text-destructive" title={error}
      disabled={saving} onClick={() => { void flush().catch(() => {}) }}>
      <RotateCcw className="mr-1.5 h-3.5 w-3.5" />保存失败，重试
    </Button>
  </span>
  return (
    <span role="status" className="flex items-center gap-1.5 text-xs text-muted-foreground">
      {saving || pending ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Check className="h-3.5 w-3.5" />}
      {saving || pending ? '正在保存' : '已保存'}
    </span>
  )
}

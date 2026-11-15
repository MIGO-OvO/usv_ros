import { useId, useState } from 'react'
import { AlertDialog, AlertDialogContent, AlertDialogHeader, AlertDialogTitle, AlertDialogFooter } from '@/components/ui/alert-dialog'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Button } from '@/components/ui/button'
import { recordingName } from '@/lib/recording-name'

export function RecordingNameDialog({ title, label, confirmLabel, initialName = '', onCancel, onConfirm }: {
  title: string
  label: string
  confirmLabel: string
  initialName?: string
  onCancel: () => void
  onConfirm: (name: string) => void
}) {
  const id = useId()
  const [name, setName] = useState(initialName)
  const [error, setError] = useState('')
  return (
    <AlertDialog open onOpenChange={(open) => { if (!open) onCancel() }}>
      <AlertDialogContent aria-describedby={undefined} className="max-w-md">
        <AlertDialogHeader><AlertDialogTitle>{title}</AlertDialogTitle></AlertDialogHeader>
        <form className="space-y-4" onSubmit={(event) => {
          event.preventDefault()
          try { onConfirm(recordingName(name)) } catch (error) { setError(String((error as Error).message)) }
        }}>
          <div className="space-y-2">
            <Label htmlFor={id}>{label}</Label>
            <Input id={id} autoFocus value={name} onChange={(event) => { setName(event.target.value); setError('') }}
              aria-invalid={Boolean(error)} aria-describedby={error ? `${id}-error` : undefined} />
            {error && <p id={`${id}-error`} role="alert" className="text-sm text-destructive">{error}</p>}
          </div>
          <AlertDialogFooter>
            <Button type="button" variant="outline" onClick={onCancel}>取消</Button>
            <Button type="submit" disabled={!name.trim()}>{confirmLabel}</Button>
          </AlertDialogFooter>
        </form>
      </AlertDialogContent>
    </AlertDialog>
  )
}

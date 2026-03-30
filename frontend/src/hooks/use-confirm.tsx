import * as React from "react"
import {
  AlertDialog,
  AlertDialogContent,
  AlertDialogHeader,
  AlertDialogFooter,
  AlertDialogTitle,
  AlertDialogDescription,
  AlertDialogCancel,
} from "@/components/ui/alert-dialog"
import { Button } from "@/components/ui/button"

interface ConfirmState {
  open: boolean
  title: string
  description: string
  onConfirm: () => void
}

const ConfirmContext = React.createContext<
  (opts: { title: string; description: string }) => Promise<boolean>
>(() => Promise.resolve(false))

export function ConfirmProvider({ children }: { children: React.ReactNode }) {
  const [state, setState] = React.useState<ConfirmState>({
    open: false,
    title: "",
    description: "",
    onConfirm: () => {},
  })

  const resolveRef = React.useRef<(value: boolean) => void>(undefined)

  const confirm = React.useCallback(
    (opts: { title: string; description: string }) => {
      return new Promise<boolean>((resolve) => {
        resolveRef.current = resolve
        setState({ open: true, title: opts.title, description: opts.description, onConfirm: () => {} })
      })
    },
    []
  )

  const handleClose = (confirmed: boolean) => {
    setState((prev) => ({ ...prev, open: false }))
    resolveRef.current?.(confirmed)
  }

  return (
    <ConfirmContext.Provider value={confirm}>
      {children}
      <AlertDialog open={state.open} onOpenChange={(open) => { if (!open) handleClose(false) }}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>{state.title}</AlertDialogTitle>
            <AlertDialogDescription>{state.description}</AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel onClick={() => handleClose(false)}>取消</AlertDialogCancel>
            <Button variant="destructive" onClick={() => handleClose(true)}>确认</Button>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </ConfirmContext.Provider>
  )
}

export const useConfirm = () => React.useContext(ConfirmContext)


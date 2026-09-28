import { useEffect, useState } from 'react'
import type { Diagnostics } from '@/components/gps-status-card'

export function useGpsDiagnostics() {
  const [gps, setGps] = useState<Diagnostics | null>(null)
  const [failed, setFailed] = useState(false)
  useEffect(() => {
    let disposed = false
    let timer: ReturnType<typeof setTimeout>
    let controller: AbortController
    const poll = async () => {
      controller = new AbortController()
      const timeout = setTimeout(() => controller.abort(), 4000)
      try {
        const response = await fetch('/api/gps/diagnostics', { signal: controller.signal, cache: 'no-store' })
        if (!response.ok) throw new Error('GPS diagnostics unavailable')
        const data: Diagnostics = await response.json()
        if (!disposed) { setGps(data); setFailed(false) }
      } catch {
        if (!disposed) { setGps(null); setFailed(true) }
      } finally {
        clearTimeout(timeout)
        if (!disposed) timer = setTimeout(poll, 1000)
      }
    }
    void poll()
    return () => { disposed = true; controller?.abort(); clearTimeout(timer) }
  }, [])
  return { gps, failed }
}

import { useCallback, useEffect, useRef, useState } from 'react'

export function useAutoSave(url: string, value: unknown | null) {
  const body = value === null ? null : JSON.stringify(value)
  const latest = useRef(body)
  const saved = useRef<string | null>(null)
  const queue = useRef<Promise<void>>(Promise.resolve())
  const [savedBody, setSavedBody] = useState<string | null>(null)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)
  latest.current = body

  const flush = useCallback(() => {
    const snapshot = latest.current
    const request = queue.current.catch(() => {}).then(async () => {
      if (snapshot === null || snapshot === saved.current) return
      setSaving(true)
      try {
        const response = await fetch(url, {
          method: 'POST', headers: { 'Content-Type': 'application/json' }, body: snapshot,
        })
        const result = await response.json()
        if (!response.ok || !result.success) throw new Error(result.message || '配置保存失败')
        saved.current = snapshot
        setSavedBody(snapshot)
        setError(null)
      } catch (cause) {
        setError(cause instanceof Error ? cause.message : String(cause))
        throw cause
      } finally {
        setSaving(false)
      }
    })
    queue.current = request
    return request
  }, [url])

  useEffect(() => {
    if (body === null) return
    if (saved.current === null) {
      saved.current = body
      // Record the loaded configuration without writing it back to the server.
      setSavedBody(body)
      return
    }
    if (body === saved.current) return
    const timer = setTimeout(() => { void flush().catch(() => {}) }, 500)
    return () => clearTimeout(timer)
  }, [body, flush])

  useEffect(() => () => { void flush().catch(() => {}) }, [flush])

  return { flush, saving, error, pending: body !== null && body !== savedBody }
}

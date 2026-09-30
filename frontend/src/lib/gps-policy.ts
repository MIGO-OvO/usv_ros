/** Persist an optimistic toggle, restoring the confirmed value on any failure. */
export async function persistGpsPolicy(
  next: boolean, previous: boolean, change: (value: boolean) => void,
  request: typeof fetch = fetch,
) {
  change(next)
  try {
    const response = await request('/api/config', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ automation_policy: { require_gps: next } }),
    })
    if (!response.ok) throw new Error(`HTTP ${response.status}`)
    const result = await response.json()
    if (result.success !== true) throw new Error('GPS policy was not saved')
  } catch (error) {
    change(previous)
    throw error
  }
}

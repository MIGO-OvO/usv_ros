import { test } from 'node:test'
import assert from 'node:assert/strict'
import { persistGpsPolicy } from '../src/lib/gps-policy.ts'

for (const failure of ['http', 'application', 'network']) {
  test(`GPS save ${failure} failure rolls back the optimistic toggle`, async () => {
    const changes: boolean[] = []
    const request = async () => {
      if (failure === 'network') throw new Error('offline')
      return new Response(JSON.stringify({ success: false }), { status: failure === 'http' ? 500 : 200 })
    }
    await assert.rejects(persistGpsPolicy(false, true, v => changes.push(v), request))
    assert.deepEqual(changes, [false, true])
  })
}
test('GPS save success retains the new value', async () => {
  const changes: boolean[] = []
  await persistGpsPolicy(false, true, v => changes.push(v), async () => new Response('{"success":true}'))
  assert.deepEqual(changes, [false])
})

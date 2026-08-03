import assert from 'node:assert/strict'
import test from 'node:test'

import {
  getAutomationControlAvailability,
  resolveAutomationAction,
} from '../src/lib/automation-controls.ts'

test('paused automation can be resumed with either Start or Resume and can be stopped', () => {
  const controls = getAutomationControlAvailability({ running: false, paused: true })

  assert.deepEqual(controls, {
    start: true,
    pause: false,
    resume: true,
    stop: true,
  })
  assert.equal(resolveAutomationAction('start', { running: false, paused: true }), 'resume')
})

test('idle and running automation expose only valid controls', () => {
  assert.deepEqual(
    getAutomationControlAvailability({ running: false, paused: false }),
    { start: true, pause: false, resume: false, stop: false },
  )
  assert.deepEqual(
    getAutomationControlAvailability({ running: true, paused: false }),
    { start: false, pause: true, resume: false, stop: true },
  )
  assert.equal(resolveAutomationAction('start', { running: false, paused: false }), 'start')
})

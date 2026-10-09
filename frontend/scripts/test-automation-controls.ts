import assert from 'node:assert/strict'
import test from 'node:test'
import { getInvolvedAxes, getPreflightLabel } from '../src/lib/automation-preflight.ts'

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

test('preflight exposes stop while disabling start, pause and resume', () => {
  for (const running of [false, true]) {
    assert.deepEqual(getAutomationControlAvailability({ running, paused: false, preflight: true }),
      { start: false, pause: false, resume: false, stop: true })
  }
})

test('preflight summary follows enabled axes and names each observed stage', () => {
  assert.deepEqual(getInvolvedAxes([{ X: { enable: 'E' }, Y: { enable: 'D' } },
    { Z: { enable: 'E' }, X: { enable: 'E' } }]), ['X', 'Z'])
  assert.equal(getPreflightLabel({ active: true, phase: 'homing' }), '回到相对零点')
  assert.equal(getPreflightLabel({ active: true, phase: 'separating' }), '油相分隔')
  assert.equal(getPreflightLabel({ active: false, phase: 'failed' }), '准备失败')
})

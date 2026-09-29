import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'
import vm from 'node:vm'
import ts from 'typescript'

// Execute the actual component helper, without duplicating its decision logic.
const source = readFileSync(new URL('../src/components/gps-status-card.tsx', import.meta.url), 'utf8')
const num = source.slice(source.indexOf('const num ='), source.indexOf('const date ='))
const helper = source.slice(source.indexOf('const reasonText:'), source.indexOf('export function GpsStatusCard'))
const js = ts.transpileModule(num + helper, { compilerOptions: { target: ts.ScriptTarget.ES2022 } }).outputText
const layerStatus = vm.runInNewContext(js + '\nlayerStatus')
const gps = (age, valid = false) => ({
  freshness_threshold_s: 2,
  gps_raw: { available: true, fix_label: '3D Fix', satellites: 15, hdop: 0.9 },
  navsat: { available: true, age_s: age },
  sampling_position: { valid, reason: valid ? null : 'gps_stale' },
})

test('5s-old NavSatFix is received but stale for a 2s sampling threshold', () => {
  const result = layerStatus(gps(5))
  assert.match(result.mavros, /已接收 · age 5.0s · 超过采样阈值 2.0s/)
  assert.doesNotMatch(result.mavros, /正常/)
  assert.match(result.sampling, /不可用 · 数据过期/)
})

test('fresh NavSatFix and admission are independent facts', () => {
  const input = gps(1)
  input.sampling_position.reason = 'gps_no_fix'
  const result = layerStatus(input)
  assert.match(result.mavros, /采样时效内/)
  assert.match(result.sampling, /不可用 · 无 Fix/)
})

test('an admitted fresh fix is shown as available for sampling', () => {
  assert.match(layerStatus(gps(1, true)).sampling, /可用于采样/)
})

test('missing NavSatFix is not replaced by raw or global coordinates', () => {
  const input = gps(0)
  input.navsat.available = false
  input.sampling_position.reason = 'gps_missing'
  assert.equal(layerStatus(input).mavros, '未收到 NavSatFix')
  assert.match(layerStatus(input).sampling, /不可用 · 无数据/)
})

test('threshold comes from backend without a 10s display floor', () => {
  const input = gps(1)
  input.freshness_threshold_s = 0.5
  assert.match(layerStatus(input).mavros, /超过采样阈值 0.5s/)
  input.freshness_threshold_s = 2
  input.navsat.age_s = 2
  assert.match(layerStatus(input).mavros, /采样时效内/)
})

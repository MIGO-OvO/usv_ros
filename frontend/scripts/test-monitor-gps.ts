import assert from 'node:assert/strict'
import test from 'node:test'
import { summarizeGps } from '../src/lib/monitor-gps.ts'

const position = { available: true, latitude: 25.4219396, longitude: 110.2543123, altitude: 34.7, age_s: 0.5, stale: false }
const fixture = () => ({ gps_raw: { ...position, valid: true }, global_position: { ...position }, navsat: { ...position, valid_navsat_fix: true }, freshness_threshold_s: 2 })

test('fresh fix is normal; unknown is not a fault', () => {
  assert.equal(summarizeGps(fixture()).label, '定位正常')
  assert.equal(summarizeGps(null).tone, 'neutral')
})
test('missing raw with valid global position is amber, not unavailable or sampling admission', () => {
  const gps = fixture()
  gps.gps_raw.available = false
  gps.gps_raw.valid = false
  gps.navsat.valid_navsat_fix = false
  assert.equal(summarizeGps(gps).label, '定位降级')
  assert.equal(summarizeGps(gps).tone, 'warning')
  assert.equal(summarizeGps(gps).source, 'GLOBAL_POSITION_INT')
  assert.equal(gps.navsat.valid_navsat_fix, false)
})
test('older global coordinates degrade then become unavailable after 10 seconds', () => {
  const gps = fixture()
  gps.navsat.valid_navsat_fix = false
  gps.gps_raw.valid = false
  gps.global_position.age_s = 4.8
  gps.global_position.stale = true
  assert.equal(summarizeGps(gps).tone, 'warning')
  gps.global_position.age_s = 11
  assert.equal(summarizeGps(gps).tone, 'error')
})
test('invalid or missing coordinate values cannot appear usable', () => {
  for (const latitude of [NaN, Infinity, 91]) {
    const gps = fixture()
    gps.navsat.valid_navsat_fix = false
    gps.gps_raw.valid = false
    gps.global_position.latitude = latitude
    assert.equal(summarizeGps(gps).label, '定位不可用')
  }
})

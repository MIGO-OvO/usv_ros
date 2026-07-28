import assert from 'node:assert/strict'
import test from 'node:test'
import { RingBuffer } from '../src/lib/time-series/ring-buffer.ts'
import { minMaxDownsample } from '../src/lib/time-series/min-max-downsample.ts'
import { buildVoltageHistoryCsv, voltageHistoryFilename } from '../src/lib/voltage-history-csv.ts'
import {
  analyzeSpikeTest,
  buildSpikeTestSummaryCsv,
  createSpikeTestSession,
  finishSpikeTestSession,
} from '../src/lib/spectro-spike-test.ts'

test('ring buffer keeps the newest items in order', () => {
  const buffer = new RingBuffer<number>(3)
  buffer.appendBatch([1, 2])
  buffer.appendBatch([3, 4])
  assert.equal(buffer.length, 3)
  assert.deepEqual(buffer.toArray(), [2, 3, 4])
})

test('ring buffer stays bounded at the production capacity', () => {
  const buffer = new RingBuffer<number>(20_000)
  buffer.appendBatch(Array.from({ length: 30_000 }, (_, index) => index))
  const snapshot = buffer.toArray()
  assert.equal(snapshot.length, 20_000)
  assert.equal(snapshot[0], 10_000)
  assert.equal(snapshot[snapshot.length - 1], 29_999)
})

test('ring buffer clear removes retained items and accepts new samples', () => {
  const buffer = new RingBuffer<number>(3)
  buffer.appendBatch([1, 2, 3])
  buffer.clear()
  assert.equal(buffer.length, 0)
  assert.deepEqual(buffer.toArray(), [])

  buffer.appendBatch([4])
  assert.deepEqual(buffer.toArray(), [4])
})

test('min/max downsampling retains a short peak and stays within the pixel budget', () => {
  const points = Array.from({ length: 1000 }, (_, index) => ({
    receivedAtMs: index,
    voltage: index === 501 ? 9 : index === 502 ? -4 : 1,
  }))
  const sampled = minMaxDownsample(points, 100)
  assert.ok(sampled.length <= 202)
  assert.equal(sampled[0], points[0])
  assert.equal(sampled[sampled.length - 1], points[points.length - 1])
  assert.ok(sampled.some((point) => point.voltage === 9))
  assert.ok(sampled.some((point) => point.voltage === -4))
  assert.ok(sampled.every((point, index) => index === 0 || sampled[index - 1].receivedAtMs <= point.receivedAtMs))
})

test('one hundred thousand samples stay within a 1000px canvas budget', () => {
  const points = Array.from({ length: 100_000 }, (_, index) => ({
    receivedAtMs: index,
    voltage: index === 50_001 ? 12 : Math.sin(index / 100),
  }))
  const sampled = minMaxDownsample(points, 1000)
  assert.ok(sampled.length <= 2002)
  assert.ok(sampled.some((point) => point.voltage === 12))
})

test('voltage history CSV exports complete raw samples with an Excel-compatible BOM', () => {
  const csv = buildVoltageHistoryCsv([
    {
      seq: 42,
      sourceTimestampMs: 1_750_000_000_000,
      receivedAtMs: 1_750_000_000_125,
      voltage: 2.345,
      absorbance: 0.1234,
      rawCode: 123456,
      valid: true,
    },
    {
      seq: 43,
      sourceTimestampMs: 0,
      receivedAtMs: 1_750_000_000_225,
      voltage: 2.346,
      absorbance: null,
      valid: false,
    },
  ])

  assert.ok(csv.startsWith('\uFEFFreceived_time_iso,received_time_ms,source_time_iso'))
  assert.match(csv, /,42,2\.345,0\.1234,123456,true\r\n/)
  assert.match(csv, /,,43,2\.346,,,false\r\n$/)
  assert.equal(csv.split('\r\n').filter(Boolean).length, 3)
})

test('voltage history filename uses a sortable local timestamp', () => {
  const exportedAt = new Date(2026, 6, 26, 14, 5, 9)
  assert.equal(voltageHistoryFilename(exportedAt), 'spectrometer-voltage-history_2026-07-26_14-05-09.csv')
})

test('spike test counts session drops and ADS counter deltas', () => {
  const started = createSpikeTestSession(1000, {
    crcError: 1,
    duplicate: 10,
    transientDrop: 2,
  })
  const points = [
    [1000, 1.000],
    [1050, 0.994],
    [1100, 1.000],
    [1150, 0.989],
    [1200, 1.000],
    [1250, 0.979],
    [1300, 1.000],
  ].map(([receivedAtMs, voltage], index) => ({
    seq: index + 1,
    sourceTimestampMs: receivedAtMs,
    receivedAtMs,
    voltage,
    absorbance: null,
    valid: true,
  }))
  const session = finishSpikeTestSession(started, 1350, {
    crcError: 1,
    duplicate: 13,
    transientDrop: 4,
  })

  const summary = analyzeSpikeTest(points, session, {
    crcError: 99,
    duplicate: 99,
    transientDrop: 99,
  }, 9999)

  assert.equal(summary.active, false)
  assert.equal(summary.durationS, 0.35)
  assert.equal(summary.sampleCount, 7)
  assert.equal(summary.receiveRateHz, 20)
  assert.equal(summary.dropCount5mv, 3)
  assert.equal(summary.dropCount10mv, 2)
  assert.equal(summary.dropCount20mv, 1)
  assert.ok(Math.abs(summary.maxDownMv - 21) < 1e-9)
  assert.equal(summary.adsCrcErrorDelta, 0)
  assert.equal(summary.adsDuplicateDelta, 3)
  assert.equal(summary.adsTransientDropDelta, 2)
  assert.equal(summary.counterResetDetected, false)
})

test('spike test ignores invalid samples and marks counter reset', () => {
  const session = createSpikeTestSession(2000, {
    crcError: 3,
    duplicate: 20,
    transientDrop: 8,
  })
  const summary = analyzeSpikeTest([
    { seq: 1, sourceTimestampMs: 2000, receivedAtMs: 2000, voltage: 1.0, absorbance: null, valid: true },
    { seq: 2, sourceTimestampMs: 2050, receivedAtMs: 2050, voltage: 0.0, absorbance: null, valid: false },
    { seq: 3, sourceTimestampMs: 2100, receivedAtMs: 2100, voltage: Number.NaN, absorbance: null, valid: true },
    { seq: 4, sourceTimestampMs: 2150, receivedAtMs: 2150, voltage: 0.995, absorbance: null, valid: true },
  ], session, {
    crcError: 0,
    duplicate: 2,
    transientDrop: 1,
  }, 2200)

  assert.equal(summary.sampleCount, 2)
  assert.equal(summary.dropCount5mv, 0)
  assert.equal(summary.adsCrcErrorDelta, null)
  assert.equal(summary.adsDuplicateDelta, null)
  assert.equal(summary.adsTransientDropDelta, null)
  assert.equal(summary.counterResetDetected, true)
})

test('spike test summary CSV uses cross-platform columns', () => {
  const session = finishSpikeTestSession(
    createSpikeTestSession(1000, { crcError: 0 }, 30),
    1100,
    { crcError: 0 },
  )
  const summary = analyzeSpikeTest([
    { seq: 1, sourceTimestampMs: 1000, receivedAtMs: 1000, voltage: 1.0, absorbance: null, valid: true },
  ], session, { crcError: 99 }, 9999)
  const csv = buildSpikeTestSummaryCsv(summary, 'ros-test', 'ros_web')

  assert.ok(csv.startsWith('\uFEFFsession_id,transport_path,started_at_ms'))
  assert.match(csv, /ros-test,ros_web,1000,1100,0\.1,30,1,/)
  assert.match(csv, /target_duration_s/)
  assert.match(csv, /ads_transient_drop_delta/)
})

test('spike test uses a fixed deadline and ignores late samples', () => {
  const started = createSpikeTestSession(1000, { crcError: 0 }, 30)
  assert.equal(started.targetDurationS, 30)
  assert.equal(started.deadlineMs, 31000)

  const points = [
    { seq: 1, sourceTimestampMs: 1000, receivedAtMs: 1000, voltage: 1.000, absorbance: null, valid: true },
    { seq: 2, sourceTimestampMs: 30999, receivedAtMs: 30999, voltage: 0.990, absorbance: null, valid: true },
    { seq: 3, sourceTimestampMs: 31001, receivedAtMs: 31001, voltage: 0.900, absorbance: null, valid: true },
  ]
  const running = analyzeSpikeTest(points, started, { crcError: 0 }, 16000)
  assert.equal(running.targetDurationS, 30)
  assert.equal(running.remainingS, 15)
  assert.equal(running.sampleCount, 1)

  const completedSession = finishSpikeTestSession(started, 32000, { crcError: 0 })
  assert.equal(completedSession.endedAtMs, 31000)
  const completed = analyzeSpikeTest(points, completedSession, { crcError: 0 }, 99999)
  assert.equal(completed.durationS, 30)
  assert.equal(completed.remainingS, 0)
  assert.equal(completed.sampleCount, 2)
})

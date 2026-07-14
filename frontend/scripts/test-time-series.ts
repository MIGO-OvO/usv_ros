import assert from 'node:assert/strict'
import test from 'node:test'
import { RingBuffer } from '../src/lib/time-series/ring-buffer.ts'
import { minMaxDownsample } from '../src/lib/time-series/min-max-downsample.ts'

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

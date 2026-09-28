// Local Vite + synthetic state only. Never sends commands to ROS/hardware.
import assert from 'node:assert/strict'
import { mkdir } from 'node:fs/promises'
import { resolve } from 'node:path'
import { pathToFileURL } from 'node:url'

const { chromium } = await import(process.env.USV_PLAYWRIGHT_PATH
  ? pathToFileURL(resolve(process.env.USV_PLAYWRIGHT_PATH)).href : 'playwright')
const browser = await chromium.launch({ channel: 'msedge', headless: true })
const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } })
const errors = []
page.on('pageerror', error => errors.push(error.message))
const output = resolve('../.omo/monitor-layout')
await mkdir(output, { recursive: true })
const pos = { available: true, latitude: 25.4219396, longitude: 110.2543123, altitude: 34.7, age_s: 0.5, received_at: Date.now() / 1000, stale: false }
const gps = {
  freshness_threshold_s: 2,
  overall: { state: 'raw_missing', severity: 'orange', message: 'GPS原始数据缺失 · 未收到 GPS_RAW_INT' },
  fcu: { heartbeat_valid: true, mavros_connected: true, system_id: 1, component_id: 1 },
  gps_config: {}, gps_raw: { ...pos, available: false, valid: false },
  global_position: pos, navsat: { ...pos, valid_navsat_fix: false, status: -1 },
  sampling_position: { valid: false, reason: 'gps_no_fix' }, autopilot_version: {},
  warnings: ['融合位置不能证明原始 GNSS Fix。'], statustext: [],
}
const commands = []
await page.route('**/socket.io/**', route => route.abort())
await page.route('**/api/**', async route => {
  const url = new URL(route.request().url())
  if (route.request().method() === 'POST') commands.push(url.pathname)
  await route.fulfill({ json: url.pathname === '/api/gps/diagnostics' ? gps : { success: true, data: { nodes: [{ name: 'mavlink-routerd', alive: true }] } } })
})
try {
  await page.goto('http://127.0.0.1:5178')
  await page.getByText('定位降级', { exact: true }).first().waitFor()
  await page.evaluate(async () => {
    const { useAppStore } = await import('/src/store.ts')
    const history = useAppStore.getState().voltageHistory
    const now = Date.now()
    history.appendBatch(Array.from({ length: 1000 }, (_, i) => ({ seq: i, receivedAtMs: now - (999 - i) * 33, sourceTimestampMs: i * 33, voltage: 0.932 + Math.sin(i / 20) * 0.02, absorbance: 0.108 + Math.sin(i / 20) * 0.001 })))
    useAppStore.setState({ connected: true, pumpConnected: true, currentVoltage: 0.932, currentAbsorbance: 0.108, spectrometerStatus: 'acquiring', spectrometerBaselineSet: true, currentReferenceVoltage: 1.195, voltageHistoryRevision: 1,
      systemHealth: { health: { level: 'ok', summary: '正常' }, ros_nodes: ['web', 'pump', 'mavros', 'bridge', 'health'].map(name => ({ name, alive: true })), jetson: { temperature_c: 50, cpu_percent: 9, memory_percent: 47 }, detector: { temperature_c: 68, heap_percent_free: 87, spectrometer: { crc_error: 0, duplicate: 0 } } } })
  })
  await page.getByRole('tab', { name: '分光计电压', exact: true }).waitFor()
  assert.equal(await page.locator('.monitor-diagnostics details[open]').count(), 0)
  assert.equal(await page.locator('canvas:visible').count(), 1)
  const before = await page.locator('#chart-panel').boundingBox()
  await page.getByRole('tab', { name: '吸光度', exact: true }).click()
  assert.equal(await page.locator('canvas:visible').count(), 1)
  assert.equal((await page.locator('#chart-panel').boundingBox()).height, before.height)
  assert.equal(await page.evaluate(async () => (await import('/src/store.ts')).useAppStore.getState().voltageHistory.length), 1000)
  await page.getByRole('button', { name: '数据', exact: true }).click()
  assert.equal(await page.locator('tbody tr').count(), 500)
  await page.getByRole('tab', { name: '分光计电压', exact: true }).click()
  await page.getByRole('button', { name: '暂停视图', exact: true }).click()
  await page.evaluate(async () => {
    const { useAppStore } = await import('/src/store.ts')
    const state = useAppStore.getState()
    state.voltageHistory.appendBatch([{ seq: 1000, receivedAtMs: Date.now(), sourceTimestampMs: 33000, voltage: 0.95, absorbance: 0.12 }])
    useAppStore.setState({ voltageHistoryRevision: 2 })
  })
  await page.getByText('原始 1000/1001 点', { exact: true }).waitFor()
  await page.getByRole('button', { name: '回到实时', exact: true }).click()
  await page.getByText('原始 1001/1001 点', { exact: true }).waitFor()
  for (const width of [1440, 820, 390, 320]) {
    await page.setViewportSize({ width, height: 1000 })
    await page.waitForFunction(() => Math.abs(parseFloat(getComputedStyle(document.querySelector('main')).paddingLeft) - (innerWidth >= 768 ? 256 : 0)) < 0.1)
    await page.evaluate(() => window.scrollTo(0, 0))
    await page.screenshot({ path: resolve(output, `monitor-${width}.png`), fullPage: true })
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true, `overflow at ${width}`)
    const chart = await page.locator('#chart-panel').boundingBox()
    const section = await page.locator('[aria-labelledby="realtime-title"]').boundingBox()
    assert.ok(chart.width >= section.width - 4, `full width chart at ${width}`)
    await page.getByRole('tab', { name: '吸光度', exact: true }).click()
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true, `table overflow at ${width}`)
    await page.getByRole('tab', { name: '分光计电压', exact: true }).click()
    await page.evaluate(() => window.scrollTo(0, document.body.scrollHeight))
    assert.ok((await page.locator('.monitor-page > header').boundingBox()).y >= -1)
  }
  const download = page.waitForEvent('download')
  await page.getByRole('button', { name: '导出当前浏览器缓存中的分光计电压数据为 CSV' }).click()
  assert.match((await download).suggestedFilename(), /\.csv$/)
  await page.getByRole('button', { name: '开始分光', exact: true }).click()
  await page.getByRole('button', { name: '停止分光', exact: true }).click()
  assert.deepEqual(commands, ['/api/spectrometer/start', '/api/spectrometer/stop'])
  await page.getByRole('link', { name: '详细诊断', exact: true }).click()
  assert.equal(await page.locator('#gps-diagnostics').getAttribute('open'), '')
  while (await page.locator('.monitor-diagnostics > details:not([open]) > summary').count()) {
    await page.locator('.monitor-diagnostics > details:not([open]) > summary').first().click()
  }
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true, 'expanded diagnostics overflow')
  await page.screenshot({ path: resolve(output, 'monitor-mobile-diagnostics.png'), fullPage: true })
  await page.setViewportSize({ width: 1440, height: 1000 })
  await page.evaluate(() => document.documentElement.classList.add('dark'))
  await page.screenshot({ path: resolve(output, 'monitor-dark.png'), fullPage: true })
  await page.getByRole('button', { name: '清空分光计电压图表及历史数据' }).click()
  assert.equal(await page.evaluate(async () => (await import('/src/store.ts')).useAppStore.getState().voltageHistory.length), 0)
  await page.getByRole('button', { name: '开始获取', exact: true }).click()
  await page.waitForFunction(() => document.querySelector('header button')?.disabled === true)
  await page.getByRole('button', { name: '取消', exact: true }).click()
  await page.waitForFunction(() => document.querySelector('header button')?.disabled === false)
  gps.global_position = { ...pos, age_s: 11, stale: true }
  await page.getByText('定位不可用', { exact: true }).first().waitFor()
  gps.gps_raw = { ...pos, valid: true, fix_type: 3 }
  gps.navsat = { ...pos, valid_navsat_fix: true, status: 0 }
  gps.sampling_position = { valid: true, reason: null }
  await page.getByText('定位正常', { exact: true }).first().waitFor()
  await page.evaluate(async () => (await import('/src/store.ts')).useAppStore.setState({ connected: false }))
  await page.getByText('连接已断开', { exact: true }).waitFor()
  assert.deepEqual(errors, [])
  console.log('PASS: desktop/tablet/mobile/320px, sticky, full-width tabs, retained history, pause/live, CSV, clear, mocked controls, collapsed/expanded diagnostics, dark mode; no page errors.')
} finally { await browser.close() }

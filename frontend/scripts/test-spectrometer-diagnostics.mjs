import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { createRequire } from 'node:module'
import test from 'node:test'
import vm from 'node:vm'
import ts from 'typescript'
import React from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { RingBuffer } from '../src/lib/time-series/ring-buffer.ts'

const require = createRequire(import.meta.url)
function load(path, mocks) {
  const source = readFileSync(new URL(path, import.meta.url), 'utf8')
  const js = ts.transpileModule(source, { compilerOptions: {
    module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX,
  } }).outputText
  const exports = {}
  vm.runInNewContext(js, { exports, require: name => mocks[name] ?? require(name),
    console, setTimeout, clearTimeout, Date, fetch })
  return exports
}

function fixture() {
  const handlers = {}
  const socket = { on: (name, callback) => { handlers[name] = callback }, disconnect() {} }
  const { useAppStore } = load('../src/store.ts', {
    'socket.io-client': { io: () => socket }, '@/lib/time-series/ring-buffer': { RingBuffer },
  })
  useAppStore.getState().connect()
  handlers.connect()
  const wrapper = ({ children }) => React.createElement('div', null, children)
  const { SpectrometerDiagnosticsCard } = load('../src/components/spectrometer-diagnostics-card.tsx', {
    '@/store': { useAppStore: () => useAppStore.getState() },
    '@/components/ui/card': { Card: wrapper, CardHeader: wrapper, CardTitle: wrapper, CardContent: wrapper },
  })
  return { handlers, store: useAppStore, render: () => renderToStaticMarkup(React.createElement(SpectrometerDiagnosticsCard)) }
}

test('real status handler preserves serial false independently of pump_connected and shows I2CMAP failure', () => {
  const { handlers, store, render } = fixture()
  handlers.status({ pump_connected: true, serial_connected: false,
    spectrometer_config_state: 'failed', spectrometer_state: 'stopped',
    spectrometer_txn_phase: 'i2c_map_failed', spectrometer_last_txn_error: 'I2CMAP mismatch: SPEC=7' })
  assert.equal(store.getState().serialConnected, false)
  assert.equal(store.getState().spectrometerLastTxnError, 'I2CMAP mismatch: SPEC=7')
  assert.equal(store.getState().automationTerminalReason, null)
  const html = render()
  assert.match(html, /串口连接.*已断开/)
  assert.match(html, /I2C \/ ADS 配置.*配置失败/)
  assert.match(html, /分光采集.*已停止/)
  assert.match(html, /I2CMAP mismatch: SPEC=7/)
})

test('ADSCFG timeout clears only on a later successful snapshot; recovery attempts stay visible', () => {
  const { handlers, store, render } = fixture()
  handlers.status({ serial_connected: true, spectrometer_config_state: 'failed',
    spectrometer_txn_phase: 'ads_config_failed', spectrometer_last_txn_error: 'ADSCFG: timeout' })
  assert.match(render(), /ADSCFG: timeout/)
  handlers.status({ serial_connected: true, spectrometer_config_state: 'ready', spectrometer_state: 'acquiring',
    spectrometer_txn_phase: 'running', spectrometer_last_txn_error: null, spectrometer_txn_attempt: 2,
    spectrometer_retry_errors: [{ attempt: 1, phase: 'ads_config_failed', error: 'ADSCFG: timeout' }] })
  assert.equal(store.getState().spectrometerLastTxnError, null)
  assert.match(render(), /配置已验证/)
  assert.match(render(), /正在采集/)
  assert.match(render(), /尝试 2\/3/)
  assert.match(render(), /本次事务重试记录/)
})

test('missing fields and websocket disconnect cannot claim a ready serial session', () => {
  const { handlers, store, render } = fixture()
  handlers.status({ pump_connected: true })
  assert.equal(store.getState().serialConnected, null)
  assert.match(render(), /串口连接.*未知/)
  handlers.status({ serial_connected: true, spectrometer_config_state: 'ready' })
  handlers.disconnect()
  assert.equal(store.getState().serialConnected, null)
  assert.match(render(), /未知（Web 离线）/)
  assert.doesNotMatch(render(), /配置已验证/)
})

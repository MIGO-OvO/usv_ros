import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'
import vm from 'node:vm'
import ts from 'typescript'

// Execute the real Data component with a tiny hook/JSX harness. No DOM/test
// dependencies: requests, cleanup and rendered state are exercised, not regexes.
const source = await readFile(new URL('../src/pages/Data.tsx', import.meta.url), 'utf8')
const compiled = ts.transpileModule(source, {
  fileName: 'Data.tsx',
  compilerOptions: { module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX, target: ts.ScriptTarget.ES2022 },
}).outputText

function harness() {
  const hooks = [], effects = [], requests = []
  let cursor = 0, dirty = true, tree
  const react = {
    useState(initial) {
      const i = cursor++
      hooks[i] ??= { value: initial }
      return [hooks[i].value, value => {
        const next = typeof value === 'function' ? value(hooks[i].value) : value
        if (!Object.is(next, hooks[i].value)) { hooks[i].value = next; dirty = true }
      }]
    },
    useRef(initial) {
      const i = cursor++
      hooks[i] ??= { current: initial }
      return hooks[i]
    },
    useMemo(fn) { return fn() },
    useEffect(fn, deps) {
      const i = cursor++
      const previous = hooks[i]
      if (!previous || deps.some((value, index) => !Object.is(value, previous.deps[index]))) {
        hooks[i] = { deps, cleanup: previous?.cleanup }
        effects.push(() => { hooks[i].cleanup?.(); hooks[i].cleanup = fn() })
      }
    },
  }
  const exports = {}
  const component = new vm.Script(compiled)
  component.runInNewContext({
    exports, AbortController, console,
    fetch(url, init) {
      return new Promise(resolve => requests.push({ url, signal: init?.signal,
        respond(data) { resolve({ ok: true, json: async () => ({ success: true, data }) }) } }))
    },
    require(name) {
      if (name === 'react') return react
      if (name === 'react/jsx-runtime') return { jsx: (type, props) => ({ type, props }), jsxs: (type, props) => ({ type, props }) }
      if (name.includes('use-confirm')) return { useConfirm: () => async () => true }
      if (name.includes('utils')) return { cn: (...args) => args.join(' ') }
      return new Proxy({}, { get: (_, name) => String(name) })
    },
  })
  const api = {
    requests,
    async flush() {
      for (let i = 0; i < 20; i++) {
        await new Promise(resolve => setImmediate(resolve))
        if (dirty) { dirty = false; cursor = 0; tree = exports.default(); effects.splice(0).forEach(fn => fn()) }
      }
    },
    nodes() {
      const result = []
      function walk(value) {
        if (Array.isArray(value)) value.forEach(walk)
        else if (value && typeof value === 'object') { result.push(value); walk(value.props?.children) }
      }
      walk(tree)
      return result
    },
    text() { return JSON.stringify(tree) },
    clickSample(id = 'sample') {
      api.nodes().find(node => node.type === 'button' && node.props.children?.[1]?.props?.children === id).props.onClick()
    },
    clickMission(name) {
      api.nodes().find(node => node.type === 'button' && node.props.children?.[0]?.props?.children === name).props.onClick()
    },
  }
  return api
}

async function openTask() {
  const app = harness()
  await app.flush()
  app.requests[0].respond([{ id: 'history', name: 'History', start_time: '2026-01-01', point_count: 3 },
    { id: 'other', name: 'Other', start_time: '2026-01-01', point_count: 0 }])
  await app.flush()
  return app
}

const overview = { data_points: [{ voltage: 1 }], point_count: 3, samples: [{ sample_id: 'sample' }] }

test('task first paint is loading; overview alone does not request raw; click requests exactly two endpoints', async () => {
  const app = await openTask()
  assert.match(app.text(), /正在加载任务趋势与采样窗口/)
  assert.doesNotMatch(app.text(), /该任务暂无数据点/)
  assert.equal(app.requests.length, 2) // list + single overview
  assert.equal(app.requests[1].url, '/api/data/mission/history?view=data-center')
  app.requests[1].respond(overview)
  await app.flush()
  assert.equal(app.requests.length, 2)
  assert.match(app.text(), /点击采样窗口后加载原始曲线/)
  app.clickSample()
  await app.flush()
  assert.equal(app.requests.length, 4)
  assert.match(app.requests[2].url, /\/sample\/sample$/)
  assert.match(app.requests[3].url, /voltage-series/)
  assert.match(app.text(), /正在加载所选窗口/)
})

test('switching mission aborts raw and ignores even an uncooperative late response', async () => {
  const app = await openTask()
  app.requests[1].respond(overview)
  await app.flush()
  app.clickSample()
  await app.flush()
  const detail = app.requests[2], raw = app.requests[3]
  app.clickMission('Other')
  await app.flush()
  assert.equal(raw.signal.aborted, true)
  detail.respond({ sample_id: 'STALE_DETAIL' })
  raw.respond({ samples: [{ voltage: 2 }], raw_count: 1, returned_count: 1 })
  app.requests[4].respond({ data_points: [], samples: [], point_count: 0 })
  await app.flush()
  assert.doesNotMatch(app.text(), /STALE_DETAIL/)
  assert.match(app.text(), /该任务暂无数据点/)
  assert.equal(app.requests.length, 5)
})

test('manual save retains the chosen window and does not reload raw', async () => {
  const app = await openTask()
  app.requests[1].respond({ ...overview, samples: [{ sample_id: 'sample' }, { sample_id: 'second' }] })
  await app.flush()
  app.clickSample('second')
  await app.flush()
  app.requests[2].respond({ sample_id: 'second', manual_result: {} })
  app.requests[3].respond({ samples: [], raw_count: 0, returned_count: 0 })
  await app.flush()
  app.nodes().find(node => node.type === 'Button' && node.props.children?.[1] === '保存结果').props.onClick()
  await app.flush()
  assert.match(app.requests[4].url, /\/sample\/second\/manual-result$/)
  app.requests[4].respond({ sample_id: 'second', manual_result: { concentration: 3, status: 'recorded' } })
  await app.flush()
  assert.equal(app.requests.length, 5)
  assert.match(app.text(), /已记录/)
})

test('late overview cannot replace newly selected task', async () => {
  const app = await openTask()
  const old = app.requests[1]
  app.clickMission('Other')
  await app.flush()
  assert.equal(old.signal.aborted, true)
  app.requests[2].respond({ samples: [], data_points: [], point_count: 0 })
  await app.flush()
  old.respond({ ...overview, samples: [{ sample_id: 'STALE_WINDOW' }] })
  await app.flush()
  assert.doesNotMatch(app.text(), /STALE_WINDOW/)
  assert.match(app.text(), /该任务暂无数据点/)
})

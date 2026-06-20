import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'
import ts from 'typescript'

const hookUrl = new URL('../src/hooks/use-lab-map.ts', import.meta.url)
const adapterUrl = new URL('../src/lib/lab-coordinate-adapter.ts', import.meta.url)
const mapUrl = new URL('../src/pages/Map.tsx', import.meta.url)

function containsEventLatLng(node) {
  let found = false
  function visit(child) {
    if (
      ts.isPropertyAccessExpression(child)
      && child.name.text === 'latlng'
      && ts.isIdentifier(child.expression)
      && child.expression.text === 'event'
    ) {
      found = true
      return
    }
    ts.forEachChild(child, visit)
  }
  visit(node)
  return found
}

async function loadAdapter() {
  const source = await readFile(adapterUrl, 'utf8')
  const transpiled = ts.transpileModule(source, {
    compilerOptions: {
      module: ts.ModuleKind.ESNext,
      target: ts.ScriptTarget.ES2022,
      verbatimModuleSyntax: true,
    },
    fileName: adapterUrl.pathname,
  })
  const encoded = Buffer.from(transpiled.outputText).toString('base64')
  return import(`data:text/javascript;base64,${encoded}`)
}

test('map clicks never persist event.latlng as bare coordinates', async () => {
  // Given: the Lab map hook source.
  const source = await readFile(hookUrl, 'utf8')
  const sourceFile = ts.createSourceFile(
    hookUrl.pathname,
    source,
    ts.ScriptTarget.Latest,
    true,
    ts.ScriptKind.TS,
  )
  const violations = []

  // When: persisted coordinate-shaped object properties are inspected.
  function visit(node) {
    if (
      ts.isPropertyAssignment(node)
      && (node.name.getText(sourceFile) === 'lat' || node.name.getText(sourceFile) === 'lng')
      && containsEventLatLng(node.initializer)
    ) {
      violations.push(node.getText(sourceFile))
    }
    ts.forEachChild(node, visit)
  }
  visit(sourceFile)

  // Then: click coordinates must cross the API boundary through the adapter.
  assert.deepEqual(violations, [])
})

test('coordinate adapter labels clicks and draws only saved GCJ-02 values', async () => {
  // Given: a map click and a normalized schema-v2 coordinate pair.
  const adapter = await loadAdapter()
  const click = { lat: 25.314167, lng: 110.412778 }
  const saved = {
    coordinate_schema_version: 2,
    wgs84: { lat: 25.3225917645, lng: 110.3977401264 },
    gcj02: click,
  }
  const config = {
    sim: { start: saved, start_lat: click.lat, start_lng: click.lng },
    mission: { waypoints: [{ ...saved, seq: 0 }], center: saved },
    pollution: { mode: 'center', source: null },
    water_area: { enabled: true, polygon: [saved] },
  }

  // When: request and drawing values are adapted.
  const request = adapter.gcj02Input(click)
  const drawing = adapter.gcj02ForDrawing(saved)
  const startWrite = adapter.configWriteWithGcj02Start(config, click)
  const missionWrite = adapter.missionWriteWithGcj02Waypoint(config.mission, click)
  const sourceWrite = adapter.configWriteWithGcj02PollutionSource(config, click)
  const waterWrite = adapter.waterAreaWriteWithGcj02Vertex(config.water_area, click)

  // Then: the request is explicitly labeled and drawing selects saved GCJ-02.
  assert.deepEqual(request, { input_crs: 'GCJ02', gcj02: click })
  assert.deepEqual(startWrite.sim.start, request)
  assert.deepEqual(missionWrite.waypoints.at(-1), { ...request, seq: 1 })
  assert.deepEqual(sourceWrite.pollution.source, request)
  assert.deepEqual(waterWrite.polygon.at(-1), request)
  assert.equal('wgs84' in startWrite.sim.start, false)
  assert.deepEqual(drawing, click)
  assert.notDeepEqual(drawing, saved.wgs84)
})

test('map page does not keep frontend WGS-84 GCJ-02 conversion helpers', async () => {
  // Given: the production map page source.
  const source = await readFile(mapUrl, 'utf8')
  const bannedFragments = [
    'outOfChina',
    'transformLat',
    'transformLng',
    'wgs84ToGcj02',
    'gcj02ToWgs84',
    '6378245.0',
    '0.006693421622965943',
  ]

  // When: the source is scanned for CRS conversion implementation details.
  const violations = bannedFragments.filter((fragment) => source.includes(fragment))

  // Then: the frontend must remain a GCJ-02 display/input consumer only.
  assert.deepEqual(violations, [])
})

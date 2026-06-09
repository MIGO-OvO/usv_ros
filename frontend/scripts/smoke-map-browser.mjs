import { existsSync, mkdirSync, readFileSync, rmSync, writeFileSync } from 'node:fs'
import { dirname, resolve } from 'node:path'
import { spawn } from 'node:child_process'
import { once } from 'node:events'
import net from 'node:net'
import os from 'node:os'

const frontendRoot = resolve(import.meta.dirname, '..')
const repoRoot = resolve(frontendRoot, '..')
const workspaceRoot = resolve(repoRoot, '..', '..')
const evidenceRoot = resolve(workspaceRoot, '.omo/evidence')
const screenshotPath = process.env.USV_MAP_SMOKE_SCREENSHOT || resolve(evidenceRoot, 'task-T18-pollution-web-map.png')
const domPath = process.env.USV_MAP_SMOKE_DOM || resolve(evidenceRoot, 'task-T18-pollution-web-map-dom.html')
const distIndex = resolve(repoRoot, 'static/dist/index.html')

const requiredDomTokens = [
  '历史任务',
  'COD',
  'fixture-calibration',
  '历史污染面已生成',
  'GeoJSON',
  'Surface',
  '低质量/排除原因',
  'IDW size',
  '缺少 GPS',
  '/api/data/mission/',
  'download=true',
]

function assert(condition, message) {
  if (!condition) {
    throw new Error(message)
  }
}

function findChrome() {
  const envPath = process.env.CHROME_PATH || process.env.BROWSER_PATH
  const candidates = [
    envPath,
    'C:/Program Files/Google/Chrome/Application/chrome.exe',
    'C:/Program Files (x86)/Google/Chrome/Application/chrome.exe',
    'C:/Program Files/Microsoft/Edge/Application/msedge.exe',
    'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe',
    '/usr/bin/google-chrome',
    '/usr/bin/google-chrome-stable',
    '/usr/bin/chromium',
    '/usr/bin/chromium-browser',
    '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
  ].filter(Boolean)
  const chrome = candidates.find((candidate) => existsSync(candidate))
  assert(chrome, 'Map browser smoke failed: Chrome/Edge executable not found')
  return chrome
}

async function findFreePort() {
  const server = net.createServer()
  server.listen(0, '127.0.0.1')
  await once(server, 'listening')
  const address = server.address()
  const port = typeof address === 'object' && address ? address.port : 0
  server.close()
  await once(server, 'close')
  assert(port > 0, 'Map browser smoke failed: no free port found')
  return port
}

function pythonCandidates() {
  return [
    process.env.PYTHON,
    process.env.PYTHON3,
    'python',
    'python3',
  ].filter(Boolean)
}

function startFixtureServer(port) {
  const pythonCode = String.raw`
import importlib.util
import json
import os
import sys
import tempfile
from pathlib import Path

repo_root = Path(sys.argv[1]).resolve()
port = int(sys.argv[2])
os.environ["USV_WEB_UI"] = "dist"
sys.path.insert(0, str(repo_root / "tests"))

spec = importlib.util.spec_from_file_location("web_config_server_browser_smoke", repo_root / "scripts" / "web_config_server.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)

from pollution_map_fixtures import create_pollution_map_fixture

tmpdir = tempfile.TemporaryDirectory(prefix="usv-map-browser-smoke-")
fixture = create_pollution_map_fixture(module, tmpdir.name)
server = module.WebConfigServer(standalone=True)
server.host = "127.0.0.1"
server.port = port
server.data_manager = module.MissionDataManager(fixture["missions_dir"])
mission_msg = module.String()
mission_msg.data = "SURVEYING:5.0"
gate_msg = module.String()
gate_msg.data = "survey_gate_skipped:gps_stale"
server._mission_status_cb(mission_msg)
server._trigger_status_cb(gate_msg)
print(json.dumps({"ready": True, "mission_id": fixture["mission_id"], "port": port}), flush=True)
server.app.run(host="127.0.0.1", port=port, debug=False, use_reloader=False)
`

  let lastError = null
  for (const python of pythonCandidates()) {
    try {
      const proc = spawn(python, ['-u', '-c', pythonCode, repoRoot, String(port)], {
        cwd: repoRoot,
        env: { ...process.env, USV_WEB_UI: 'dist' },
        stdio: ['ignore', 'pipe', 'pipe'],
      })
      proc.stdout.setEncoding('utf8')
      proc.stderr.setEncoding('utf8')
      return { proc, python }
    } catch (error) {
      lastError = error
    }
  }
  throw new Error(`Map browser smoke failed: could not start Python (${lastError?.message || 'unknown'})`)
}

async function waitForReady(serverProcess, timeoutMs = 30000) {
  let buffer = ''
  let stderr = ''
  serverProcess.proc.stderr.on('data', (chunk) => {
    stderr += chunk
  })

  return await new Promise((resolveReady, rejectReady) => {
    const timer = setTimeout(() => {
      rejectReady(new Error(`Map browser smoke failed: fixture server did not become ready. stderr=${stderr}`))
    }, timeoutMs)
    serverProcess.proc.stdout.on('data', (chunk) => {
      buffer += chunk
      for (const line of buffer.split(/\r?\n/)) {
        if (!line.trim().startsWith('{')) continue
        try {
          const payload = JSON.parse(line)
          if (payload.ready) {
            clearTimeout(timer)
            resolveReady(payload)
          }
        } catch {
          // Keep waiting for the JSON readiness line.
        }
      }
    })
    serverProcess.proc.on('exit', (code) => {
      clearTimeout(timer)
      rejectReady(new Error(`Map browser smoke failed: fixture server exited with ${code}. stderr=${stderr}`))
    })
  })
}

async function runChrome(chrome, args, timeoutMs = 20000) {
  const proc = spawn(chrome, args, { stdio: ['ignore', 'pipe', 'pipe'] })
  let stdout = ''
  let stderr = ''
  proc.stdout.setEncoding('utf8')
  proc.stderr.setEncoding('utf8')
  proc.stdout.on('data', (chunk) => {
    stdout += chunk
  })
  proc.stderr.on('data', (chunk) => {
    stderr += chunk
  })

  const exit = await new Promise((resolveExit, rejectExit) => {
    const timer = setTimeout(() => {
      proc.kill()
      rejectExit(new Error(`Chrome timed out. stderr=${stderr}`))
    }, timeoutMs)
    proc.on('exit', (code) => {
      clearTimeout(timer)
      resolveExit(code)
    })
  })
  assert(exit === 0, `Chrome exited with ${exit}. stderr=${stderr}`)
  return { stdout, stderr }
}

async function main() {
  assert(existsSync(distIndex), `Map browser smoke failed: missing built frontend ${distIndex}`)
  mkdirSync(dirname(screenshotPath), { recursive: true })
  const chrome = findChrome()
  const port = await findFreePort()
  const profileDir = resolve(os.tmpdir(), `usv-map-browser-smoke-${Date.now()}`)
  const serverProcess = startFixtureServer(port)

  try {
    const ready = await waitForReady(serverProcess)
    const url = `http://127.0.0.1:${port}/map?mode=history`
    const commonChromeArgs = [
      '--headless=new',
      '--disable-gpu',
      '--disable-dev-shm-usage',
      '--disable-extensions',
      '--hide-scrollbars',
      '--no-first-run',
      '--no-default-browser-check',
      `--user-data-dir=${profileDir}`,
      '--window-size=1600,900',
      '--virtual-time-budget=12000',
      '--run-all-compositor-stages-before-draw',
    ]

    const domRun = await runChrome(chrome, [...commonChromeArgs, '--dump-dom', url])
    writeFileSync(domPath, domRun.stdout, 'utf8')
    for (const token of requiredDomTokens) {
      assert(domRun.stdout.includes(token), `Map browser smoke failed: DOM missing ${token}`)
    }
    assert(domRun.stdout.includes(ready.mission_id), 'Map browser smoke failed: DOM missing fixture mission id')
    assert(domRun.stdout.includes('<canvas'), 'Map browser smoke failed: Leaflet heat/canvas layer not rendered')

    await runChrome(chrome, [
      ...commonChromeArgs,
      `--screenshot=${screenshotPath}`,
      url,
    ])

    const screenshot = readFileSync(screenshotPath)
    assert(screenshot.length > 5000, 'Map browser smoke failed: screenshot is too small')
    assert(screenshot[0] === 0x89 && screenshot[1] === 0x50 && screenshot[2] === 0x4e && screenshot[3] === 0x47, 'Map browser smoke failed: screenshot is not a PNG')

    console.log(JSON.stringify({
      ok: true,
      mission_id: ready.mission_id,
      url,
      screenshot: screenshotPath,
      dom: domPath,
      viewport: '1600x900',
      chrome,
    }, null, 2))
    console.log('pollution map browser smoke ok')
  } finally {
    serverProcess.proc.kill()
    rmSync(profileDir, { recursive: true, force: true })
  }
}

main().catch((error) => {
  console.error(error.stack || String(error))
  process.exit(1)
})

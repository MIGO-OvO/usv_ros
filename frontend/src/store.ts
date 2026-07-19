import { create } from 'zustand'
import { io, Socket } from 'socket.io-client'
import { RingBuffer } from '@/lib/time-series/ring-buffer'

interface PumpAngles {
  X: number
  Y: number
  Z: number
  A: number
}

interface AngleTelemetry {
  angles: PumpAngles
  raw_angles: PumpAngles
  source: string
  received_at: number | null
  age_ms: number | null
  detector_angle_age_ms: number | null
  channel_age_ms?: Partial<PumpAngles> | null
  stale: boolean
  valid: boolean
}

interface InjectionPumpStatus {
  enabled: boolean
  speed: number
  last_response: string
  last_error: string
}

interface LogEntry {
  timestamp: string
  message: string
  level: string
}

export interface VoltagePoint {
  seq: number
  sourceTimestampMs: number
  receivedAtMs: number
  voltage: number
  absorbance: number | null
  rawCode?: number
  valid: boolean
}

interface SpectrometerRawPayload {
  valid?: boolean
  raw_code?: number
  reference_voltage?: number
  baseline_voltage?: number
  baseline_set?: boolean
  seq?: number
  timestamp_ms?: number
  source_timestamp_ms?: number
  received_at_ms?: number
}

interface VoltagePayload {
  value?: number
  absorbance?: number | null
  status?: string
  reference_voltage?: number
  baseline_voltage?: number
  baseline_set?: boolean
  raw?: SpectrometerRawPayload
  sample?: boolean
}

interface VoltageBatchSample {
  seq: number
  source_timestamp_ms: number
  received_at_ms: number
  voltage: number
  absorbance: number | null
  raw_code?: number
  valid: boolean
  status?: string
  reference_voltage?: number
  baseline_voltage?: number
  baseline_set?: boolean
}

interface VoltageBatchPayload {
  first_seq: number
  last_seq: number
  sent_at_ms?: number
  samples: VoltageBatchSample[]
  dropped_for_ui?: number
}

interface MavrosState {
  connected: boolean
  armed: boolean
  mode: string
}

interface BridgeDiag {
  sysid: number
  compid: number
  mavros_connected: boolean
  tx_total: number
  tx_named_value: number
  tx_heartbeat: number
  pub_errors: number
  mavros_drops: number
  uptime_s: number
  rate_hz: number
  router_url: string
}

interface RadioStatus {
  rssi: number
  remrssi: number
  noise: number
  remnoise: number
  rxerrors: number
  fixed: number
  txbuf: number
}

interface SystemHealth {
  ts?: string
  jetson?: {
    cpu_percent?: number | null
    memory_percent?: number | null
    memory_used_mb?: number | null
    memory_total_mb?: number | null
    temperature_c?: number | null
    uptime_s?: number | null
  }
  detector?: {
    online?: boolean
    temperature_c?: number | null
    heap_free?: number | null
    heap_total?: number | null
    heap_percent_free?: number | null
    uptime_s?: number | null
    task_count?: number | null
    task_stack_hwm?: Record<string, number>
  }
  ros_nodes?: { name: string; alive: boolean }[]
  health?: {
    code?: number
    level?: string
    summary?: string
  }
}

interface ManualStatus {
  enabled: boolean
  automation_active: boolean
  spectrometer_active: boolean
}

interface ControlEvent {
  command_id: string
  source: string
  action: string
  state: string
  message: string
  timestamp: number
  elapsed_ms?: number
  result?: Record<string, unknown>
}

interface StatusPayload {
  pump_connected?: boolean
  automation_running?: boolean
  mission_status?: string
  spectrometer_status?: string
}

const MAX_HISTORY_POINTS = 20_000
const FAST_TELEMETRY_INTERVAL_MS = 100

function createThrottledCommit<T>(intervalMs: number, commit: (data: T) => void) {
  let latest: T | null = null
  let timer: ReturnType<typeof setTimeout> | null = null
  let lastCommitAt = 0

  const flush = () => {
    timer = null
    if (latest === null) return
    const data = latest
    latest = null
    lastCommitAt = Date.now()
    commit(data)
  }

  return {
    push(data: T) {
      latest = data
      const delay = Math.max(0, intervalMs - (Date.now() - lastCommitAt))
      if (delay === 0) {
        if (timer) {
          clearTimeout(timer)
          timer = null
        }
        flush()
        return
      }
      if (!timer) {
        timer = setTimeout(flush, delay)
      }
    },
    cancel() {
      if (timer) clearTimeout(timer)
      timer = null
      latest = null
    },
  }
}

interface AppState {
  socket: Socket | null
  connected: boolean
  pumpConnected: boolean
  automationRunning: boolean
  missionStatus: string
  pumpAngles: PumpAngles
  rawAngles: PumpAngles
  angleTelemetry: AngleTelemetry
  currentVoltage: number
  currentAbsorbance: number
  currentReferenceVoltage: number | null
  currentBaselineVoltage: number
  spectrometerBaselineSet: boolean
  spectrometerStatus: string
  voltageHistory: RingBuffer<VoltagePoint>
  voltageHistoryRevision: number
  voltageBatchSupported: boolean
  voltageSequenceGaps: number
  voltageUiDropped: number
  voltageServerBacklogMs: number
  injectionPump: InjectionPumpStatus
  logs: LogEntry[]
  mavrosState: MavrosState
  bridgeDiag: BridgeDiag | null
  radioStatus: RadioStatus | null
  systemHealth: SystemHealth | null
  systemHealthHistory: SystemHealth[]
  manualStatus: ManualStatus
  controlEvents: ControlEvent[]

  connect: () => void
  disconnect: () => void
  refreshInjectionPumpStatus: () => Promise<void>
  setInjectionPumpSpeed: (speed: number) => Promise<{ success: boolean; message: string }>
  turnInjectionPumpOn: (speed?: number) => Promise<{ success: boolean; message: string }>
  turnInjectionPumpOff: () => Promise<{ success: boolean; message: string }>
  refreshManualStatus: () => Promise<void>
  setManualMode: (enabled: boolean) => Promise<{ success: boolean; message: string }>
  sendManualPumpStep: (payload: {
    axis: string
    direction: string
    speed_rpm: number
    angle_deg: number
    continuous: boolean
  }) => Promise<{ success: boolean; message: string }>
  stopManualPumps: () => Promise<{ success: boolean; message: string }>
}

const DEFAULT_INJECTION_PUMP_STATUS: InjectionPumpStatus = {
  enabled: false,
  speed: 0,
  last_response: '',
  last_error: '',
}

const DEFAULT_MANUAL_STATUS: ManualStatus = {
  enabled: false,
  automation_active: false,
  spectrometer_active: false,
}

const DEFAULT_ANGLES: PumpAngles = { X: 0, Y: 0, Z: 0, A: 0 }

const DEFAULT_ANGLE_TELEMETRY: AngleTelemetry = {
  angles: DEFAULT_ANGLES,
  raw_angles: DEFAULT_ANGLES,
  source: 'not_received',
  received_at: null,
  age_ms: null,
  detector_angle_age_ms: null,
  stale: true,
  valid: false,
}

export const useAppStore = create<AppState>((set, get) => ({
  socket: null,
  connected: false,
  pumpConnected: false,
  automationRunning: false,
  missionStatus: 'IDLE',
  pumpAngles: DEFAULT_ANGLES,
  rawAngles: DEFAULT_ANGLES,
  angleTelemetry: DEFAULT_ANGLE_TELEMETRY,
  currentVoltage: 0,
  currentAbsorbance: 0,
  currentReferenceVoltage: null,
  currentBaselineVoltage: 0,
  spectrometerBaselineSet: false,
  spectrometerStatus: 'idle',
  voltageHistory: new RingBuffer<VoltagePoint>(MAX_HISTORY_POINTS),
  voltageHistoryRevision: 0,
  voltageBatchSupported: false,
  voltageSequenceGaps: 0,
  voltageUiDropped: 0,
  voltageServerBacklogMs: 0,
  injectionPump: DEFAULT_INJECTION_PUMP_STATUS,
  logs: [],
  mavrosState: { connected: false, armed: false, mode: '' },
  bridgeDiag: null,
  radioStatus: null,
  systemHealth: null,
  systemHealthHistory: [],
  manualStatus: DEFAULT_MANUAL_STATUS,
  controlEvents: [],

  connect: () => {
    if (get().socket) return

    const socket = io({
      path: '/socket.io',
      transports: ['websocket', 'polling'],
    })

    let voltageBatchSupported = false
    let angleSnapshotSupported = false
    let lastVoltageSequence: number | null = null

    socket.on('connect', () => {
      voltageBatchSupported = false
      angleSnapshotSupported = false
      lastVoltageSequence = null
      set({ connected: true, voltageBatchSupported: false, voltageSequenceGaps: 0, voltageUiDropped: 0, voltageServerBacklogMs: 0 })
      console.log('Socket connected')
    })

    const angleSnapshotCommitter = createThrottledCommit<AngleTelemetry>(FAST_TELEMETRY_INTERVAL_MS, (data) => {
      set((state) => ({
        angleTelemetry: {
          ...DEFAULT_ANGLE_TELEMETRY,
          ...data,
          angles: data.angles || state.pumpAngles,
          raw_angles: data.raw_angles || state.rawAngles,
        },
        pumpAngles: data.angles || state.pumpAngles,
        rawAngles: data.raw_angles || state.rawAngles,
      }))
    })

    const commitVoltageSamples = (samples: VoltageBatchSample[], droppedForUi = 0, sequenceGap = 0, batch = false, serverBacklogMs = 0) => {
      if (samples.length === 0) return
      const points: VoltagePoint[] = samples
        .filter((sample) => sample.valid || typeof sample.raw_code === 'number')
        .map((sample) => ({
          seq: sample.seq,
          sourceTimestampMs: sample.source_timestamp_ms,
          receivedAtMs: sample.received_at_ms,
          voltage: sample.voltage,
          absorbance: sample.absorbance,
          rawCode: sample.raw_code,
          valid: sample.valid,
        }))
      const latest = samples[samples.length - 1]
      set((state) => {
        state.voltageHistory.appendBatch(points)
        return {
        currentVoltage: latest.voltage,
        currentAbsorbance: latest.absorbance ?? state.currentAbsorbance,
        currentReferenceVoltage: typeof latest.reference_voltage === 'number' ? latest.reference_voltage : state.currentReferenceVoltage,
        currentBaselineVoltage: typeof latest.baseline_voltage === 'number' ? latest.baseline_voltage : state.currentBaselineVoltage,
        spectrometerBaselineSet: typeof latest.baseline_set === 'boolean' ? latest.baseline_set : state.spectrometerBaselineSet,
        spectrometerStatus: latest.status || state.spectrometerStatus,
        voltageHistoryRevision: state.voltageHistoryRevision + (points.length > 0 ? 1 : 0),
        voltageUiDropped: droppedForUi,
        voltageServerBacklogMs: serverBacklogMs,
        voltageBatchSupported: batch || state.voltageBatchSupported,
        voltageSequenceGaps: state.voltageSequenceGaps + sequenceGap,
        }
      })
    }

    socket.on('disconnect', () => {
      angleSnapshotCommitter.cancel()
      set({ connected: false, voltageBatchSupported: false })
      console.log('Socket disconnected')
    })

    socket.on('status', (data: StatusPayload) => {
      set({
        pumpConnected: data.pump_connected ?? false,
        automationRunning: data.automation_running ?? false,
        missionStatus: data.mission_status || 'IDLE',
        spectrometerStatus: data.spectrometer_status || 'idle',
      })
    })

    socket.on('angles', (data: PumpAngles) => {
      if (!angleSnapshotSupported) {
        angleSnapshotCommitter.push({ ...DEFAULT_ANGLE_TELEMETRY, angles: data, raw_angles: data, valid: true })
      }
    })

    socket.on('pump_angles', (data: PumpAngles) => {
      if (!angleSnapshotSupported) {
        angleSnapshotCommitter.push({ ...DEFAULT_ANGLE_TELEMETRY, angles: data, raw_angles: data, valid: true })
      }
    })

    socket.on('raw_angles', (data: PumpAngles) => {
      if (!angleSnapshotSupported) {
        angleSnapshotCommitter.push({ ...DEFAULT_ANGLE_TELEMETRY, angles: data, raw_angles: data, valid: true })
      }
    })

    socket.on('angle_telemetry', (data: AngleTelemetry) => {
      if (!angleSnapshotSupported) angleSnapshotCommitter.push(data)
    })

    socket.on('angle_snapshot', (data: AngleTelemetry) => {
      angleSnapshotSupported = true
      angleSnapshotCommitter.push(data)
    })

    socket.on('voltage', (data: VoltagePayload) => {
      if (voltageBatchSupported) return
      const raw = data.raw || {}
      const hasSample = data.sample !== false && (raw.valid === true || typeof raw.raw_code === 'number' || data.sample === true)
      if (!hasSample) return
      commitVoltageSamples([{
        seq: raw.seq ?? 0,
        source_timestamp_ms: raw.source_timestamp_ms ?? raw.timestamp_ms ?? 0,
        received_at_ms: raw.received_at_ms ?? Date.now(),
        voltage: data.value ?? 0,
        absorbance: data.absorbance ?? null,
        raw_code: raw.raw_code,
        valid: raw.valid === true,
        status: data.status,
        reference_voltage: data.reference_voltage ?? raw.reference_voltage,
        baseline_voltage: data.baseline_voltage ?? raw.baseline_voltage,
        baseline_set: data.baseline_set ?? raw.baseline_set,
      }])
    })

    socket.on('voltage_batch', (data: VoltageBatchPayload) => {
      voltageBatchSupported = true
      const gap = lastVoltageSequence === null ? 0 : Math.max(0, data.first_seq - lastVoltageSequence - 1)
      lastVoltageSequence = data.last_seq
      const latest = data.samples?.[data.samples.length - 1]
      const backlog = latest && typeof data.sent_at_ms === 'number'
        ? Math.max(0, data.sent_at_ms - latest.received_at_ms)
        : 0
      commitVoltageSamples(data.samples || [], data.dropped_for_ui || 0, gap, true, backlog)
    })

    socket.on('spectrometer_status', (status: string) => {
      set({ spectrometerStatus: status || 'idle' })
    })

    socket.on('injection_pump_status', (data: InjectionPumpStatus) => {
      set({ injectionPump: { ...DEFAULT_INJECTION_PUMP_STATUS, ...data } })
    })

    socket.on('log', (data: LogEntry) => {
      set((state) => ({ logs: [...state.logs.slice(-99), data] }))
    })

    socket.on('mavros_state', (data: MavrosState) => {
      set({ mavrosState: data })
    })

    socket.on('bridge_diagnostics', (data: BridgeDiag) => {
      set({ bridgeDiag: data })
    })

    socket.on('radio_status', (data: RadioStatus) => {
      set({ radioStatus: data })
    })

    socket.on('system_health', (data: SystemHealth) => {
      if (!data || Object.keys(data).length === 0) return
      set((state) => ({
        systemHealth: data,
        systemHealthHistory: [...state.systemHealthHistory.slice(-299), data],
      }))
    })

    socket.on('manual_status', (data: ManualStatus) => {
      set({ manualStatus: { ...DEFAULT_MANUAL_STATUS, ...data } })
    })

    socket.on('control_event', (data: ControlEvent) => {
      set((state) => ({ controlEvents: [...state.controlEvents.slice(-49), data] }))
    })

    set({ socket })
  },

  disconnect: () => {
    const { socket } = get()
    if (socket) {
      socket.disconnect()
      set({ socket: null, connected: false })
    }
  },

  refreshInjectionPumpStatus: async () => {
    const res = await fetch('/api/injection-pump/status', { method: 'POST' })
    const json = await res.json()
    if (json.success && json.data) {
      set({ injectionPump: { ...DEFAULT_INJECTION_PUMP_STATUS, ...json.data } })
    }
  },

  setInjectionPumpSpeed: async (speed: number) => {
    const res = await fetch('/api/injection-pump/set', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ speed }),
    })
    const json = await res.json()
    if (json.success && json.data) {
      set({ injectionPump: { ...DEFAULT_INJECTION_PUMP_STATUS, ...json.data } })
    }
    return { success: !!json.success, message: json.message || '' }
  },

  turnInjectionPumpOn: async (speed?: number) => {
    const options: RequestInit = { method: 'POST' }
    if (typeof speed === 'number') {
      options.headers = { 'Content-Type': 'application/json' }
      options.body = JSON.stringify({ speed })
    }
    const res = await fetch('/api/injection-pump/on', options)
    const json = await res.json()
    if (json.success && json.data) {
      set({ injectionPump: { ...DEFAULT_INJECTION_PUMP_STATUS, ...json.data } })
    }
    return { success: !!json.success, message: json.message || '' }
  },

  turnInjectionPumpOff: async () => {
    const res = await fetch('/api/injection-pump/off', { method: 'POST' })
    const json = await res.json()
    if (json.success && json.data) {
      set({ injectionPump: { ...DEFAULT_INJECTION_PUMP_STATUS, ...json.data } })
    }
    return { success: !!json.success, message: json.message || '' }
  },

  refreshManualStatus: async () => {
    const res = await fetch('/api/manual/status')
    const json = await res.json()
    if (json.success && json.data) {
      set({
        manualStatus: { ...DEFAULT_MANUAL_STATUS, ...json.data },
        controlEvents: Array.isArray(json.events) ? json.events : get().controlEvents,
      })
    }
  },

  setManualMode: async (enabled: boolean) => {
    const res = await fetch('/api/manual/mode', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ enabled }),
    })
    const json = await res.json()
    if (json.data) {
      set({ manualStatus: { ...DEFAULT_MANUAL_STATUS, ...json.data } })
    }
    return { success: !!json.success, message: json.message || '' }
  },

  sendManualPumpStep: async (payload) => {
    const res = await fetch('/api/manual/pump-step', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    })
    const json = await res.json()
    return { success: !!json.success, message: json.message || '' }
  },

  stopManualPumps: async () => {
    const res = await fetch('/api/manual/stop-all', { method: 'POST' })
    const json = await res.json()
    return { success: !!json.success, message: json.message || '' }
  },
}))

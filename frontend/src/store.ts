import { create } from 'zustand'
import { io, Socket } from 'socket.io-client'

interface PumpAngles {
  X: number
  Y: number
  Z: number
  A: number
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

interface VoltagePoint {
  time: string
  voltage: number
  absorbance: number
}

interface SpectrometerRawPayload {
  valid?: boolean
  raw_code?: number
  reference_voltage?: number
  baseline_voltage?: number
  baseline_set?: boolean
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

interface PidErrorState {
  X: number
  Y: number
  Z: number
  A: number
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

interface StatusPayload {
  pump_connected?: boolean
  automation_running?: boolean
  mission_status?: string
  spectrometer_status?: string
}

const MAX_HISTORY_POINTS = 150
const VOLTAGE_UI_INTERVAL_MS = 200
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
  currentVoltage: number
  currentAbsorbance: number
  currentReferenceVoltage: number | null
  currentBaselineVoltage: number
  spectrometerBaselineSet: boolean
  spectrometerStatus: string
  voltageHistory: VoltagePoint[]
  pidErrors: PidErrorState
  injectionPump: InjectionPumpStatus
  logs: LogEntry[]
  mavrosState: MavrosState
  bridgeDiag: BridgeDiag | null
  radioStatus: RadioStatus | null

  connect: () => void
  disconnect: () => void
  refreshInjectionPumpStatus: () => Promise<void>
  setInjectionPumpSpeed: (speed: number) => Promise<{ success: boolean; message: string }>
  turnInjectionPumpOn: (speed?: number) => Promise<{ success: boolean; message: string }>
  turnInjectionPumpOff: () => Promise<{ success: boolean; message: string }>
}

const DEFAULT_INJECTION_PUMP_STATUS: InjectionPumpStatus = {
  enabled: false,
  speed: 0,
  last_response: '',
  last_error: '',
}

export const useAppStore = create<AppState>((set, get) => ({
  socket: null,
  connected: false,
  pumpConnected: false,
  automationRunning: false,
  missionStatus: 'IDLE',
  pumpAngles: { X: 0, Y: 0, Z: 0, A: 0 },
  rawAngles: { X: 0, Y: 0, Z: 0, A: 0 },
  currentVoltage: 0,
  currentAbsorbance: 0,
  currentReferenceVoltage: null,
  currentBaselineVoltage: 0,
  spectrometerBaselineSet: false,
  spectrometerStatus: 'idle',
  voltageHistory: [],
  pidErrors: { X: 0, Y: 0, Z: 0, A: 0 },
  injectionPump: DEFAULT_INJECTION_PUMP_STATUS,
  logs: [],
  mavrosState: { connected: false, armed: false, mode: '' },
  bridgeDiag: null,
  radioStatus: null,

  connect: () => {
    if (get().socket) return

    const socket = io({
      path: '/socket.io',
      transports: ['websocket', 'polling'],
    })

    socket.on('connect', () => {
      set({ connected: true })
      console.log('Socket connected')
    })

    const angleCommitter = createThrottledCommit<PumpAngles>(FAST_TELEMETRY_INTERVAL_MS, (data) => {
      set({ pumpAngles: data })
    })
    const rawAngleCommitter = createThrottledCommit<PumpAngles>(FAST_TELEMETRY_INTERVAL_MS, (data) => {
      set({ rawAngles: data })
    })
    const voltageCommitter = createThrottledCommit<VoltagePayload>(VOLTAGE_UI_INTERVAL_MS, (data) => {
      const voltage = data.value ?? 0
      const absorbance = data.absorbance ?? 0
      const hasSample =
        data.raw?.valid === true ||
        typeof data.raw?.raw_code === 'number' ||
        data.sample === true
      const referenceVoltage =
        typeof data.reference_voltage === 'number'
          ? data.reference_voltage
          : data.raw?.reference_voltage
      const baselineVoltage =
        typeof data.baseline_voltage === 'number'
          ? data.baseline_voltage
          : data.raw?.baseline_voltage
      const baselineSet =
        typeof data.baseline_set === 'boolean'
          ? data.baseline_set
          : data.raw?.baseline_set
      const time = new Date().toLocaleTimeString()

      set((state) => ({
        currentVoltage: hasSample ? voltage : state.currentVoltage,
        currentAbsorbance: hasSample ? absorbance : state.currentAbsorbance,
        currentReferenceVoltage:
          typeof referenceVoltage === 'number' ? referenceVoltage : state.currentReferenceVoltage,
        currentBaselineVoltage:
          typeof baselineVoltage === 'number' ? baselineVoltage : state.currentBaselineVoltage,
        spectrometerBaselineSet:
          typeof baselineSet === 'boolean' ? baselineSet : state.spectrometerBaselineSet,
        spectrometerStatus: data.status || state.spectrometerStatus,
        voltageHistory: hasSample
          ? [
              ...state.voltageHistory.slice(-(MAX_HISTORY_POINTS - 1)),
              { time, voltage, absorbance },
            ]
          : state.voltageHistory,
      }))
    })

    socket.on('disconnect', () => {
      angleCommitter.cancel()
      rawAngleCommitter.cancel()
      voltageCommitter.cancel()
      set({ connected: false })
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
      angleCommitter.push(data)
    })

    socket.on('pump_angles', (data: PumpAngles) => {
      angleCommitter.push(data)
    })

    socket.on('raw_angles', (data: PumpAngles) => {
      rawAngleCommitter.push(data)
    })

    socket.on('voltage', (data: VoltagePayload) => {
      voltageCommitter.push(data)
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
}))

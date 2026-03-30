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

interface PidErrorState {
  X: number
  Y: number
  Z: number
  A: number
}

const MAX_HISTORY_POINTS = 150

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
  voltageHistory: VoltagePoint[]
  pidErrors: PidErrorState
  injectionPump: InjectionPumpStatus
  logs: LogEntry[]

  connect: () => void
  disconnect: () => void
  refreshInjectionPumpStatus: () => Promise<void>
  setInjectionPumpSpeed: (speed: number) => Promise<{ success: boolean; message: string }>
  turnInjectionPumpOn: () => Promise<{ success: boolean; message: string }>
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
  voltageHistory: [],
  pidErrors: { X: 0, Y: 0, Z: 0, A: 0 },
  injectionPump: DEFAULT_INJECTION_PUMP_STATUS,
  logs: [],

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

    socket.on('disconnect', () => {
      set({ connected: false })
      console.log('Socket disconnected')
    })

    socket.on('status', (data: any) => {
      set({
        pumpConnected: data.pump_connected,
        automationRunning: data.automation_running,
        missionStatus: data.mission_status || 'IDLE',
      })
    })

    socket.on('angles', (data: PumpAngles) => {
      set({ pumpAngles: data })
    })

    socket.on('pump_angles', (data: PumpAngles) => {
      set({ pumpAngles: data })
    })

    socket.on('raw_angles', (data: PumpAngles) => {
      set({ rawAngles: data })
    })

    socket.on('voltage', (data: { value: number; absorbance?: number }) => {
      const voltage = data.value ?? 0
      const absorbance = data.absorbance ?? 0
      const time = new Date().toLocaleTimeString()
      set((state) => ({
        currentVoltage: voltage,
        currentAbsorbance: absorbance,
        voltageHistory: [
          ...state.voltageHistory.slice(-(MAX_HISTORY_POINTS - 1)),
          { time, voltage, absorbance },
        ],
      }))
    })

    socket.on('injection_pump_status', (data: InjectionPumpStatus) => {
      set({ injectionPump: { ...DEFAULT_INJECTION_PUMP_STATUS, ...data } })
    })

    socket.on('log', (data: LogEntry) => {
      set((state) => ({ logs: [...state.logs.slice(-99), data] }))
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

  turnInjectionPumpOn: async () => {
    const res = await fetch('/api/injection-pump/on', { method: 'POST' })
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

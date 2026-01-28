import { create } from 'zustand'
import { io, Socket } from 'socket.io-client'

interface PumpAngles {
  X: number
  Y: number
  Z: number
  A: number
}

interface LogEntry {
  timestamp: string
  message: string
  level: string
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
  logs: LogEntry[]
  
  connect: () => void
  disconnect: () => void
}

export const useAppStore = create<AppState>((set, get) => ({
  socket: null,
  connected: false,
  pumpConnected: false,
  automationRunning: false,
  missionStatus: "IDLE",
  pumpAngles: { X: 0, Y: 0, Z: 0, A: 0 },
  rawAngles: { X: 0, Y: 0, Z: 0, A: 0 },
  currentVoltage: 0,
  logs: [],

  connect: () => {
    if (get().socket) return

    // Connect to same host, port is handled by proxy in vite or relative path in prod
    const socket = io({
      path: '/socket.io',
      transports: ['websocket', 'polling'] 
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
        missionStatus: data.mission_status || "IDLE"
      })
    })

    socket.on('angles', (data: PumpAngles) => {
      set({ pumpAngles: data })
    })

    socket.on('raw_angles', (data: PumpAngles) => {
      set({ rawAngles: data })
    })

    socket.on('voltage', (data: { value: number }) => {
      set({ currentVoltage: data.value })
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
  }
}))

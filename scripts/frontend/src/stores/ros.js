import { defineStore } from 'pinia'
import { io } from 'socket.io-client'
import { ref, reactive } from 'vue'

export const useRosStore = defineStore('ros', () => {
  // 状态
  const connected = ref(false)
  const pumpConnected = ref(false)
  const automationRunning = ref(false)
  const lastLatency = ref(0)
  
  // 数据缓存
  const motors = reactive({
    X: 0.0, Y: 0.0, Z: 0.0, A: 0.0
  })
  const logs = ref([])
  
  // PID Data
  const pidData = ref({ error: 0, target: 0, actual: 0, timestamp: 0 })
  const pidHistory = ref({
     target: [],
     actual: []
  })
  
  // Socket 实例
  const socket = io('/', {
    transports: ['websocket', 'polling'],
    autoConnect: true,
    reconnection: true
  })

  // 事件监听
  socket.on('connect', () => {
    connected.value = true
    console.log('Socket Connected')
  })

  socket.on('disconnect', () => {
    connected.value = false
    console.log('Socket Disconnected')
  })

  socket.on('status', (data) => {
    pumpConnected.value = data.pump_connected
    automationRunning.value = data.automation_running
  })

  socket.on('angles', (data) => {
    Object.assign(motors, data)
  })
  
  socket.on('pid_data', (data) => {
    // 假设后端传回 { timestamp, target, actual, error }
    // 如果后端只传了 error (根据 web_config_server.py line 328)，我们需要适配
    // 暂时仅记录 error，实际项目中建议后端补全 target/actual
    pidData.value = data
  })

  socket.on('log', (entry) => {
    logs.value.push(entry)
    if (logs.value.length > 50) logs.value.shift()
  })

  // --- API Methods ---
  
  function sendCommand(cmd) {
    return fetch('/api/motor/command', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ command: cmd })
    }).then(res => res.json())
  }
  
  function emergencyStop() {
    return fetch('/api/motor/stop', { method: 'POST' }).then(res => res.json())
  }
  
  // Calibration
  function setZero(motor) {
    return fetch('/api/calibration/zero', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ motor })
    }).then(res => res.json())
  }
  
  function resetZero() {
    return fetch('/api/calibration/reset', { method: 'POST' }).then(res => res.json())
  }

  // PID Config
  async function getPidConfig() {
    return fetch('/api/pid/config').then(res => res.json())
  }
  
  function setPidConfig(config) {
    return fetch('/api/pid/config', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(config)
    }).then(res => res.json())
  }
  
  function runPidTest(params) {
    return fetch('/api/pid/test', {
       method: 'POST',
       headers: { 'Content-Type': 'application/json' },
       body: JSON.stringify(params)
    }).then(res => res.json())
  }

  // Records
  async function listRecords() {
    return fetch('/api/records/list').then(res => res.json())
  }
  
  function deleteRecord(filename) {
    return fetch(`/api/records/file/${filename}`, { method: 'DELETE' }).then(res => res.json())
  }

  return {
    connected,
    pumpConnected,
    automationRunning,
    motors,
    logs,
    pidData,
    sendCommand,
    emergencyStop,
    setZero,
    resetZero,
    getPidConfig,
    setPidConfig,
    runPidTest,
    listRecords,
    deleteRecord
  }
})

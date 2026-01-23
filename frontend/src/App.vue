<template>
  <div id="app">
    <!-- App Header -->
    <AppHeader 
      :status="status" 
      :connection="connection" 
      @stop-mission="stopMission"
    />

    <div class="main-container">
      <!-- Sidebar Navigation (Desktop) -->
      <nav class="sidebar glass-panel show-desktop" role="navigation" aria-label="主导航">
        <a 
          v-for="view in views" 
          :key="view.id" 
          :class="['nav-link', { active: currentView === view.id }]"
          @click="currentView = view.id"
          role="button"
          :aria-current="currentView === view.id ? 'page' : undefined"
          tabindex="0"
          @keydown.enter="currentView = view.id"
          @keydown.space.prevent="currentView = view.id"
        >
          <span class="icon" v-html="view.icon" aria-hidden="true"></span>
          {{ view.label }}
        </a>
      </nav>

      <!-- Main Content -->
      <main class="content-area">
        <Transition name="slide-up" mode="out-in">
          <!-- Dashboard View -->
          <DashboardView 
            v-if="currentView === 'dashboard'" 
            :angles="angles" 
            :status="status" 
            :logs="logs"
            @control="handleMissionControl" 
            @clear-log="clearLogs"
          />

          <!-- Manual Control View -->
          <ManualControlView 
            v-else-if="currentView === 'manual'" 
            :connection="connection"
            @toast="addToast"
          />

          <!-- PID Config View -->
          <PIDConfigView 
            v-else-if="currentView === 'pid'" 
            @toast="addToast"
          />

          <!-- Config View -->
          <ConfigView 
            v-else-if="currentView === 'config'" 
            :config="config" 
            :is-dirty="isConfigDirty"
            :presets="presets" 
            @save="saveConfig" 
            @reset="resetConfig" 
            @load-preset="loadPreset"
            @save-preset="savePreset" 
            @delete-preset="deletePreset"
          />

          <!-- Log View (Mobile/Full) -->
          <LogView 
            v-else-if="currentView === 'logs'" 
            :logs="logs" 
            @clear="clearLogs"
          />
        </Transition>
      </main>
    </div>

    <!-- Mobile Bottom Nav -->
    <nav class="bottom-nav glass-panel show-mobile" role="navigation" aria-label="移动端导航">
      <a 
        v-for="view in views" 
        :key="view.id" 
        :class="['nav-item', { active: currentView === view.id }]"
        @click="currentView = view.id"
        role="button"
        :aria-current="currentView === view.id ? 'page' : undefined"
        tabindex="0"
        @keydown.enter="currentView = view.id"
      >
        <span class="icon" v-html="view.icon" aria-hidden="true"></span>
        <span class="label">{{ view.label }}</span>
      </a>
    </nav>

    <!-- Global Toast Container -->
    <div class="toast-container" aria-live="polite" aria-atomic="true">
      <TransitionGroup name="list">
        <div 
          v-for="t in toasts" 
          :key="t.id" 
          :class="['toast', t.type]"
          role="alert"
        >
          <div class="toast-content">
            <span class="toast-message">{{ t.message }}</span>
            <button 
              v-if="t.action" 
              @click="t.action.handler" 
              class="toast-action"
              :aria-label="t.action.label"
            >
              {{ t.action.label }}
            </button>
          </div>
        </div>
      </TransitionGroup>
    </div>
  </div>
</template>

<script setup>
import { ref, reactive } from 'vue'
import { io } from 'socket.io-client'
import AppHeader from './components/AppHeader.vue'
import DashboardView from './components/DashboardView.vue'
import ManualControlView from './components/ManualControlView.vue'
import PIDConfigView from './components/PIDConfigView.vue'
import ConfigView from './components/ConfigView.vue'
import LogView from './components/LogView.vue'
import { Icons } from './utils/icons'

// State
const views = ref([
  { id: 'dashboard', label: '概览', icon: Icons.dashboard },
  { id: 'manual', label: '手动', icon: Icons.manual },
  { id: 'pid', label: 'PID', icon: Icons.pid },
  { id: 'config', label: '配置', icon: Icons.config },
  { id: 'logs', label: '日志', icon: Icons.logs }
])

const currentView = ref('dashboard')

const connection = reactive({
  socket: false,
  pump: false,
  automation: false
})

const angles = reactive({ X: 0, Y: 0, Z: 0, A: 0 })
const logs = ref([])
const config = reactive({
  mission: { name: '' },
  pump_settings: { pid_mode: true, pid_precision: 0.1 },
  sampling_sequence: { loop_count: 1, steps: [] }
})
const presets = ref([])
const toasts = ref([])
const isConfigDirty = ref(false)
const status = reactive({ automation: false })

// Socket.IO
const socket = io()

socket.on('connect', () => {
  connection.socket = true
  addToast('已连接服务器', 'success')
})

socket.on('disconnect', () => {
  connection.socket = false
  addToast('连接断开', 'error')
})

socket.on('status', (data) => {
  connection.pump = data.pump_connected
  connection.automation = data.automation_running
  status.automation = data.automation_running
})

socket.on('angles', (data) => {
  Object.assign(angles, data)
})

socket.on('log', (data) => {
  logs.value.unshift({
    id: Date.now(),
    time: new Date().toLocaleTimeString(),
    msg: data.message,
    level: data.level || 'info'
  })
  if (logs.value.length > 500) logs.value.pop()
})

// Methods
const addToast = (message, type = 'info', action = null) => {
  const id = Date.now()
  toasts.value.push({ id, message, type, action })
  setTimeout(() => {
    toasts.value = toasts.value.filter(t => t.id !== id)
  }, 5000)
}

const handleMissionControl = async (action) => {
  try {
    const res = await fetch(`/api/mission/${action}`, { method: 'POST' })
    const data = await res.json()
    addToast(data.message, data.success ? 'success' : 'error')
  } catch (e) {
    addToast(`操作失败: ${e.message}`, 'error')
  }
}

const stopMission = async () => {
  await handleMissionControl('stop')
}

const clearLogs = () => {
  logs.value = []
  addToast('日志已清空', 'success')
}

const saveConfig = async () => {
  try {
    const res = await fetch('/api/config', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(config)
    })
    const data = await res.json()
    addToast(data.message, data.success ? 'success' : 'error')
    if (data.success) isConfigDirty.value = false
  } catch (e) {
    addToast(`保存失败: ${e.message}`, 'error')
  }
}

const resetConfig = async () => {
  try {
    const res = await fetch('/api/config')
    const data = await res.json()
    Object.assign(config, data)
    isConfigDirty.value = false
    addToast('配置已重置', 'success')
  } catch (e) {
    addToast(`重置失败: ${e.message}`, 'error')
  }
}

const loadPreset = async (name) => {
  try {
    const res = await fetch(`/api/preset/${name}`)
    const data = await res.json()
    Object.assign(config, data)
    isConfigDirty.value = true
    addToast(`已加载预设: ${name}`, 'success')
  } catch (e) {
    addToast(`加载失败: ${e.message}`, 'error')
  }
}

const savePreset = async (name) => {
  try {
    const res = await fetch(`/api/preset/${name}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(config)
    })
    const data = await res.json()
    addToast(data.message, data.success ? 'success' : 'error')
  } catch (e) {
    addToast(`保存失败: ${e.message}`, 'error')
  }
}

const deletePreset = async (name) => {
  try {
    const res = await fetch(`/api/preset/${name}`, { method: 'DELETE' })
    const data = await res.json()
    addToast(data.message, data.success ? 'success' : 'error')
  } catch (e) {
    addToast(`删除失败: ${e.message}`, 'error')
  }
}

// Load initial data
fetch('/api/config').then(res => res.json()).then(data => Object.assign(config, data))
fetch('/api/presets').then(res => res.json()).then(data => presets.value = data)
</script>

<style scoped>
.sidebar {
  width: var(--sidebar-width);
  margin: var(--spacing-lg);
  margin-right: 0;
  display: flex;
  flex-direction: column;
  padding: var(--spacing-md);
}

.nav-link {
  padding: 12px 16px;
  color: var(--color-text-muted);
  border-radius: var(--radius-sm);
  cursor: pointer;
  margin-bottom: 4px;
  display: flex;
  align-items: center;
  gap: 12px;
  transition: var(--trans-fast);
}

.nav-link.active {
  background: linear-gradient(90deg, rgba(0, 243, 255, 0.1) 0%, transparent 100%);
  color: var(--color-primary);
  border-left: 3px solid var(--color-primary);
}

.show-desktop {
  display: flex;
}

.show-mobile {
  display: none;
}

@media (max-width: 768px) {
  .show-desktop {
    display: none;
  }

  .show-mobile {
    display: flex;
  }

  .main-container {
    flex-direction: column;
  }

  .sidebar {
    display: none;
  }

  .bottom-nav {
    position: fixed;
    bottom: 0;
    left: 0;
    right: 0;
    height: var(--bottom-nav-height);
    border-radius: 0;
    margin: 0;
    justify-content: space-around;
    align-items: center;
    z-index: 100;
    backdrop-filter: blur(20px);
    background: rgba(5, 10, 20, 0.9);
    padding-bottom: env(safe-area-inset-bottom, 0);
  }

  .nav-item {
    display: flex;
    flex-direction: column;
    align-items: center;
    font-size: 10px;
    color: var(--color-text-muted);
    padding: 8px;
    min-width: 60px;
    min-height: 44px;
    justify-content: center;
  }

  .nav-item.active {
    color: var(--color-primary);
  }

  .nav-item .icon :deep(svg) {
    width: 24px;
    height: 24px;
  }

  .content-area {
    padding-bottom: calc(var(--bottom-nav-height) + env(safe-area-inset-bottom, 0) + 16px);
  }
}

/* Toast */
.toast-container {
  position: fixed;
  top: 20px;
  right: 20px;
  z-index: 999;
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.toast {
  background: rgba(0, 0, 0, 0.8);
  border: 1px solid rgba(255, 255, 255, 0.1);
  color: white;
  padding: 12px 24px;
  border-radius: 8px;
  backdrop-filter: blur(10px);
  box-shadow: 0 4px 12px rgba(0, 0, 0, 0.5);
  min-width: 280px;
}

.toast.success {
  border-color: var(--color-success);
  background: rgba(0, 255, 157, 0.1);
}

.toast.success .toast-message {
  color: var(--color-success);
}

.toast.error {
  border-color: var(--color-danger);
  background: rgba(255, 0, 85, 0.1);
}

.toast.error .toast-message {
  color: var(--color-danger);
}

.toast.warning {
  border-color: var(--color-warning);
  background: rgba(255, 184, 0, 0.1);
}

.toast.warning .toast-message {
  color: var(--color-warning);
}

.toast-content {
  display: flex;
  align-items: center;
  gap: 12px;
  justify-content: space-between;
}

.toast-message {
  flex: 1;
}

.toast-action {
  background: rgba(255, 255, 255, 0.1);
  border: 1px solid rgba(255, 255, 255, 0.2);
  color: white;
  padding: 4px 12px;
  border-radius: 4px;
  cursor: pointer;
  font-size: 0.85em;
  transition: var(--trans-fast);
}

.toast-action:hover {
  background: rgba(255, 255, 255, 0.2);
}
</style>

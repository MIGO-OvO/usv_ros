<script setup>
import { useRosStore } from './stores/ros'
import { useThemeStore } from './stores/theme'
import { computed, ref } from 'vue'
import { BoltIcon, SignalIcon, ExclamationTriangleIcon, HomeIcon, PlayCircleIcon, CpuChipIcon, Cog6ToothIcon, MoonIcon, SunIcon } from '@heroicons/vue/24/solid'
import MotorCard from './components/MotorCard.vue'
import PidChart from './components/PidChart.vue'
import AutomationView from './views/AutomationView.vue'
import MotorView from './views/MotorView.vue'
import SettingsView from './views/SettingsView.vue'

const store = useRosStore()
const themeStore = useThemeStore()

const statusColor = computed(() => store.connected ? 'text-green-600 dark:text-emerald-400' : 'text-red-600 dark:text-rose-400')
const pumpColor = computed(() => store.pumpConnected ? 'text-green-600 dark:text-emerald-400' : 'text-yellow-600 dark:text-amber-400')

// Navigation State
const currentView = ref('dashboard') // dashboard | automation | settings

// Mock PID Data for visualization
const pidSeries = ref([
  { name: 'Actual', data: [] },
  { name: 'Target', data: [] }
])

</script>

<template>
  <div class="h-full flex flex-col bg-slate-50 dark:bg-gemini-950 text-slate-900 dark:text-slate-200 font-sans transition-colors duration-300">
    <!-- Top Bar -->
    <header class="h-16 border-b border-slate-200 dark:border-gemini-800 flex items-center justify-between px-4 md:px-6 bg-white dark:bg-gemini-900 shadow-sm dark:shadow-none z-10 transition-colors duration-300">
      <div class="flex items-center space-x-3">
        <div class="w-8 h-8 bg-indigo-600 rounded flex items-center justify-center shadow-md shadow-indigo-500/30 shrink-0">
          <BoltIcon class="w-5 h-5 text-white" />
        </div>
        <h1 class="text-xl font-bold tracking-tight text-slate-800 dark:text-slate-100 hidden md:block">USV 水质监测控制台</h1>
        <h1 class="text-lg font-bold tracking-tight text-slate-800 dark:text-slate-100 md:hidden">USV 控制台</h1>
      </div>

      <div class="flex items-center space-x-3 md:space-x-6">
        <!-- Theme Toggle (Hidden on small mobile to save space, or kept compact) -->
        <button 
          @click="themeStore.toggle()" 
          class="p-2 rounded-lg text-slate-500 hover:bg-slate-100 dark:text-slate-400 dark:hover:bg-gemini-800 transition-colors hidden sm:block"
          :title="themeStore.isDark ? '切换到浅色模式' : '切换到深色模式'"
        >
          <SunIcon v-if="themeStore.isDark" class="w-5 h-5" />
          <MoonIcon v-else class="w-5 h-5" />
        </button>

        <div class="h-6 w-px bg-slate-200 dark:bg-gemini-800 hidden sm:block"></div>

        <!-- Status Indicators (Compact on mobile) -->
        <div class="flex items-center space-x-4 text-sm font-medium">
          <div class="flex items-center space-x-1" :class="statusColor">
            <SignalIcon class="w-4 h-4" />
            <span class="hidden sm:inline">ROS: {{ store.connected ? '已连接' : '断开' }}</span>
          </div>
          <div class="flex items-center space-x-1" :class="pumpColor">
            <div class="w-2 h-2 rounded-full bg-current"></div>
            <span class="hidden sm:inline">泵组: {{ store.pumpConnected ? '在线' : '离线' }}</span>
          </div>
        </div>

        <!-- E-Stop (Icon only on very small screens) -->
        <button 
          @click="store.emergencyStop()"
          class="bg-red-600 hover:bg-red-700 text-white px-3 md:px-4 py-2 rounded font-bold uppercase tracking-wide flex items-center space-x-2 transition-colors shadow-lg shadow-red-500/30 dark:shadow-red-900/50"
        >
          <ExclamationTriangleIcon class="w-5 h-5" />
          <span class="hidden md:inline">紧急停止</span>
          <span class="md:hidden">急停</span>
        </button>
      </div>
    </header>

    <!-- Main Layout -->
    <div class="flex-1 flex overflow-hidden relative">
      <!-- Sidebar Navigation (Desktop) -->
      <aside class="hidden md:flex w-64 bg-white dark:bg-gemini-900 border-r border-slate-200 dark:border-gemini-800 flex-col transition-colors duration-300">
        <nav class="flex-1 p-4 space-y-2">
          <button 
            v-for="item in [
              { id: 'dashboard', icon: HomeIcon, label: '仪表盘' },
              { id: 'automation', icon: PlayCircleIcon, label: '自动化序列' },
              { id: 'motor', icon: CpuChipIcon, label: '电机控制' },
              { id: 'settings', icon: Cog6ToothIcon, label: '系统设置' }
            ]"
            :key="item.id"
            @click="currentView = item.id"
            class="w-full flex items-center px-4 py-2 rounded font-medium transition-colors"
            :class="currentView === item.id ? 'bg-slate-100 dark:bg-indigo-500/10 text-indigo-600 dark:text-indigo-400 border-l-4 border-indigo-500' : 'text-slate-600 dark:text-slate-400 hover:bg-slate-50 dark:hover:bg-gemini-800 hover:text-slate-900 dark:hover:text-slate-200'"
          >
            <component :is="item.icon" class="w-5 h-5 mr-3" />
            {{ item.label }}
          </button>
        </nav>
        
        <!-- Logs Preview (Desktop Only) -->
        <div class="h-48 border-t border-slate-200 dark:border-gemini-800 p-2 bg-slate-50 dark:bg-gemini-900 overflow-y-auto transition-colors duration-300">
          <h4 class="text-xs font-bold text-slate-500 dark:text-slate-500 uppercase mb-2 px-2">系统日志</h4>
          <div class="space-y-1">
            <div v-for="(log, idx) in store.logs" :key="idx" class="text-xs font-mono px-2 py-0.5 rounded hover:bg-slate-200 dark:hover:bg-gemini-800 transition-colors">
              <span class="text-slate-500 dark:text-slate-500">[{{ log.timestamp }}]</span> 
              <span :class="log.level === 'error' ? 'text-red-600 dark:text-rose-400' : 'text-slate-700 dark:text-slate-300'">{{ log.message }}</span>
            </div>
          </div>
        </div>
      </aside>

      <!-- Content Area -->
      <main class="flex-1 overflow-auto bg-slate-50 dark:bg-gemini-950 relative transition-colors duration-300 pb-20 md:pb-0">
        <!-- View: Automation -->
        <AutomationView v-if="currentView === 'automation'" />

        <!-- View: Motor Control -->
        <MotorView v-else-if="currentView === 'motor'" />

        <!-- View: Settings -->
        <SettingsView v-else-if="currentView === 'settings'" />

        <!-- View: Dashboard -->
        <div v-else-if="currentView === 'dashboard'" class="p-4 md:p-6">
          <!-- Motor Grid -->
          <section class="mb-8">
            <h2 class="text-lg font-semibold text-slate-500 dark:text-slate-400 mb-4 flex items-center">
              <span class="w-1.5 h-6 bg-indigo-500 rounded-full mr-3"></span>
              电机状态
            </h2>
            <div class="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4 md:gap-6">
              <!-- All Motors: Cyber Style (Optimized) -->
              <MotorCard 
                v-for="(val, motor) in store.motors" 
                :key="motor" 
                :motor-id="motor" 
                :angle="val"
                variant="cyber"
              />
            </div>
          </section>

          <!-- PID & Automation Row -->
          <section class="grid grid-cols-1 lg:grid-cols-3 gap-6">
            <div class="lg:col-span-2">
              <PidChart :series="pidSeries" />
            </div>
            
            <!-- Quick Status Card -->
            <div class="bg-white dark:bg-gemini-900 rounded-xl border border-slate-200 dark:border-gemini-800 p-4 shadow-sm transition-colors duration-300">
               <h3 class="font-bold text-slate-800 dark:text-slate-200 mb-4">系统健康度</h3>
               <div class="space-y-4">
                 <div class="flex justify-between items-center p-3 bg-slate-50 dark:bg-gemini-950/50 rounded-lg">
                    <span class="text-sm text-slate-500 dark:text-slate-400">CPU 温度</span>
                    <span class="text-green-600 dark:text-emerald-400 font-mono">42°C</span>
                 </div>
                 <div class="flex justify-between items-center p-3 bg-slate-50 dark:bg-gemini-950/50 rounded-lg">
                    <span class="text-sm text-slate-500 dark:text-slate-400">电池电压</span>
                    <span class="text-blue-600 dark:text-indigo-400 font-mono">12.4V</span>
                 </div>
                 <div class="flex justify-between items-center p-3 bg-slate-50 dark:bg-gemini-950/50 rounded-lg">
                    <span class="text-sm text-slate-500 dark:text-slate-400">内存使用</span>
                    <span class="text-yellow-600 dark:text-amber-400 font-mono">1.2GB / 4GB</span>
                 </div>
               </div>
            </div>
          </section>
        </div>
        
        <!-- Placeholder Views -->
        <div v-else class="flex items-center justify-center h-full text-slate-400 dark:text-slate-500">
           <div class="text-center">
             <Cog6ToothIcon class="w-16 h-16 mx-auto mb-4 opacity-20" />
             <p>模块 "{{ currentView }}" 正在开发中。</p>
           </div>
        </div>
        
      </main>

      <!-- Bottom Navigation (Mobile Only) -->
      <nav class="md:hidden absolute bottom-0 left-0 w-full bg-white dark:bg-gemini-900 border-t border-slate-200 dark:border-gemini-800 flex justify-around p-2 z-20 transition-colors duration-300">
        <button 
          v-for="item in [
            { id: 'dashboard', icon: HomeIcon, label: '仪表盘' },
            { id: 'automation', icon: PlayCircleIcon, label: '自动化' },
            { id: 'motor', icon: CpuChipIcon, label: '电机' },
            { id: 'settings', icon: Cog6ToothIcon, label: '设置' }
          ]"
          :key="item.id"
          @click="currentView = item.id"
          class="flex flex-col items-center p-2 rounded-lg transition-colors"
          :class="currentView === item.id ? 'text-indigo-600 dark:text-indigo-400' : 'text-slate-500 dark:text-slate-400'"
        >
          <component :is="item.icon" class="w-6 h-6 mb-1" />
          <span class="text-[10px] font-medium">{{ item.label }}</span>
        </button>
      </nav>
    </div>
  </div>
</template>

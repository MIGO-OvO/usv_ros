<script setup>
import { computed, ref } from 'vue'
import { useRosStore } from '../stores/ros'
import { CogIcon, PlayIcon, StopIcon } from '@heroicons/vue/24/solid'

const props = defineProps({
  motorId: { type: String, required: true },
  angle: { type: Number, default: 0 },
  variant: { type: String, default: 'modern' } // 'cyber', 'industrial', 'modern'
})

const store = useRosStore()
const targetAngle = ref(props.angle)

// --- Common Calculations ---
const handleSliderChange = () => {
  store.sendCommand(`${props.motorId}EGV5J${targetAngle.value.toFixed(1)}`)
}

const sendDirectCmd = (action) => {
  if (action === 'stop') {
    store.sendCommand(`${props.motorId}DFV0J0`)
  } else if (action === 'continuous') {
    store.sendCommand(`${props.motorId}EFV5JG`)
  }
}

// --- Variant Specifics ---

// Industrial: 240 degree arc
const industrialRadius = 40
const industrialCircumference = 2 * Math.PI * industrialRadius
const industrialDashArray = `${industrialCircumference * 0.66} ${industrialCircumference * 0.34}` // 240 degrees visible
const industrialOffset = computed(() => {
  const maxAngle = 360
  const angle = Math.min(Math.max(props.angle, 0), maxAngle)
  const progress = angle / maxAngle
  return industrialCircumference * (1 - 0.66 * progress) // Fill up to 240deg based on 360deg input
})
const targetPointerRotate = computed(() => {
  // Map 0-360 to -120 to +120 degrees
  return (targetAngle.value / 360) * 240 - 120
})

// Cyber: Ticks
const ticks = Array.from({ length: 30 }, (_, i) => i * 12) // 30 ticks
const getTickColor = (tickAngle) => {
  return tickAngle <= props.angle ? 'text-blue-500 dark:text-blue-400' : 'text-slate-200 dark:text-slate-700'
}

// Modern: Full circle
const modernRadius = 40
const modernCircumference = 2 * Math.PI * modernRadius
const modernOffset = computed(() => {
  const progress = (props.angle % 360) / 360
  return modernCircumference - (progress * modernCircumference)
})
</script>

<template>
  <div class="bg-white dark:bg-gemini-900 rounded-xl border border-slate-200 dark:border-gemini-800 shadow-sm dark:shadow-none p-4 flex flex-col relative overflow-hidden group transition-all duration-300 hover:shadow-md hover:border-indigo-300 dark:hover:border-indigo-900">
    
    <!-- Header -->
    <div class="flex justify-between items-center mb-4 z-10">
      <div class="flex items-center space-x-2">
        <div class="p-1.5 bg-slate-100 dark:bg-gemini-800 rounded-lg text-indigo-500 dark:text-indigo-400 transition-colors">
          <CogIcon class="w-5 h-5" />
        </div>
        <div>
          <h3 class="font-bold text-slate-800 dark:text-slate-200 transition-colors">{{ motorId }} 轴</h3>
          <p class="text-[10px] uppercase tracking-wider text-slate-400 dark:text-slate-500 font-bold">{{ variant }} Design</p>
        </div>
      </div>
      <div class="flex items-center space-x-1">
        <span class="w-2 h-2 rounded-full" :class="angle > 0 ? 'bg-emerald-500 animate-pulse' : 'bg-slate-300 dark:bg-gemini-800'"></span>
      </div>
    </div>

    <!-- Visualization Area -->
    <div class="flex-1 flex justify-center items-center py-2 relative z-10 h-36">
      
      <!-- ================= Variant A: Cyber Tick ================= -->
      <div v-if="variant === 'cyber'" class="relative w-32 h-32">
        <svg class="w-full h-full transform -rotate-90 scale-125">
          <!-- Ticks -->
          <line 
            v-for="tick in ticks" :key="tick"
            x1="64" y1="10" x2="64" y2="18"
            stroke="currentColor"
            stroke-width="2"
            :transform="`rotate(${tick} 64 64)`"
            class="transition-colors duration-300"
            :class="tick <= angle ? 'text-indigo-500 dark:text-indigo-400' : 'text-slate-200 dark:text-gemini-800'"
          />
          <!-- Inner Ring -->
          <circle cx="64" cy="64" r="32" stroke="currentColor" stroke-width="1" fill="transparent" class="text-slate-200 dark:text-gemini-800" />
          <!-- Active Arc -->
          <circle
            cx="64" cy="64" r="32"
            stroke="currentColor" stroke-width="3" fill="transparent"
            :stroke-dasharray="2 * Math.PI * 32"
            :stroke-dashoffset="2 * Math.PI * 32 * (1 - (angle % 360) / 360)"
            class="text-indigo-500 dark:text-indigo-400"
            stroke-linecap="round"
          />
        </svg>
        <div class="absolute inset-0 flex flex-col items-center justify-center">
          <span class="text-xl font-mono font-bold text-slate-800 dark:text-indigo-100">{{ angle.toFixed(0) }}<span class="text-xs align-top">°</span></span>
        </div>
      </div>

      <!-- ================= Variant B: Industrial Arc ================= -->
      <div v-else-if="variant === 'industrial'" class="relative w-32 h-32">
        <!-- Gauge SVG (Rotated to open at bottom) -->
        <svg class="w-full h-full transform rotate-[150deg]">
          <!-- Track -->
          <circle
            cx="64" cy="64" r="40"
            stroke="currentColor" stroke-width="10" fill="transparent"
            :stroke-dasharray="industrialDashArray"
            stroke-linecap="butt"
            class="text-slate-100 dark:text-gemini-950"
          />
          <!-- Progress -->
          <circle
            cx="64" cy="64" r="40"
            stroke="currentColor" stroke-width="10" fill="transparent"
            :stroke-dasharray="industrialCircumference"
            :stroke-dashoffset="industrialOffset"
            stroke-linecap="butt"
            class="text-indigo-600 dark:text-indigo-500 transition-all duration-300 ease-out"
          />
        </svg>
        
        <!-- Target Pointer (CSS Rotation) -->
        <div 
           class="absolute top-0 left-0 w-full h-full pointer-events-none transition-transform duration-300"
           :style="{ transform: `rotate(${targetPointerRotate}deg)` }"
        >
           <div class="w-0 h-0 border-l-[6px] border-l-transparent border-r-[6px] border-r-transparent border-t-[8px] border-t-amber-500 absolute left-1/2 -translate-x-1/2 top-[14px]"></div>
        </div>

        <div class="absolute inset-0 flex flex-col items-center justify-center pt-4">
          <span class="text-2xl font-black font-mono text-slate-800 dark:text-slate-100 tracking-tighter">{{ angle.toFixed(0) }}</span>
          <span class="text-[10px] text-slate-400 uppercase font-bold">DEG</span>
        </div>
      </div>

      <!-- ================= Variant C: Modern Soft (Default) ================= -->
      <div v-else class="relative w-32 h-32">
        <svg class="w-full h-full transform -rotate-90">
          <!-- Track -->
          <circle
            cx="64" cy="64" r="40"
            stroke="currentColor" stroke-width="6" fill="transparent"
            class="text-slate-100 dark:text-gemini-800"
          />
          <!-- Target Indicator (Dashed) -->
          <circle
            cx="64" cy="64" r="40"
            stroke="currentColor" stroke-width="2" fill="transparent"
            stroke-dasharray="4 4"
            class="text-amber-400/50 dark:text-amber-500/50"
            :stroke-dashoffset="-(targetAngle / 360) * modernCircumference" 
          />
          <!-- Progress -->
          <circle
            cx="64" cy="64" r="40"
            stroke="currentColor" stroke-width="6" fill="transparent"
            :stroke-dasharray="modernCircumference"
            :stroke-dashoffset="modernOffset"
            class="text-indigo-500 dark:text-indigo-400 transition-all duration-300 ease-out"
            stroke-linecap="round"
          />
        </svg>
        <div class="absolute inset-0 flex flex-col items-center justify-center">
          <span class="text-2xl font-mono font-bold text-slate-800 dark:text-slate-100">{{ angle.toFixed(1) }}°</span>
          <span class="text-xs text-slate-500">位置</span>
        </div>
      </div>

    </div>

    <!-- Controls -->
    <div class="mt-4 space-y-3 z-10">
      <!-- Slider -->
      <div class="space-y-1">
        <div class="flex justify-between text-xs text-slate-500 dark:text-slate-400">
          <span>Target</span>
          <span>{{ targetAngle.toFixed(1) }}°</span>
        </div>
        <input 
          type="range" 
          min="0" 
          max="360" 
          step="0.1"
          v-model.number="targetAngle"
          @change="handleSliderChange"
          class="w-full h-1.5 bg-slate-200 dark:bg-gemini-950 rounded-lg appearance-none cursor-pointer accent-indigo-500 hover:accent-indigo-400 transition-colors"
        />
      </div>

      <!-- Quick Actions -->
      <div class="grid grid-cols-2 gap-2">
        <button 
          @click="sendDirectCmd('continuous')"
          class="flex items-center justify-center space-x-1 py-1.5 bg-slate-100 dark:bg-gemini-950 hover:bg-slate-200 dark:hover:bg-gemini-800 text-slate-600 dark:text-slate-300 rounded text-xs font-medium transition-colors"
        >
          <PlayIcon class="w-3 h-3" />
          <span>连续旋转</span>
        </button>
        <button 
          @click="sendDirectCmd('stop')"
          class="flex items-center justify-center space-x-1 py-1.5 bg-red-50 dark:bg-rose-900/20 hover:bg-red-100 dark:hover:bg-rose-900/40 text-red-500 dark:text-rose-400 rounded text-xs font-medium transition-colors"
        >
          <StopIcon class="w-3 h-3" />
          <span>停止</span>
        </button>
      </div>
    </div>
  </div>
</template>

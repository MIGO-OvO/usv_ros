<script setup>
import { ref, onMounted } from 'vue'
import { useRosStore } from '../stores/ros'
import { AdjustmentsHorizontalIcon, WrenchScrewdriverIcon, ArrowPathIcon, CheckIcon } from '@heroicons/vue/24/solid'
import MotorCard from '../components/MotorCard.vue'

const store = useRosStore()

// State
const pidConfig = ref({
  Kp: 0.14, Ki: 0.015, Kd: 0.06,
  output_min: 1.0, output_max: 8.0
})
const pidTestParams = ref({
  motor: 'X', direction: 'F', angle: 90.0, runs: 5
})
const isSaving = ref(false)

// Load Config on mount
onMounted(async () => {
  try {
    const res = await store.getPidConfig()
    if (res.success && res.data) {
      pidConfig.value = res.data
    }
  } catch (e) {
    console.error('Failed to load PID config', e)
  }
})

// Actions
const handleSavePid = async () => {
  isSaving.value = true
  await store.setPidConfig(pidConfig.value)
  setTimeout(() => isSaving.value = false, 1000)
}

const handleSetZero = async (motor) => {
  if (confirm(`确定要将 ${motor} 轴当前位置设为零点吗?`)) {
    await store.setZero(motor)
  }
}

const handleResetZero = async () => {
  if (confirm('确定要重置所有零点偏移吗?')) {
    await store.resetZero()
  }
}

const handleRunTest = async () => {
  await store.runPidTest(pidTestParams.value)
}
</script>

<template>
  <div class="h-full flex flex-col p-4 md:p-6 overflow-y-auto custom-scrollbar bg-slate-50 dark:bg-gemini-950 transition-colors duration-300">
    
    <!-- Top: Motor Grid -->
    <div class="mb-8">
      <h2 class="text-xl font-bold text-slate-800 dark:text-slate-100 mb-4 flex items-center transition-colors">
        <WrenchScrewdriverIcon class="w-6 h-6 mr-2 text-indigo-500" />
        电机调试
      </h2>
      <div class="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4 md:gap-6">
        <MotorCard 
          v-for="(val, motor) in store.motors" 
          :key="motor" 
          :motor-id="motor" 
          :angle="val"
          variant="cyber"
        />
      </div>
    </div>

    <div class="grid grid-cols-1 lg:grid-cols-2 gap-6 md:gap-8 pb-12">
      <!-- Left: Calibration & Zeroing -->
      <div class="bg-white dark:bg-gemini-900 rounded-xl border border-slate-200 dark:border-gemini-800 p-4 md:p-6 shadow-sm dark:shadow-none transition-colors">
        <h3 class="text-lg font-bold text-slate-800 dark:text-slate-200 mb-4 flex items-center transition-colors">
          <AdjustmentsHorizontalIcon class="w-5 h-5 mr-2 text-emerald-500" />
          零点标定
        </h3>
        <p class="text-sm text-slate-500 dark:text-slate-400 mb-6 transition-colors">
          将电机移动到物理零点位置，然后点击下方按钮保存偏移量。
        </p>
        
        <div class="grid grid-cols-2 gap-3 md:gap-4 mb-6">
          <button 
            v-for="m in ['X', 'Y', 'Z', 'A']" 
            :key="m"
            @click="handleSetZero(m)"
            class="py-2 px-3 md:px-4 bg-slate-100 dark:bg-gemini-950 hover:bg-slate-200 dark:hover:bg-gemini-800 text-slate-700 dark:text-slate-200 rounded-lg border border-slate-200 dark:border-gemini-800 transition-colors flex justify-between items-center text-sm md:text-base"
          >
            <span>{{ m }} 轴归零</span>
            <span class="text-xs font-mono bg-slate-200 dark:bg-gemini-900 px-1.5 py-0.5 rounded text-indigo-500 dark:text-indigo-400">Set</span>
          </button>
        </div>

        <button 
          @click="handleResetZero"
          class="w-full py-2 bg-red-50 dark:bg-rose-900/20 hover:bg-red-100 dark:hover:bg-rose-900/30 text-red-500 dark:text-rose-400 border border-red-200 dark:border-rose-900/50 rounded-lg transition-colors text-sm font-medium"
        >
          重置所有零点数据
        </button>
      </div>

      <!-- Right: PID Tuning -->
      <div class="bg-white dark:bg-gemini-900 rounded-xl border border-slate-200 dark:border-gemini-800 p-4 md:p-6 shadow-sm dark:shadow-none transition-colors">
        <div class="flex flex-col sm:flex-row justify-between items-start sm:items-center mb-6 gap-3">
          <h3 class="text-lg font-bold text-slate-800 dark:text-slate-200 flex items-center transition-colors">
            <ArrowPathIcon class="w-5 h-5 mr-2 text-amber-500" />
            PID 参数调节
          </h3>
          <button 
            @click="handleSavePid"
            class="w-full sm:w-auto flex items-center justify-center px-3 py-1.5 bg-indigo-600 hover:bg-indigo-500 text-white rounded text-sm font-bold transition-colors shadow-sm shadow-indigo-500/30"
            :disabled="isSaving"
          >
            <CheckIcon v-if="!isSaving" class="w-4 h-4 mr-1.5" />
            <ArrowPathIcon v-else class="w-4 h-4 mr-1.5 animate-spin" />
            {{ isSaving ? '保存中...' : '应用参数' }}
          </button>
        </div>

        <div class="grid grid-cols-3 gap-3 md:gap-4 mb-6">
          <div>
            <label class="block text-xs text-slate-500 dark:text-slate-500 mb-1">Kp (比例)</label>
            <input type="number" step="0.01" v-model.number="pidConfig.Kp" class="w-full bg-slate-50 dark:bg-gemini-950 border border-slate-300 dark:border-gemini-800 rounded p-2 text-slate-900 dark:text-slate-100 font-mono focus:border-indigo-500 outline-none transition-colors" />
          </div>
          <div>
            <label class="block text-xs text-slate-500 dark:text-slate-500 mb-1">Ki (积分)</label>
            <input type="number" step="0.001" v-model.number="pidConfig.Ki" class="w-full bg-slate-50 dark:bg-gemini-950 border border-slate-300 dark:border-gemini-800 rounded p-2 text-slate-900 dark:text-slate-100 font-mono focus:border-indigo-500 outline-none transition-colors" />
          </div>
          <div>
            <label class="block text-xs text-slate-500 dark:text-slate-500 mb-1">Kd (微分)</label>
            <input type="number" step="0.01" v-model.number="pidConfig.Kd" class="w-full bg-slate-50 dark:bg-gemini-950 border border-slate-300 dark:border-gemini-800 rounded p-2 text-slate-900 dark:text-slate-100 font-mono focus:border-indigo-500 outline-none transition-colors" />
          </div>
        </div>

        <div class="grid grid-cols-2 gap-3 md:gap-4 mb-6 pt-4 border-t border-slate-100 dark:border-gemini-800">
          <div>
            <label class="block text-xs text-slate-500 dark:text-slate-500 mb-1">输出下限 (V)</label>
            <input type="number" step="0.1" v-model.number="pidConfig.output_min" class="w-full bg-slate-50 dark:bg-gemini-950 border border-slate-300 dark:border-gemini-800 rounded p-2 text-slate-900 dark:text-slate-100 font-mono focus:border-indigo-500 outline-none transition-colors" />
          </div>
          <div>
            <label class="block text-xs text-slate-500 dark:text-slate-500 mb-1">输出上限 (V)</label>
            <input type="number" step="0.1" v-model.number="pidConfig.output_max" class="w-full bg-slate-50 dark:bg-gemini-950 border border-slate-300 dark:border-gemini-800 rounded p-2 text-slate-900 dark:text-slate-100 font-mono focus:border-indigo-500 outline-none transition-colors" />
          </div>
        </div>
        
        <!-- Quick Test -->
        <div class="bg-slate-50 dark:bg-gemini-950/50 p-4 rounded-lg border border-slate-200 dark:border-gemini-800 transition-colors">
           <h4 class="text-xs font-bold text-slate-500 dark:text-slate-400 mb-3 uppercase">阶跃响应测试</h4>
           <div class="flex flex-col sm:flex-row gap-2">
             <select v-model="pidTestParams.motor" class="bg-white dark:bg-gemini-900 border border-slate-300 dark:border-gemini-800 rounded px-2 py-2 sm:py-0 text-sm text-slate-900 dark:text-slate-200 outline-none focus:border-indigo-500 transition-colors">
               <option v-for="m in ['X','Y','Z','A']" :key="m" :value="m">{{ m }}轴</option>
             </select>
             <input type="number" v-model.number="pidTestParams.angle" class="w-full sm:w-24 bg-white dark:bg-gemini-900 border border-slate-300 dark:border-gemini-800 rounded px-2 py-2 sm:py-0 text-sm text-slate-900 dark:text-slate-200 outline-none focus:border-indigo-500 transition-colors" placeholder="角度" />
             <button @click="handleRunTest" class="flex-1 bg-slate-200 dark:bg-gemini-800 hover:bg-slate-300 dark:hover:bg-gemini-700 text-slate-800 dark:text-slate-200 rounded py-2 sm:py-0 text-sm font-medium transition-colors">运行测试</button>
           </div>
        </div>

      </div>
    </div>
  </div>
</template>

<style scoped>
.custom-scrollbar::-webkit-scrollbar {
  width: 6px;
}
.custom-scrollbar::-webkit-scrollbar-track {
  background: transparent;
}
.custom-scrollbar::-webkit-scrollbar-thumb {
  background: #334155;
  border-radius: 3px;
}
</style>

<script setup>
import { ref, watch, toRaw } from 'vue'
import { TrashIcon, ChevronDownIcon, ChevronUpIcon } from '@heroicons/vue/24/solid'

const props = defineProps({
  step: {
    type: Object,
    required: true
  },
  index: {
    type: Number,
    required: true
  }
})

const emit = defineEmits(['update:step', 'remove'])

// 使用 ref 及其副本，避免直接修改 props
const localStep = ref(JSON.parse(JSON.stringify(props.step)))
const isExpanded = ref(false) // 默认折叠，减少初始渲染压力

// 仅在 props 发生外部根本性变化（如重置）时同步
// 注意：这可能会与内部修改冲突，但对于列表排序场景通常是安全的
watch(() => props.step, (newVal) => {
  // 简单的深度比较，避免不必要的重置
  if (JSON.stringify(newVal) !== JSON.stringify(localStep.value)) {
    localStep.value = JSON.parse(JSON.stringify(newVal))
  }
}, { deep: true })

// 优化：仅在数据实际变动后触发更新，避免高频事件
const updateParent = () => {
  emit('update:step', toRaw(localStep.value))
}

// Helper to get motor config safely
const getMotor = (id) => {
  if (!localStep.value[id]) {
    localStep.value[id] = { enable: 'D', direction: 'F', speed: 5, angle: 0, continuous: false }
  }
  return localStep.value[id]
}

const toggleMotor = (id) => {
  const motor = getMotor(id)
  motor.enable = motor.enable === 'E' ? 'D' : 'E'
  updateParent()
}
</script>

<template>
  <div class="bg-white dark:bg-gemini-900 border border-slate-200 dark:border-gemini-800 rounded-lg mb-3 shadow-sm transition-all hover:border-indigo-300 dark:hover:border-indigo-900">
    <!-- Header Row -->
    <div 
      class="flex items-center justify-between p-3 cursor-pointer bg-slate-50 dark:bg-gemini-950/50 rounded-t-lg select-none transition-colors"
      @click="isExpanded = !isExpanded"
    >
      <div class="flex items-center space-x-3">
        <div class="w-6 h-6 rounded-full bg-slate-200 dark:bg-gemini-800 text-xs flex items-center justify-center text-slate-600 dark:text-slate-300 font-mono">
          {{ index + 1 }}
        </div>
        <input 
          v-if="isExpanded"
          type="text" 
          v-model.lazy="localStep.name"
          @change="updateParent"
          class="bg-transparent border-b border-slate-300 dark:border-gemini-800 focus:border-indigo-500 outline-none text-sm font-medium text-slate-800 dark:text-slate-200 w-48 transition-colors"
          placeholder="步骤名称"
          @click.stop
        />
        <span v-else class="text-sm font-medium text-slate-800 dark:text-slate-200">{{ localStep.name }}</span>
      </div>
      
      <div class="flex items-center space-x-2">
        <span class="text-xs text-slate-500 font-mono">{{ (localStep.interval / 1000).toFixed(1) }}s</span>
        <button @click.stop="emit('remove')" class="p-1 hover:bg-red-50 dark:hover:bg-rose-900/30 text-slate-400 hover:text-rose-500 dark:hover:text-rose-400 rounded transition-colors">
          <TrashIcon class="w-4 h-4" />
        </button>
        <component :is="isExpanded ? ChevronUpIcon : ChevronDownIcon" class="w-4 h-4 text-slate-400" />
      </div>
    </div>

    <!-- Expanded Configuration -->
    <!-- 优化：使用 v-if 代替 v-show，大幅减少 DOM 节点数量 -->
    <div v-if="isExpanded" class="p-4 border-t border-slate-100 dark:border-gemini-800/50 space-y-4">
      
      <!-- Motor Matrix -->
      <div class="grid grid-cols-1 md:grid-cols-2 gap-4">
        <div v-for="motorId in ['X', 'Y', 'Z', 'A']" :key="motorId" class="bg-slate-50 dark:bg-gemini-950/50 p-3 rounded border border-slate-200 dark:border-gemini-800/50 transition-colors">
          <div class="flex items-center justify-between mb-2">
            <span class="text-xs font-bold text-slate-500 dark:text-slate-400">{{ motorId }} 轴电机</span>
            
            <!-- iOS Switch -->
            <button 
              @click="toggleMotor(motorId)"
              class="relative inline-flex h-5 w-9 items-center rounded-full transition-colors focus:outline-none"
              :class="getMotor(motorId).enable === 'E' ? 'bg-emerald-500' : 'bg-slate-300 dark:bg-gemini-800'"
            >
              <span
                class="inline-block h-3.5 w-3.5 transform rounded-full bg-white transition-transform"
                :class="getMotor(motorId).enable === 'E' ? 'translate-x-[18px]' : 'translate-x-1'"
              />
            </button>
          </div>
          
          <div v-if="getMotor(motorId).enable === 'E'" class="grid grid-cols-2 gap-2">
            <!-- Angle -->
            <div>
              <label class="block text-[10px] text-slate-500 uppercase">角度</label>
              <div class="flex items-center space-x-1">
                <input 
                  type="number" 
                  v-model.lazy.number="getMotor(motorId).angle" 
                  @change="updateParent"
                  class="w-full bg-white dark:bg-gemini-900 border border-slate-300 dark:border-gemini-800 rounded px-2 py-1 text-xs text-slate-800 dark:text-slate-200 transition-colors"
                />
                <span class="text-xs text-slate-500 dark:text-slate-600">°</span>
              </div>
            </div>
            <!-- Speed -->
            <div>
              <label class="block text-[10px] text-slate-500 uppercase">速度</label>
              <div class="flex items-center space-x-1">
                <input 
                  type="number" 
                  v-model.lazy.number="getMotor(motorId).speed" 
                  @change="updateParent"
                  class="w-full bg-white dark:bg-gemini-900 border border-slate-300 dark:border-gemini-800 rounded px-2 py-1 text-xs text-slate-800 dark:text-slate-200 transition-colors"
                />
                <span class="text-xs text-slate-500 dark:text-slate-600">R</span>
              </div>
            </div>
            <!-- Continuous Checkbox -->
             <div class="col-span-2 flex items-center space-x-2 mt-1">
                <input 
                  type="checkbox" 
                  v-model="getMotor(motorId).continuous" 
                  @change="updateParent"
                  :id="`cont-${index}-${motorId}`"
                  class="rounded bg-white dark:bg-gemini-900 border-slate-300 dark:border-gemini-800 text-indigo-500 focus:ring-0 w-3 h-3 transition-colors" 
                />
                <label :for="`cont-${index}-${motorId}`" class="text-xs text-slate-500 dark:text-slate-400 cursor-pointer select-none">连续旋转</label>
             </div>
          </div>
        </div>
      </div>

      <!-- Step Settings -->
      <div class="flex items-center space-x-4 pt-2 border-t border-slate-100 dark:border-gemini-800/50">
        <div class="flex-1">
           <label class="block text-xs text-slate-500 mb-1">等待时长 (秒)</label>
           <input 
             type="number" 
             :value="localStep.interval / 1000"
             @change="(e) => { localStep.interval = parseFloat(e.target.value) * 1000; updateParent() }"
             step="0.1"
             min="0"
             class="w-full bg-slate-50 dark:bg-gemini-950 border border-slate-300 dark:border-gemini-800 rounded px-3 py-1.5 text-sm text-slate-800 dark:text-slate-200 focus:border-indigo-500 focus:ring-1 focus:ring-indigo-500 outline-none transition-colors"
           />
        </div>
      </div>

    </div>
  </div>
</template>

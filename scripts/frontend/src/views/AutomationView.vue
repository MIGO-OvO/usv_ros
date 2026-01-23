<script setup>
import { onMounted } from 'vue'
import draggable from 'vuedraggable'
import { 
  PlayIcon, PauseIcon, StopIcon, PlusIcon, ArrowPathIcon, 
  CloudArrowUpIcon, DocumentArrowDownIcon 
} from '@heroicons/vue/24/solid'
import { useAutomationStore } from '../stores/automation'
import { useRosStore } from '../stores/ros'
import StepCard from '../components/StepCard.vue'

const store = useAutomationStore()
const rosStore = useRosStore()

onMounted(() => {
  store.loadConfig()
})

const handleStart = async () => {
  await store.startMission()
}
</script>

<template>
  <div class="h-full flex flex-col bg-slate-50 dark:bg-gemini-950 overflow-hidden transition-colors duration-300">
    <!-- Header Controls -->
    <div class="p-4 md:p-6 pb-2">
      <div class="flex flex-col md:flex-row justify-between items-start md:items-end mb-4 md:mb-6 gap-4">
        <div>
          <h2 class="text-xl md:text-2xl font-bold text-slate-800 dark:text-slate-100 flex items-center transition-colors">
             <ArrowPathIcon class="w-6 h-6 mr-2 text-indigo-500" />
             自动化序列
          </h2>
          <p class="text-slate-500 dark:text-slate-400 text-sm mt-1 hidden md:block">设计并执行采样任务流程。</p>
        </div>
        
        <!-- Action Buttons -->
        <div class="flex flex-wrap items-center gap-2 md:gap-3 w-full md:w-auto">
          <div class="bg-white dark:bg-gemini-900 rounded-lg p-1 flex items-center border border-slate-200 dark:border-gemini-800 transition-colors shadow-sm grow md:grow-0">
             <span class="text-xs text-slate-500 uppercase px-2 md:px-3 font-bold whitespace-nowrap">循环</span>
             <input 
               type="number" 
               v-model.number="store.loopCount" 
               class="w-full md:w-16 bg-slate-50 dark:bg-gemini-950 border border-slate-200 dark:border-gemini-800 rounded px-2 py-1 text-sm text-center text-slate-800 dark:text-slate-100 focus:border-indigo-500 outline-none transition-colors"
             />
          </div>

          <button 
            @click="store.saveConfig()"
            class="flex items-center justify-center px-3 md:px-4 py-2 bg-white dark:bg-gemini-900 hover:bg-slate-50 dark:hover:bg-gemini-800 text-slate-600 dark:text-slate-300 rounded-lg border border-slate-200 dark:border-gemini-800 transition-colors text-sm font-medium shadow-sm grow md:grow-0"
          >
            <CloudArrowUpIcon class="w-4 h-4 md:mr-2" />
            <span class="hidden md:inline">保存配置</span>
            <span class="md:hidden ml-1">保存</span>
          </button>
          
          <div class="h-8 w-px bg-slate-200 dark:bg-gemini-800 mx-1 hidden md:block"></div>

          <button 
            v-if="!rosStore.automationRunning"
            @click="handleStart"
            class="flex items-center justify-center px-4 md:px-6 py-2 bg-emerald-600 hover:bg-emerald-500 text-white rounded-lg shadow-lg shadow-emerald-500/30 dark:shadow-emerald-900/30 transition-all transform active:scale-95 font-bold grow md:grow-0"
          >
            <PlayIcon class="w-5 h-5 md:mr-2" />
            <span class="hidden md:inline">启动任务</span>
            <span class="md:hidden ml-1">启动</span>
          </button>
          
          <template v-else>
             <button 
               @click="store.stopMission()"
               class="flex items-center justify-center px-4 md:px-6 py-2 bg-rose-600 hover:bg-rose-500 text-white rounded-lg shadow-lg shadow-rose-500/30 dark:shadow-rose-900/30 font-bold grow md:grow-0"
             >
               <StopIcon class="w-5 h-5 md:mr-2" />
               <span class="hidden md:inline">停止</span>
               <span class="md:hidden ml-1">停止</span>
             </button>
          </template>
        </div>
      </div>
      
      <!-- Progress Bar (Visible when running) -->
      <div v-if="rosStore.automationRunning" class="mb-4 bg-white dark:bg-gemini-900 rounded-full h-2 overflow-hidden border border-slate-200 dark:border-gemini-800 shadow-inner">
        <div class="bg-indigo-500 h-full transition-all duration-500 animate-pulse" style="width: 60%"></div>
      </div>
    </div>

    <!-- Scrollable Sequence Editor -->
    <div class="flex-1 overflow-y-auto px-4 md:px-6 pb-20 custom-scrollbar">
      <draggable 
        v-model="store.steps" 
        item-key="name"
        handle=".cursor-pointer"
        ghost-class="opacity-50"
        :animation="200"
        class="space-y-2"
      >
        <template #item="{ element, index }">
          <StepCard 
            :step="element" 
            :index="index"
            @update:step="(val) => store.updateStep(index, val)"
            @remove="store.removeStep(index)"
          />
        </template>
      </draggable>

      <!-- Empty State -->
      <div v-if="store.steps.length === 0" class="text-center py-12 border-2 border-dashed border-slate-300 dark:border-gemini-800 rounded-xl mt-4 bg-white/50 dark:bg-gemini-900/50">
        <DocumentArrowDownIcon class="w-12 h-12 text-slate-400 dark:text-slate-600 mx-auto mb-3" />
        <h3 class="text-slate-500 dark:text-slate-400 font-medium">暂无步骤</h3>
        <p class="text-slate-400 dark:text-slate-500 text-sm mt-1">请添加步骤以开始配置序列。</p>
      </div>

      <!-- Add Button -->
      <button 
        @click="store.addStep()"
        class="w-full py-3 mt-4 border-2 border-dashed border-slate-300 dark:border-gemini-800 text-slate-500 dark:text-slate-500 hover:border-indigo-500 hover:text-indigo-500 hover:bg-slate-50 dark:hover:bg-indigo-500/10 rounded-xl transition-all flex items-center justify-center font-medium bg-white/50 dark:bg-gemini-900/50"
      >
        <PlusIcon class="w-5 h-5 mr-2" />
        添加新步骤
      </button>
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

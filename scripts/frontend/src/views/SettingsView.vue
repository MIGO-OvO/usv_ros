<script setup>
import { ref, onMounted } from 'vue'
import { useRosStore } from '../stores/ros'
import { TrashIcon, ArrowDownTrayIcon, ArrowPathIcon, TableCellsIcon } from '@heroicons/vue/24/solid'

const store = useRosStore()
const records = ref([])
const isLoading = ref(false)

const loadRecords = async () => {
  isLoading.value = true
  try {
    const res = await store.listRecords()
    if (res.success) {
      records.value = res.data
    }
  } catch (e) {
    console.error('Failed to load records', e)
  } finally {
    isLoading.value = false
  }
}

const handleDelete = async (filename) => {
  if (confirm(`确定要永久删除文件 ${filename} 吗?`)) {
    await store.deleteRecord(filename)
    await loadRecords()
  }
}

const formatSize = (bytes) => {
  if (bytes === 0) return '0 B'
  const k = 1024
  const sizes = ['B', 'KB', 'MB', 'GB']
  const i = Math.floor(Math.log(bytes) / Math.log(k))
  return parseFloat((bytes / Math.pow(k, i)).toFixed(2)) + ' ' + sizes[i]
}

onMounted(() => {
  loadRecords()
})
</script>

<template>
  <div class="h-full flex flex-col p-6 overflow-y-auto custom-scrollbar bg-slate-50 dark:bg-gemini-950 transition-colors duration-300">
    <div class="flex justify-between items-center mb-6">
      <h2 class="text-2xl font-bold text-slate-800 dark:text-slate-100 flex items-center transition-colors">
         <TableCellsIcon class="w-6 h-6 mr-2 text-indigo-500" />
         数据文件管理
      </h2>
      <button 
        @click="loadRecords" 
        class="p-2 bg-white dark:bg-gemini-900 hover:bg-slate-100 dark:hover:bg-gemini-800 rounded-lg text-slate-500 dark:text-slate-400 hover:text-slate-900 dark:hover:text-white transition-colors border border-slate-200 dark:border-gemini-800 shadow-sm"
      >
        <ArrowPathIcon class="w-5 h-5" :class="{ 'animate-spin': isLoading }" />
      </button>
    </div>

    <div class="bg-white dark:bg-gemini-900 border border-slate-200 dark:border-gemini-800 rounded-xl overflow-hidden shadow-sm dark:shadow-none transition-colors">
      <table class="w-full text-left text-sm text-slate-600 dark:text-slate-400">
        <thead class="bg-slate-50 dark:bg-gemini-950/50 text-slate-800 dark:text-slate-200 uppercase font-bold text-xs">
          <tr>
            <th class="px-6 py-4">文件名</th>
            <th class="px-6 py-4">大小</th>
            <th class="px-6 py-4">修改时间</th>
            <th class="px-6 py-4 text-right">操作</th>
          </tr>
        </thead>
        <tbody class="divide-y divide-slate-100 dark:divide-gemini-800/50">
          <tr v-for="file in records" :key="file.name" class="hover:bg-slate-50 dark:hover:bg-gemini-800/20 transition-colors">
            <td class="px-6 py-4 font-mono text-slate-800 dark:text-slate-200">{{ file.name }}</td>
            <td class="px-6 py-4">{{ formatSize(file.size) }}</td>
            <td class="px-6 py-4">{{ new Date(file.modified).toLocaleString() }}</td>
            <td class="px-6 py-4 text-right space-x-2">
              <a 
                :href="`/api/records/download/${file.name}`" 
                target="_blank"
                class="inline-flex p-1.5 text-blue-500 dark:text-indigo-400 hover:text-blue-600 dark:hover:text-indigo-300 hover:bg-blue-50 dark:hover:bg-indigo-500/10 rounded transition-colors"
                title="下载"
              >
                <ArrowDownTrayIcon class="w-4 h-4" />
              </a>
              <button 
                @click="handleDelete(file.name)"
                class="inline-flex p-1.5 text-red-500 dark:text-rose-400 hover:text-red-600 dark:hover:text-rose-300 hover:bg-red-50 dark:hover:bg-rose-500/10 rounded transition-colors"
                title="删除"
              >
                <TrashIcon class="w-4 h-4" />
              </button>
            </td>
          </tr>
          <tr v-if="records.length === 0">
            <td colspan="4" class="px-6 py-12 text-center text-slate-400 dark:text-slate-500">
              暂无数据文件
            </td>
          </tr>
        </tbody>
      </table>
    </div>
    
    <!-- System Info Footer -->
    <div class="mt-8 p-6 bg-white dark:bg-gemini-900 rounded-xl border border-slate-200 dark:border-gemini-800 transition-colors shadow-sm">
       <h3 class="font-bold text-slate-800 dark:text-slate-200 mb-2">关于系统</h3>
       <div class="grid grid-cols-2 gap-4 text-sm text-slate-500 dark:text-slate-400">
          <p>Version: v1.0.0 (Vue 3 + Vite)</p>
          <p>ROS Distribution: Melodic/Noetic Compatible</p>
          <p>Backend: Flask 2.0 + SocketIO</p>
          <p>Build Date: 2026-01-24</p>
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

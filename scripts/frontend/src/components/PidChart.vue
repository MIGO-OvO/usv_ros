<script setup>
import { computed } from 'vue'
import VueApexCharts from 'vue3-apexcharts'
import { useThemeStore } from '../stores/theme'

const props = defineProps({
  series: {
    type: Array,
    default: () => []
  }
})

const themeStore = useThemeStore()

const chartOptions = computed(() => {
  const isDark = themeStore.isDark
  return {
    chart: {
      type: 'line',
      height: 250,
      background: 'transparent',
      toolbar: { show: false },
      animations: {
        enabled: true,
        easing: 'linear',
        dynamicAnimation: { speed: 200 }
      }
    },
    colors: isDark ? ['#818cf8', '#fbbf24'] : ['#3b82f6', '#eab308'], // Indigo-400/Amber-400 (Dark) vs Blue-500/Yellow-500 (Light)
    stroke: {
      curve: 'smooth',
      width: 2,
      dashArray: [0, 5]
    },
    grid: {
      borderColor: isDark ? '#1f2937' : '#E2E8F0', // gemini-800 vs slate-200
      strokeDashArray: 4,
    },
    xaxis: {
      type: 'datetime',
      tooltip: { enabled: false },
      axisBorder: { show: false },
      axisTicks: { show: false },
      labels: {
        style: { colors: isDark ? '#9ca3af' : '#64748B' },
        datetimeFormatter: {
          year: 'yyyy',
          month: "MMM 'yy",
          day: 'dd MMM',
          hour: 'HH:mm',
        }
      }
    },
    yaxis: {
      labels: {
        style: { colors: isDark ? '#9ca3af' : '#64748B' }
      }
    },
    theme: { mode: isDark ? 'dark' : 'light' },
    tooltip: {
      theme: isDark ? 'dark' : 'light',
      x: { format: 'HH:mm:ss' }
    },
    legend: {
      position: 'top',
      horizontalAlign: 'right',
      labels: { colors: isDark ? '#e5e7eb' : '#1E293B' }
    }
  }
})
</script>

<template>
  <div class="bg-white dark:bg-gemini-900 rounded-xl border border-slate-200 dark:border-gemini-800 shadow-sm dark:shadow-none p-4 h-full flex flex-col transition-colors duration-300">
    <div class="flex justify-between items-center mb-2">
      <h3 class="font-bold text-slate-800 dark:text-slate-200 transition-colors">PID 性能监控</h3>
      <div class="flex items-center space-x-2 text-xs text-slate-500 dark:text-slate-400">
        <span class="flex items-center"><span class="w-2 h-2 rounded-full bg-blue-500 dark:bg-indigo-400 mr-1"></span> 实际值</span>
        <span class="flex items-center"><span class="w-2 h-2 rounded-full bg-yellow-500 dark:bg-amber-400 mr-1"></span> 目标值</span>
      </div>
    </div>
    <div class="flex-1 min-h-0">
      <VueApexCharts
        type="line"
        height="100%"
        :options="chartOptions"
        :series="series"
      />
    </div>
  </div>
</template>

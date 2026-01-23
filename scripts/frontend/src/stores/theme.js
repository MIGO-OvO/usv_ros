import { defineStore } from 'pinia'
import { ref, watchEffect } from 'vue'

export const useThemeStore = defineStore('theme', () => {
  // 默认从 localStorage 读取，没有则跟随系统
  const savedTheme = localStorage.getItem('theme')
  const systemDark = window.matchMedia('(prefers-color-scheme: dark)').matches
  
  const isDark = ref(savedTheme ? savedTheme === 'dark' : systemDark)

  // 监听变化并应用到 DOM
  watchEffect(() => {
    const root = document.documentElement
    if (isDark.value) {
      root.classList.add('dark')
      localStorage.setItem('theme', 'dark')
    } else {
      root.classList.remove('dark')
      localStorage.setItem('theme', 'light')
    }
  })

  function toggle() {
    isDark.value = !isDark.value
  }

  return { isDark, toggle }
})

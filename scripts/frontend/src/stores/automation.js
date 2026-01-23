import { defineStore } from 'pinia'
import { ref } from 'vue'
import { useRosStore } from './ros'

export const useAutomationStore = defineStore('automation', () => {
  const rosStore = useRosStore()
  
  // State
  const steps = ref([])
  const loopCount = ref(1)
  const isDirty = ref(false)
  
  // Running Status
  const currentStepIndex = ref(0)
  const progress = ref(0)
  
  // Actions
  async function loadConfig() {
    try {
      const res = await fetch('/api/config')
      const data = await res.json()
      if (data.sampling_sequence) {
        steps.value = data.sampling_sequence.steps || []
        loopCount.value = data.sampling_sequence.loop_count || 1
      }
    } catch (e) {
      console.error('Failed to load automation config', e)
    }
  }

  async function saveConfig() {
    try {
      const payload = {
        sampling_sequence: {
          steps: steps.value,
          loop_count: loopCount.value
        }
      }
      const res = await fetch('/api/config', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload)
      })
      const data = await res.json()
      if (data.success) {
        isDirty.value = false
        return true
      }
    } catch (e) {
      console.error('Failed to save config', e)
    }
    return false
  }

  async function startMission() {
    await saveConfig() // Always save before start
    return fetch('/api/mission/start', { method: 'POST' }).then(res => res.json())
  }

  async function stopMission() {
    return fetch('/api/mission/stop', { method: 'POST' }).then(res => res.json())
  }
  
  async function pauseMission() {
    return fetch('/api/mission/pause', { method: 'POST' }).then(res => res.json())
  }

  async function resumeMission() {
    return fetch('/api/mission/resume', { method: 'POST' }).then(res => res.json())
  }

  function addStep() {
    steps.value.push({
      name: `Step ${steps.value.length + 1}`,
      interval: 1000,
      X: { enable: 'D', speed: 5, angle: 0 },
      Y: { enable: 'D', speed: 5, angle: 0 },
      Z: { enable: 'D', speed: 5, angle: 0 },
      A: { enable: 'D', speed: 5, angle: 0 }
    })
    isDirty.value = true
  }

  function removeStep(index) {
    steps.value.splice(index, 1)
    isDirty.value = true
  }

  function updateStep(index, newStep) {
    steps.value[index] = newStep
    isDirty.value = true
  }

  return {
    steps,
    loopCount,
    isDirty,
    currentStepIndex,
    progress,
    loadConfig,
    saveConfig,
    startMission,
    stopMission,
    pauseMission,
    resumeMission,
    addStep,
    removeStep,
    updateStep
  }
})

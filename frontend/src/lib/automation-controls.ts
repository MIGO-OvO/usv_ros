export type AutomationAction = 'start' | 'pause' | 'resume' | 'stop'

interface AutomationState {
  running: boolean
  paused: boolean
}

export function getAutomationControlAvailability(state: AutomationState) {
  const active = state.running || state.paused

  return {
    start: !state.running || state.paused,
    pause: state.running && !state.paused,
    resume: state.paused,
    stop: active,
  }
}

export function resolveAutomationAction(
  requestedAction: AutomationAction,
  state: AutomationState,
): AutomationAction {
  if (requestedAction === 'start' && state.paused) {
    return 'resume'
  }
  return requestedAction
}

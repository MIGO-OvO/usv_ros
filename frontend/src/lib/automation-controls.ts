export type AutomationAction = 'start' | 'pause' | 'resume' | 'stop'

interface AutomationState {
  running: boolean
  paused: boolean
  preflight?: boolean
}

export function getAutomationControlAvailability(state: AutomationState) {
  const active = Boolean(state.running || state.paused || state.preflight)

  return {
    start: !state.preflight && (!state.running || state.paused),
    pause: !state.preflight && state.running && !state.paused,
    resume: !state.preflight && state.paused,
    stop: active,
  }
}

export function resolveAutomationAction(
  requestedAction: AutomationAction,
  state: AutomationState,
): AutomationAction {
  if (requestedAction === 'start' && state.paused && !state.preflight) {
    return 'resume'
  }
  return requestedAction
}

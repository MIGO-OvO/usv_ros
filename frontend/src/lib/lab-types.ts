export interface LatLng {
  readonly lat: number
  readonly lng: number
}

export interface Waypoint extends LatLng {
  readonly seq: number
}

export interface LabMission {
  readonly waypoints: readonly Waypoint[]
  readonly center: LatLng | null
}

export interface LabPollution {
  readonly mode: 'center' | 'manual'
  readonly source: LatLng | null
  readonly strength: number
  readonly radius_m: number
  readonly reference_voltage: number
  readonly value_min: number
  readonly value_max: number
}

export interface WaterArea {
  readonly enabled: boolean
  readonly polygon: readonly LatLng[]
}

export interface LabConfig {
  readonly enabled: boolean
  readonly profile: string
  readonly position_source: string
  readonly data_source: 'simulated' | 'real'
  readonly allow_no_gps: boolean
  readonly bypass_pid_wait: boolean
  readonly include_lab_data_by_default: boolean
  readonly sim: {
    readonly start_lat: number
    readonly start_lng: number
    readonly heading_deg: number
    readonly max_speed_mps: number
    readonly wheel_base_m: number
    readonly arrival_radius_m: number
    readonly sample_dwell_s: number
  }
  readonly mission: LabMission
  readonly pollution: LabPollution
  readonly water_area: WaterArea
}

export interface LabStatus {
  readonly enabled: boolean
  readonly running: boolean
  readonly speed_mps: number
  readonly heading_deg: number
  readonly mission?: {
    readonly active: boolean
    readonly total: number
    readonly target_seq: number | null
    readonly reached_count: number
    readonly completed: boolean
    readonly waiting_sampling_done: boolean
  }
  readonly sampling?: {
    readonly active: boolean
    readonly status: string
    readonly mission_status: string
    readonly started_at: string | null
    readonly duration_s: number
    readonly elapsed_s: number
    readonly remaining_s: number
    readonly progress_percent: number
  }
  readonly signal?: {
    readonly value: number
    readonly absorbance: number
    readonly status: string
    readonly simulated: boolean
    readonly valid: boolean
    readonly pollution_value: number | null
    readonly waypoint_seq: number | null
    readonly timestamp: number | string | null
    readonly raw: Record<string, unknown>
  }
  readonly trigger_status?: {
    readonly status: string
    readonly received_at: string | null
  }
  readonly virtual_propulsion: {
    readonly left: number
    readonly right: number
    readonly real_output_enabled: boolean
  }
}

export interface MapConfigLite {
  readonly tile_url: string
  readonly default_style: string
  readonly min_zoom: number
  readonly max_zoom: number
  readonly default_center: { readonly lng: number; readonly lat: number }
  readonly default_zoom: number
}

export const fallbackConfig: LabConfig = {
  enabled: false,
  profile: 'semi_hardware',
  position_source: 'lab_sim',
  data_source: 'simulated',
  allow_no_gps: true,
  bypass_pid_wait: true,
  include_lab_data_by_default: false,
  sim: { start_lat: 25.314167, start_lng: 110.412778, heading_deg: 0, max_speed_mps: 1, wheel_base_m: 0.6, arrival_radius_m: 3, sample_dwell_s: 3 },
  mission: { waypoints: [], center: null },
  pollution: { mode: 'center', source: null, strength: 0.8, radius_m: 150, reference_voltage: 3, value_min: 0, value_max: 1 },
  water_area: { enabled: false, polygon: [] },
}

export const fallbackStatus: LabStatus = {
  enabled: false,
  running: false,
  speed_mps: 0,
  heading_deg: 0,
  mission: {
    active: false,
    total: 0,
    target_seq: null,
    reached_count: 0,
    completed: false,
    waiting_sampling_done: false,
  },
  sampling: {
    active: false,
    status: '',
    mission_status: 'IDLE',
    started_at: null,
    duration_s: 0,
    elapsed_s: 0,
    remaining_s: 0,
    progress_percent: 0,
  },
  signal: {
    value: 0,
    absorbance: 0,
    status: 'idle',
    simulated: false,
    valid: false,
    pollution_value: null,
    waypoint_seq: null,
    timestamp: null,
    raw: {},
  },
  trigger_status: { status: '', received_at: null },
  virtual_propulsion: { left: 0, right: 0, real_output_enabled: false },
}

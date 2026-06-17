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
  }
  readonly mission: LabMission
  readonly pollution: LabPollution
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
  sim: { start_lat: 25.314167, start_lng: 110.412778, heading_deg: 0, max_speed_mps: 1, wheel_base_m: 0.6, arrival_radius_m: 3 },
  mission: { waypoints: [], center: null },
  pollution: { mode: 'center', source: null, strength: 0.8, radius_m: 150, reference_voltage: 3 },
}

export const fallbackStatus: LabStatus = {
  enabled: false,
  running: false,
  speed_mps: 0,
  heading_deg: 0,
  mission: { active: false, total: 0, target_seq: null, reached_count: 0 },
  virtual_propulsion: { left: 0, right: 0, real_output_enabled: false },
}

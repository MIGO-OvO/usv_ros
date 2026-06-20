export interface CoordinateValue {
  readonly lat: number
  readonly lng: number
  readonly alt?: number | null
}

export interface LabCoordinatePair {
  readonly coordinate_schema_version: 2
  readonly wgs84: CoordinateValue
  readonly gcj02: CoordinateValue
}

export interface Gcj02CoordinateInput {
  readonly input_crs: 'GCJ02'
  readonly gcj02: CoordinateValue
}

export type LabCoordinateWrite = LabCoordinatePair | Gcj02CoordinateInput

export type Waypoint = LabCoordinatePair & {
  readonly seq: number
}

export type WaypointWrite = LabCoordinateWrite & {
  readonly seq: number
}

export interface LabMission {
  readonly waypoints: readonly Waypoint[]
  readonly center: LabCoordinatePair | null
}

export interface LabMissionWrite {
  readonly waypoints: readonly WaypointWrite[]
  readonly center: LabCoordinateWrite | null
}

export interface LabPollution {
  readonly mode: 'center' | 'manual'
  readonly source: LabCoordinatePair | null
  readonly strength: number
  readonly radius_m: number
  readonly reference_voltage: number
  readonly value_min: number
  readonly value_max: number
  readonly analyte_id?: string
  readonly name?: string
  readonly unit?: string
}

export type LabSamplingMode = 'waypoint' | 'survey'

export interface LabNoiseConfig {
  readonly enabled: boolean
  readonly voltage_noise: number
  readonly absorbance_noise: number
  readonly concentration_noise: number
}

export interface LabAnalyteConfig {
  readonly analyte_id: string
  readonly name: string
  readonly unit: string
}

export interface LabPollutionSourceConfig {
  readonly source_id: string
  readonly analyte_id: string
  readonly peak_concentration: number
}

export interface LabAutoScanParams {
  readonly strip_spacing_m: number
  readonly heading_deg: number
  readonly inward_margin_m: number
  readonly max_waypoints: number
}

export interface WaterArea {
  readonly enabled: boolean
  readonly polygon: readonly LabCoordinatePair[]
}

export interface WaterAreaWrite {
  readonly enabled: boolean
  readonly polygon: readonly LabCoordinateWrite[]
}

export interface LabConfig {
  readonly coordinate_schema_version: 2
  readonly enabled: boolean
  readonly profile: string
  readonly position_source: string
  readonly data_source: 'simulated' | 'real'
  readonly sampling_mode?: LabSamplingMode
  readonly droplet_count?: number
  readonly seed?: number
  readonly noise?: LabNoiseConfig
  readonly analytes?: readonly LabAnalyteConfig[]
  readonly sources?: readonly LabPollutionSourceConfig[]
  readonly auto_scan?: LabAutoScanParams
  readonly allow_no_gps: boolean
  readonly bypass_pid_wait: boolean
  readonly include_lab_data_by_default: boolean
  readonly sim: {
    readonly start: LabCoordinatePair
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

export type LabConfigWrite = Omit<LabConfig, 'sim' | 'mission' | 'pollution' | 'water_area'> & {
  readonly sim: Omit<LabConfig['sim'], 'start'> & {
    readonly start: LabCoordinateWrite
  }
  readonly mission: LabMissionWrite
  readonly pollution: Omit<LabPollution, 'source'> & {
    readonly source: LabCoordinateWrite | null
  }
  readonly water_area: WaterAreaWrite
}

export type LatLng = CoordinateValue

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
    readonly droplet_count?: number
    readonly valid_count?: number
    readonly aggregate_value?: number | null
    readonly latest_event?: {
      readonly event_id: string
      readonly mode: string
      readonly analyte_id: string
      readonly droplet_count: number
      readonly valid_count: number
      readonly mean: number | null
      readonly median: number | null
      readonly standard_deviation: number | null
      readonly progress_percent: number
    }
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

export interface LabAutoScanRequest {
  readonly input_crs: 'GCJ02'
  readonly polygon: readonly LabCoordinateWrite[]
  readonly strip_spacing_m: number
  readonly heading_deg: number
  readonly inward_margin_m: number
  readonly max_waypoints: number
  readonly preview: boolean
}

export interface LabAutoScanResponse {
  readonly route_waypoints: readonly Waypoint[]
  readonly water_snapshot_hash: string
  readonly waypoint_count: number
  readonly preview: boolean
  readonly saved: boolean
  readonly parameters: LabAutoScanParams
}

export const fallbackConfig: LabConfig = {
  coordinate_schema_version: 2,
  enabled: false,
  profile: 'semi_hardware',
  position_source: 'lab_sim',
  data_source: 'simulated',
  sampling_mode: 'waypoint',
  droplet_count: 12,
  seed: 1,
  noise: {
    enabled: false,
    voltage_noise: 0,
    absorbance_noise: 0,
    concentration_noise: 0,
  },
  analytes: [{ analyte_id: 'sim', name: '模拟污染物', unit: 'mg/L' }],
  sources: [{ source_id: 'lab-source', analyte_id: 'sim', peak_concentration: 1 }],
  auto_scan: {
    strip_spacing_m: 10,
    heading_deg: 90,
    inward_margin_m: 1,
    max_waypoints: 200,
  },
  allow_no_gps: true,
  bypass_pid_wait: true,
  include_lab_data_by_default: false,
  sim: {
    start: {
      coordinate_schema_version: 2,
      wgs84: { lat: 25.32259176452231, lng: 110.39774012635971 },
      gcj02: { lat: 25.314167, lng: 110.412778 },
    },
    start_lat: 25.314167,
    start_lng: 110.412778,
    heading_deg: 0,
    max_speed_mps: 1,
    wheel_base_m: 0.6,
    arrival_radius_m: 3,
    sample_dwell_s: 3,
  },
  mission: { waypoints: [], center: null },
  pollution: { mode: 'center', source: null, strength: 0.8, radius_m: 150, reference_voltage: 3, value_min: 0, value_max: 1, analyte_id: 'sim', name: '模拟污染物', unit: 'mg/L' },
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

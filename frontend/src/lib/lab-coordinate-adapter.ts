import type {
  CoordinateValue,
  Gcj02CoordinateInput,
  LabConfig,
  LabConfigWrite,
  LabCoordinatePair,
  LabMission,
  LabMissionWrite,
  WaterArea,
  WaterAreaWrite,
} from '@/lib/lab-types'

export function gcj02Input(position: CoordinateValue): Gcj02CoordinateInput {
  return {
    input_crs: 'GCJ02',
    gcj02: {
      lat: position.lat,
      lng: position.lng,
    },
  }
}

export function gcj02ForDrawing(coordinate: LabCoordinatePair): CoordinateValue {
  return coordinate.gcj02
}

export function configWriteForManualStart(config: LabConfig): LabConfigWrite {
  const savedStart = config.sim.start.gcj02
  if (
    config.sim.start_lat === savedStart.lat
    && config.sim.start_lng === savedStart.lng
  ) {
    return config
  }
  return {
    ...config,
    sim: {
      ...config.sim,
      start: gcj02Input({
        lat: config.sim.start_lat,
        lng: config.sim.start_lng,
      }),
    },
  }
}

export function configWriteWithGcj02Start(
  config: LabConfig,
  position: CoordinateValue,
): LabConfigWrite {
  return {
    ...config,
    sim: {
      ...config.sim,
      start: gcj02Input(position),
      start_lat: position.lat,
      start_lng: position.lng,
    },
  }
}

export function missionWriteWithGcj02Waypoint(
  mission: LabMission,
  position: CoordinateValue,
): LabMissionWrite {
  return {
    waypoints: [
      ...mission.waypoints,
      {
        ...gcj02Input(position),
        seq: mission.waypoints.length,
      },
    ],
    center: null,
  }
}

export function configWriteWithGcj02PollutionSource(
  config: LabConfig,
  position: CoordinateValue,
): LabConfigWrite {
  return {
    ...config,
    pollution: {
      ...config.pollution,
      mode: 'manual',
      source: gcj02Input(position),
    },
  }
}

export function waterAreaWriteWithGcj02Vertex(
  waterArea: WaterArea,
  position: CoordinateValue,
): WaterAreaWrite {
  return {
    enabled: waterArea.enabled,
    polygon: [
      ...waterArea.polygon,
      gcj02Input(position),
    ],
  }
}

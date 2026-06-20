import { useCallback, useEffect, useRef, useState } from 'react'
import type { Dispatch, RefObject, SetStateAction } from 'react'
import L from 'leaflet'
import 'leaflet/dist/leaflet.css'
import {
  configWriteWithGcj02PollutionSource,
  configWriteWithGcj02Start,
  gcj02ForDrawing,
  missionWriteWithGcj02Waypoint,
  waterAreaWriteWithGcj02Vertex,
} from '@/lib/lab-coordinate-adapter'
import { boatIcon, isFiniteLatLng, moveBoatMarker, startIcon } from '@/lib/lab-map'
import type {
  LabConfig,
  LabConfigWrite,
  LabMission,
  LabMissionWrite,
  LatLng,
  MapConfigLite,
  Waypoint,
  WaterArea,
  WaterAreaWrite,
} from '@/lib/lab-types'

type DrawMode = '' | 'start' | 'waypoint' | 'source' | 'water_area'

interface UseLabMapArgs {
  readonly config: LabConfig
  readonly pending: string
  readonly setConfig: Dispatch<SetStateAction<LabConfig>>
  readonly setMessage: (message: string) => void
  readonly persistConfig: (nextConfig: LabConfigWrite) => Promise<LabConfig | null>
  readonly persistMission: (mission: LabMissionWrite) => Promise<LabMission | null>
  readonly persistWaterArea: (waterArea: WaterAreaWrite) => Promise<WaterArea | null>
}

interface UseLabMapResult {
  readonly containerRef: RefObject<HTMLDivElement | null>
  readonly drawMode: DrawMode
  readonly setDrawMode: Dispatch<SetStateAction<DrawMode>>
  readonly hasLabBounds: boolean
  readonly fitLabBounds: () => void
  readonly setBoatToStart: (simConfig: LabConfig['sim']) => void
  readonly updateBoatPosition: (position: LatLng, headingDeg: number) => void
  readonly setPreviewRoute: (waypoints: readonly Waypoint[]) => void
  readonly clearPreviewRoute: () => void
  readonly markDirty: () => void
  readonly clearDirty: () => void
  readonly canAcceptRemoteConfig: () => boolean
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null
}

function readMapConfig(value: unknown): MapConfigLite | null {
  if (!isRecord(value) || !isRecord(value.data)) return null
  const data = value.data
  const center = data.default_center
  if (
    typeof data.tile_url !== 'string' ||
    typeof data.default_style !== 'string' ||
    typeof data.min_zoom !== 'number' ||
    typeof data.max_zoom !== 'number' ||
    typeof data.default_zoom !== 'number' ||
    !isRecord(center) ||
    typeof center.lat !== 'number' ||
    typeof center.lng !== 'number'
  ) {
    return null
  }
  return {
    tile_url: data.tile_url,
    default_style: data.default_style,
    min_zoom: data.min_zoom,
    max_zoom: data.max_zoom,
    default_center: { lat: center.lat, lng: center.lng },
    default_zoom: data.default_zoom,
  }
}

export function useLabMap({
  config,
  pending,
  setConfig,
  setMessage,
  persistConfig,
  persistMission,
  persistWaterArea,
}: UseLabMapArgs): UseLabMapResult {
  const containerRef = useRef<HTMLDivElement | null>(null)
  const mapRef = useRef<L.Map | null>(null)
  const layerRef = useRef<L.LayerGroup | null>(null)
  const previewLayerRef = useRef<L.LayerGroup | null>(null)
  const boatRef = useRef<L.Marker | null>(null)
  const labBoundsRef = useRef<L.LatLngBounds | null>(null)
  const labInitialFitDoneRef = useRef(false)
  const configRef = useRef(config)
  const drawModeRef = useRef<DrawMode>('')
  const pendingRef = useRef(pending)
  const dirtyRef = useRef(false)
  const [drawMode, setDrawMode] = useState<DrawMode>('')
  const [mapReady, setMapReady] = useState(false)
  const [hasLabBounds, setHasLabBounds] = useState(false)

  useEffect(() => {
    configRef.current = config
  }, [config])

  useEffect(() => {
    drawModeRef.current = drawMode
  }, [drawMode])

  useEffect(() => {
    pendingRef.current = pending
  }, [pending])

  const markDirty = useCallback(() => {
    dirtyRef.current = true
  }, [])

  const clearDirty = useCallback(() => {
    dirtyRef.current = false
  }, [])

  const canAcceptRemoteConfig = useCallback(() => {
    return !drawModeRef.current && !pendingRef.current && !dirtyRef.current
  }, [])

  const fitLabBounds = useCallback(() => {
    const map = mapRef.current
    const bounds = labBoundsRef.current
    if (!map || !bounds?.isValid()) return
    map.invalidateSize()
    map.fitBounds(bounds, { padding: [36, 36], maxZoom: 15 })
    labInitialFitDoneRef.current = true
  }, [])

  const updateBoatPosition = useCallback((position: LatLng, headingDeg: number) => {
    moveBoatMarker(boatRef.current, position, headingDeg)
  }, [])

  const clearPreviewRoute = useCallback(() => {
    previewLayerRef.current?.clearLayers()
  }, [])

  const setPreviewRoute = useCallback((waypoints: readonly Waypoint[]) => {
    const group = previewLayerRef.current
    if (!group) return
    group.clearLayers()
    const points: Array<[number, number]> = waypoints.map((waypoint) => {
      const point = gcj02ForDrawing(waypoint)
      return [point.lat, point.lng]
    })
    if (points.length > 1) {
      L.polyline(points, { color: '#dc2626', weight: 3, opacity: 0.78, dashArray: '6, 6' }).addTo(group)
    }
    waypoints.forEach((waypoint, index) => {
      const point = gcj02ForDrawing(waypoint)
      L.marker([point.lat, point.lng], {
        icon: L.divIcon({ className: 'usv-draft-waypoint-icon', html: `<span>P${index}</span>`, iconAnchor: [0, 0] }),
      }).addTo(group)
    })
  }, [])

  const setBoatToStart = useCallback((simConfig: LabConfig['sim']) => {
    const start = gcj02ForDrawing(simConfig.start)
    moveBoatMarker(
      boatRef.current,
      start,
      Number(simConfig.heading_deg) || 0,
    )
  }, [])

  useEffect(() => {
    let cancelled = false
    let resizeObserver: ResizeObserver | null = null
    async function initialiseMap() {
      const response = await fetch('/api/map/config')
      const cfg = readMapConfig(await response.json())
      if (cancelled || !cfg || !containerRef.current || mapRef.current) return
      const map = L.map(containerRef.current, {
        center: [cfg.default_center.lat, cfg.default_center.lng],
        zoom: cfg.default_zoom,
        attributionControl: false,
      })
      L.tileLayer(cfg.tile_url.replace('{style}', cfg.default_style), {
        minZoom: cfg.min_zoom,
        maxZoom: cfg.max_zoom,
      }).addTo(map)
      layerRef.current = L.layerGroup().addTo(map)
      previewLayerRef.current = L.layerGroup().addTo(map)
      boatRef.current = L.marker([cfg.default_center.lat, cfg.default_center.lng], {
        icon: boatIcon(configRef.current.sim.heading_deg),
      }).addTo(map)
      setBoatToStart(configRef.current.sim)

      // Fetch existing water area polygon first to sync it
      try {
        const waterAreaRes = await fetch('/api/lab/water-area')
        const waterAreaJson = await waterAreaRes.json()
        if (waterAreaJson.success && waterAreaJson.data) {
          setConfig((c) => ({
            ...c,
            water_area: {
              enabled: waterAreaJson.data.enabled ?? c.water_area.enabled,
              polygon: waterAreaJson.data.polygon ?? c.water_area.polygon,
            },
          }))
        }
      } catch (err) {
        console.error('Failed to load water-area config:', err)
      }

      const handleMapClickPosition = (position: LatLng) => {
        const mode = drawModeRef.current
        if (!isFiniteLatLng(position)) return
        if (mode === 'start') {
          const request = configWriteWithGcj02Start(configRef.current, position)
          dirtyRef.current = true
          void persistConfig(request).then((saved) => {
            if (!saved) return
            dirtyRef.current = false
            setConfig(saved)
            setBoatToStart(saved.sim)
          })
          return
        }
        if (mode === 'waypoint') {
          const request = missionWriteWithGcj02Waypoint(
            configRef.current.mission,
            position,
          )
          dirtyRef.current = true
          void persistMission(request).then((saved) => {
            if (!saved) return
            dirtyRef.current = false
            setConfig((labConfig) => ({ ...labConfig, mission: saved }))
          })
          return
        }
        if (mode === 'source') {
          const request = configWriteWithGcj02PollutionSource(
            configRef.current,
            position,
          )
          dirtyRef.current = true
          void persistConfig(request).then((saved) => {
            if (!saved) return
            dirtyRef.current = false
            setConfig(saved)
          })
          return
        }
        if (mode === 'water_area') {
          const request = waterAreaWriteWithGcj02Vertex(
            configRef.current.water_area,
            position,
          )
          dirtyRef.current = true
          void persistWaterArea(request).then((saved) => {
            if (!saved) return
            dirtyRef.current = false
            setConfig((labConfig) => ({ ...labConfig, water_area: saved }))
          })
        }
      }
      map.on('click', (event: L.LeafletMouseEvent) => {
        handleMapClickPosition(event.latlng)
      })
      mapRef.current = map
      setMapReady(true)
      const invalidateMapSize = () => {
        if (cancelled || mapRef.current !== map) return
        if (!map.getContainer().isConnected || !map.getPane('mapPane')) return
        map.invalidateSize()
      }
      if (typeof ResizeObserver !== 'undefined') {
        resizeObserver = new ResizeObserver(invalidateMapSize)
        resizeObserver.observe(containerRef.current)
      }
      window.setTimeout(invalidateMapSize, 0)
    }
    void initialiseMap().catch((error: unknown) => {
      if (error instanceof Error) {
        setMessage('地图初始化失败')
        return
      }
      throw error
    })
    return () => {
      cancelled = true
      resizeObserver?.disconnect()
      mapRef.current?.remove()
      mapRef.current = null
      layerRef.current = null
      previewLayerRef.current = null
      boatRef.current = null
      setMapReady(false)
    }
  }, [persistConfig, persistMission, persistWaterArea, setBoatToStart, setConfig, setMessage])

  useEffect(() => {
    const group = layerRef.current
    if (!mapReady || !group) return
    group.clearLayers()
    const pts: Array<[number, number]> = config.mission.waypoints.map((waypoint) => {
      const point = gcj02ForDrawing(waypoint)
      return [point.lat, point.lng]
    })
    const bounds = L.latLngBounds([])
    const start = gcj02ForDrawing(config.sim.start)
    const startLat = Number(start.lat)
    const startLng = Number(start.lng)
    if (Number.isFinite(startLat) && Number.isFinite(startLng)) {
      L.marker([startLat, startLng], { icon: startIcon() }).addTo(group)
      bounds.extend([startLat, startLng])
    }
    if (pts.length > 1) {
      L.polyline(pts, { color: '#2563eb', weight: 3, opacity: 0.7 }).addTo(group)
    }
    config.mission.waypoints.forEach((waypoint, index) => {
      const point = gcj02ForDrawing(waypoint)
      L.marker([point.lat, point.lng], {
        icon: L.divIcon({ className: 'usv-waypoint-icon', html: `<span>#${index}</span>`, iconAnchor: [0, 0] }),
      }).addTo(group)
      bounds.extend([point.lat, point.lng])
    })
    const source = config.pollution.mode === 'manual' ? config.pollution.source : config.mission.center
    if (source) {
      const point = gcj02ForDrawing(source)
      L.circle([point.lat, point.lng], {
        radius: config.pollution.radius_m,
        color: '#e03131',
        fillColor: '#e03131',
        fillOpacity: 0.12,
        weight: 1,
      }).addTo(group)
      bounds.extend([point.lat, point.lng])
    }
    if (config.water_area.polygon && config.water_area.polygon.length >= 2) {
      const wpts: Array<[number, number]> = config.water_area.polygon.map((coordinate) => {
        const point = gcj02ForDrawing(coordinate)
        return [point.lat, point.lng]
      })
      L.polygon(wpts, {
        color: '#2b8a3e',
        fillColor: '#2b8a3e',
        fillOpacity: 0.15,
        weight: 2,
        dashArray: '5, 5',
      }).addTo(group)
      config.water_area.polygon.forEach((coordinate) => {
        const point = gcj02ForDrawing(coordinate)
        bounds.extend([point.lat, point.lng])
      })
    }
    let validBounds = false
    if (bounds.isValid()) {
      labBoundsRef.current = bounds
      validBounds = true
      if (!labInitialFitDoneRef.current) {
        const map = mapRef.current
        map?.invalidateSize()
        map?.fitBounds(bounds, { padding: [36, 36], maxZoom: 15 })
        labInitialFitDoneRef.current = true
      }
    } else {
      labBoundsRef.current = null
    }
    const timer = setTimeout(() => {
      setHasLabBounds(validBounds)
    }, 0)
    return () => clearTimeout(timer)
  }, [config.mission, config.pollution, config.sim.start, config.water_area.polygon, mapReady])

  return {
    containerRef,
    drawMode,
    setDrawMode,
    hasLabBounds,
    fitLabBounds,
    setBoatToStart,
    updateBoatPosition,
    setPreviewRoute,
    clearPreviewRoute,
    markDirty,
    clearDirty,
    canAcceptRemoteConfig,
  }
}

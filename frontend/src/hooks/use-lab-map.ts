import { useCallback, useEffect, useRef, useState } from 'react'
import type { Dispatch, RefObject, SetStateAction } from 'react'
import L from 'leaflet'
import 'leaflet/dist/leaflet.css'
import { boatIcon, isFiniteLatLng, moveBoatMarker, startIcon } from '@/lib/lab-map'
import type { LabConfig, LabMission, LabPollution, LatLng, MapConfigLite } from '@/lib/lab-types'

type DrawMode = '' | 'start' | 'waypoint' | 'source' | 'water_area'

interface UseLabMapArgs {
  readonly config: LabConfig
  readonly pending: string
  readonly setConfig: Dispatch<SetStateAction<LabConfig>>
  readonly setMessage: (message: string) => void
  readonly persistConfig: (nextConfig: LabConfig) => Promise<boolean>
  readonly persistMission: (mission: LabMission) => Promise<boolean>
}

interface UseLabMapResult {
  readonly containerRef: RefObject<HTMLDivElement | null>
  readonly drawMode: DrawMode
  readonly setDrawMode: Dispatch<SetStateAction<DrawMode>>
  readonly hasLabBounds: boolean
  readonly fitLabBounds: () => void
  readonly setBoatToStart: (simConfig: LabConfig['sim']) => void
  readonly updateBoatPosition: (position: LatLng, headingDeg: number) => void
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
}: UseLabMapArgs): UseLabMapResult {
  const containerRef = useRef<HTMLDivElement | null>(null)
  const mapRef = useRef<L.Map | null>(null)
  const layerRef = useRef<L.LayerGroup | null>(null)
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

  const setBoatToStart = useCallback((simConfig: LabConfig['sim']) => {
    moveBoatMarker(
      boatRef.current,
      { lat: Number(simConfig.start_lat), lng: Number(simConfig.start_lng) },
      Number(simConfig.heading_deg) || 0,
    )
  }, [])

  const setStartPosition = useCallback((position: LatLng) => {
    if (!isFiniteLatLng(position)) return
    const nextConfig = {
      ...configRef.current,
      sim: {
        ...configRef.current.sim,
        start_lat: position.lat,
        start_lng: position.lng,
      },
    }
    dirtyRef.current = true
    setConfig(nextConfig)
    setBoatToStart(nextConfig.sim)
    void persistConfig(nextConfig).then((saved) => {
      if (saved) dirtyRef.current = false
    })
  }, [persistConfig, setBoatToStart, setConfig])

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

      map.on('click', (event: L.LeafletMouseEvent) => {
        const mode = drawModeRef.current
        if (mode === 'start') {
          setStartPosition({ lat: event.latlng.lat, lng: event.latlng.lng })
          return
        }
        if (mode === 'waypoint') {
          const current = configRef.current
          const waypoints = [
            ...current.mission.waypoints,
            { lat: event.latlng.lat, lng: event.latlng.lng, seq: current.mission.waypoints.length },
          ]
          const nextMission = { waypoints, center: null }
          dirtyRef.current = true
          setConfig((labConfig) => ({ ...labConfig, mission: nextMission }))
          void persistMission(nextMission).then((saved) => {
            if (saved) dirtyRef.current = false
          })
          return
        }
        if (mode === 'source') {
          const pollution: LabPollution = {
            ...configRef.current.pollution,
            mode: 'manual',
            source: { lat: event.latlng.lat, lng: event.latlng.lng },
          }
          const nextConfig = { ...configRef.current, pollution }
          dirtyRef.current = true
          setConfig(nextConfig)
          void persistConfig(nextConfig).then((saved) => {
            if (saved) dirtyRef.current = false
          })
          return
        }
        if (mode === 'water_area') {
          const current = configRef.current
          const polygon = [
            ...current.water_area.polygon,
            { lat: event.latlng.lat, lng: event.latlng.lng },
          ]
          const nextConfig = {
            ...current,
            water_area: {
              ...current.water_area,
              polygon,
            },
          }
          dirtyRef.current = true
          setConfig(nextConfig)
          void fetch('/api/lab/water-area', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
              enabled: current.water_area.enabled,
              polygon,
            }),
          }).then(async (res) => {
            const json = await res.json()
            if (json.success) {
              dirtyRef.current = false
            } else {
              setMessage(json.message || '水域范围保存失败')
            }
          }).catch(() => {
            setMessage('水域范围保存失败')
          })
        }
      })
      if (typeof ResizeObserver !== 'undefined') {
        resizeObserver = new ResizeObserver(() => map.invalidateSize())
        resizeObserver.observe(containerRef.current)
      }
      mapRef.current = map
      setMapReady(true)
      window.setTimeout(() => map.invalidateSize(), 0)
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
      boatRef.current = null
      setMapReady(false)
    }
  }, [persistConfig, persistMission, setBoatToStart, setConfig, setMessage, setStartPosition])

  useEffect(() => {
    const group = layerRef.current
    if (!mapReady || !group) return
    group.clearLayers()
    const pts: Array<[number, number]> = config.mission.waypoints.map((w) => [w.lat, w.lng])
    const bounds = L.latLngBounds([])
    const startLat = Number(config.sim.start_lat)
    const startLng = Number(config.sim.start_lng)
    if (Number.isFinite(startLat) && Number.isFinite(startLng)) {
      L.marker([startLat, startLng], { icon: startIcon() }).addTo(group)
      bounds.extend([startLat, startLng])
    }
    if (pts.length > 1) {
      L.polyline(pts, { color: '#2563eb', weight: 3, opacity: 0.7 }).addTo(group)
    }
    config.mission.waypoints.forEach((waypoint, index) => {
      L.marker([waypoint.lat, waypoint.lng], {
        icon: L.divIcon({ className: 'usv-waypoint-icon', html: `<span>#${index}</span>`, iconAnchor: [0, 0] }),
      }).addTo(group)
      bounds.extend([waypoint.lat, waypoint.lng])
    })
    const source = config.pollution.mode === 'manual' ? config.pollution.source : config.mission.center
    if (source) {
      L.circle([source.lat, source.lng], {
        radius: config.pollution.radius_m,
        color: '#e03131',
        fillColor: '#e03131',
        fillOpacity: 0.12,
        weight: 1,
      }).addTo(group)
      bounds.extend([source.lat, source.lng])
    }
    if (config.water_area.polygon && config.water_area.polygon.length >= 2) {
      const wpts: Array<[number, number]> = config.water_area.polygon.map((p) => [p.lat, p.lng])
      L.polygon(wpts, {
        color: '#2b8a3e',
        fillColor: '#2b8a3e',
        fillOpacity: 0.15,
        weight: 2,
        dashArray: '5, 5',
      }).addTo(group)
      config.water_area.polygon.forEach((p) => {
        bounds.extend([p.lat, p.lng])
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
  }, [config.mission, config.pollution, config.sim.start_lat, config.sim.start_lng, config.water_area.polygon, mapReady])

  return {
    containerRef,
    drawMode,
    setDrawMode,
    hasLabBounds,
    fitLabBounds,
    setBoatToStart,
    updateBoatPosition,
    markDirty,
    clearDirty,
    canAcceptRemoteConfig,
  }
}

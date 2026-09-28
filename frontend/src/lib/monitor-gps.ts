export interface DisplayPosition {
  available: boolean
  latitude: number | null
  longitude: number | null
  altitude: number | null
  age_s: number | null
  stale?: boolean
}

/** Presentation only: never replaces the server's strict sampling admission. */
export function summarizeGps(gps: {
  gps_raw: DisplayPosition & { valid: boolean }
  global_position: DisplayPosition
  navsat: DisplayPosition & { valid_navsat_fix: boolean }
  freshness_threshold_s?: number
} | null) {
  const freshAfter = gps?.freshness_threshold_s ?? 2
  const usable = (p: DisplayPosition | undefined) => Boolean(p?.available
    && typeof p.latitude === 'number' && Number.isFinite(p.latitude) && Math.abs(p.latitude) <= 90
    && typeof p.longitude === 'number' && Number.isFinite(p.longitude) && Math.abs(p.longitude) <= 180
    && !(p.latitude === 0 && p.longitude === 0)
    && typeof p.age_s === 'number' && Number.isFinite(p.age_s) && p.age_s >= 0
    && p.age_s <= Math.max(10, freshAfter))
  const candidates = [
    { position: gps?.navsat, source: 'MAVROS NavSatFix', admitted: gps?.navsat.valid_navsat_fix },
    { position: gps?.global_position, source: 'GLOBAL_POSITION_INT', admitted: true },
    { position: gps?.gps_raw, source: 'GPS_RAW_INT', admitted: gps?.gps_raw.valid },
  ]
  const available = candidates.filter(p => p.admitted && usable(p.position))
  const selected = available.find(p => !p.position?.stale && (p.position?.age_s ?? Infinity) <= freshAfter) ?? available[0]
  const normal = selected && gps?.gps_raw.valid && (selected.position?.age_s ?? Infinity) <= freshAfter
    && !selected.position?.stale
  return {
    label: !gps ? '定位未知' : !selected ? '定位不可用' : normal ? '定位正常' : '定位降级',
    tone: !gps ? 'neutral' : !selected ? 'error' : normal ? 'success' : 'warning',
    position: selected?.position,
    source: selected?.source ?? '无有效坐标',
  }
}

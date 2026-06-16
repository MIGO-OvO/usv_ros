import L from 'leaflet'
import type { LatLng } from './lab-types'

export function isFiniteLatLng(position: LatLng): boolean {
  return Number.isFinite(position.lat) && Number.isFinite(position.lng)
}

export function startIcon(): L.DivIcon {
  return L.divIcon({
    className: 'usv-start-icon',
    html: '<span><b>起</b></span>',
    iconAnchor: [13, 28],
  })
}

export function boatIcon(headingDeg = 0): L.DivIcon {
  return L.divIcon({
    className: 'usv-boat-icon',
    html: `<span class="usv-boat-glyph" style="--boat-heading:${headingDeg}deg">▲</span>`,
    iconAnchor: [14, 14],
  })
}

export function moveBoatMarker(marker: L.Marker | null, position: LatLng, headingDeg: number): void {
  if (!marker || !isFiniteLatLng(position)) return
  marker.setLatLng([position.lat, position.lng])
  const element = marker.getElement()
  element?.style.setProperty('--boat-heading', `${headingDeg}deg`)
}

export function speedPercent(speedMps: number, maxSpeedMps: number): number {
  if (!Number.isFinite(speedMps) || !Number.isFinite(maxSpeedMps) || maxSpeedMps <= 0) return 0
  return Math.max(0, Math.min(100, (Math.abs(speedMps) / maxSpeedMps) * 100))
}

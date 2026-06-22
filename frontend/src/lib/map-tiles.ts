import L from 'leaflet'

export const MAP_TILE_NATIVE_MAX_ZOOM = 18
export const MAP_TILE_DISPLAY_MAX_ZOOM = 22

export type AmapTileLayerConfig = {
  readonly tile_url: string
  readonly default_style: string
  readonly min_zoom: number
  readonly max_zoom: number
}

export function createOverscaledAmapTileLayer(config: AmapTileLayerConfig): L.TileLayer {
  const nativeMaxZoom = Math.min(config.max_zoom, MAP_TILE_NATIVE_MAX_ZOOM)
  return L.tileLayer(config.tile_url.replace('{style}', config.default_style), {
    minZoom: config.min_zoom,
    maxNativeZoom: nativeMaxZoom,
    maxZoom: Math.max(nativeMaxZoom, MAP_TILE_DISPLAY_MAX_ZOOM),
  })
}

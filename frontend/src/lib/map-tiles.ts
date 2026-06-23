import L from 'leaflet'

// 谷歌国际版卫星 (gsatellite, mt.google.com) 现场实测原生可到 z=22;
// 高德仅到 z=18。取两源较高者作为原生上限, 实际由后端 config.max_zoom 收口。
export const MAP_TILE_NATIVE_MAX_ZOOM = 22
export const MAP_TILE_DISPLAY_MAX_ZOOM = 24

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

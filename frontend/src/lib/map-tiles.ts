import L from 'leaflet'

// 谷歌卫星 (gsatellite, google.cn) 原生瓦片可到 z=20; 高德仅到 z=18。
// 取两源较高者作为原生上限, 实际由后端 config.max_zoom 收口 (当前 20)。
export const MAP_TILE_NATIVE_MAX_ZOOM = 20
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

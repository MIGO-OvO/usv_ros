"""Headless scientific figure export for lab simulation surfaces."""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from scripts.lib.lab_sim.surface import LAYER_NAMES, SurfaceGrid

try:
    import matplotlib

    matplotlib.use("Agg")
    from matplotlib import pyplot as plt

    _MATPLOTLIB_AVAILABLE = True
except ImportError:
    _MATPLOTLIB_AVAILABLE = False
    plt = None


@dataclass(frozen=True)
class FigureExportError(RuntimeError):
    field: str
    reason: str

    def __str__(self) -> str:
        return f"invalid {self.field}: {self.reason}"


@dataclass(frozen=True)
class SurfaceFigureExport:
    png_path: Path
    tiff_path: Path
    svg_path: Path
    pdf_path: Path
    metadata_path: Path


def matplotlib_available() -> bool:
    return _MATPLOTLIB_AVAILABLE


def export_surface_figure(
    grid: SurfaceGrid,
    *,
    output_prefix: Path,
    size_inches: tuple[float, float],
    dpi: int,
    seed: int,
) -> SurfaceFigureExport:
    if not _MATPLOTLIB_AVAILABLE or plt is None:
        raise FigureExportError("matplotlib", "Agg renderer is unavailable")
    width_in, height_in = _bounded_size(size_inches)
    resolved_dpi = _bounded_dpi(dpi)
    paths = _export_paths(output_prefix)
    paths.png_path.parent.mkdir(parents=True, exist_ok=True)
    fig, axes = plt.subplots(2, 3, figsize=(width_in, height_in), dpi=resolved_dpi)
    try:
        _draw_layers(grid, axes)
        fig.subplots_adjust(left=0.02, right=0.98, bottom=0.02, top=0.86, wspace=0.06, hspace=0.32)
        fig.savefig(paths.png_path, format="png", dpi=resolved_dpi)
        fig.savefig(paths.tiff_path, format="tiff", dpi=resolved_dpi)
        fig.savefig(paths.svg_path, format="svg", dpi=resolved_dpi)
        fig.savefig(paths.pdf_path, format="pdf", dpi=resolved_dpi)
    finally:
        plt.close(fig)
    _write_metadata(
        grid,
        paths.metadata_path,
        size_inches=(width_in, height_in),
        dpi=resolved_dpi,
        seed=seed,
    )
    return paths


def _bounded_size(value: tuple[float, float]) -> tuple[float, float]:
    width_in, height_in = value
    values = (float(width_in), float(height_in))
    if not all(math.isfinite(item) and 0.1 <= item <= 20.0 for item in values):
        raise FigureExportError("size_inches", "width and height must be finite in [0.1, 20]")
    return values


def _bounded_dpi(value: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise FigureExportError("dpi", "must be an integer")
    if not 72 <= value <= 1200:
        raise FigureExportError("dpi", "must be between 72 and 1200")
    return value


def _export_paths(output_prefix: Path) -> SurfaceFigureExport:
    return SurfaceFigureExport(
        png_path=output_prefix.with_suffix(".png"),
        tiff_path=output_prefix.with_suffix(".tiff"),
        svg_path=output_prefix.with_suffix(".svg"),
        pdf_path=output_prefix.with_suffix(".pdf"),
        metadata_path=output_prefix.with_suffix(".metadata.json"),
    )


def _draw_layers(grid: SurfaceGrid, axes) -> None:
    extent = (
        float(grid.x_east_m[0]),
        float(grid.x_east_m[-1]),
        float(grid.y_north_m[0]),
        float(grid.y_north_m[-1]),
    )
    for axis, name in zip(axes.flat, LAYER_NAMES):
        data = np.ma.masked_invalid(grid.layers[name])
        axis.imshow(data, origin="lower", extent=extent, interpolation="nearest")
        axis.set_title(name)
        axis.set_xticks(())
        axis.set_yticks(())


def _write_metadata(
    grid: SurfaceGrid,
    metadata_path: Path,
    *,
    size_inches: tuple[float, float],
    dpi: int,
    seed: int,
) -> None:
    pixel_width = int(round(size_inches[0] * dpi))
    pixel_height = int(round(size_inches[1] * dpi))
    metadata = {
        "grid_size": [grid.grid_size, grid.grid_size],
        "dpi": dpi,
        "pixel_width": pixel_width,
        "pixel_height": pixel_height,
        "size_inches": [size_inches[0], size_inches[1]],
        "bbox": {
            "southwest": {
                "lat": grid.bbox_wgs84.southwest.lat,
                "lng": grid.bbox_wgs84.southwest.lng,
            },
            "northeast": {
                "lat": grid.bbox_wgs84.northeast.lat,
                "lng": grid.bbox_wgs84.northeast.lng,
            },
        },
        "crs": {
            "truth": "WGS-84",
            "grid": "local ENU",
            "enu_origin": {
                "lat": grid.enu_origin.lat,
                "lng": grid.enu_origin.lng,
                "alt": grid.enu_origin.alt,
            },
        },
        "layer_list": list(LAYER_NAMES),
        "seed": seed,
        "snapshot_hash": grid.snapshot_hash,
    }
    metadata_path.write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )

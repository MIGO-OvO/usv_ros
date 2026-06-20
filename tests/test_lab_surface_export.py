import hashlib
import json
import math
import struct
import unittest
from pathlib import Path

import numpy as np

from scripts.lib.lab_sim.calibration import BeerLambertConfig, WorkCurveConfig
from scripts.lib.lab_sim.model_config import WaterSnapshot
from scripts.lib.lab_sim.model_events import SamplingEvent
from scripts.lib.lab_sim.model_primitives import CoordinatePairRef, GeoPoint
from scripts.lib.lab_sim.pollution_field import (
    BackgroundField,
    ConcentrationBounds,
    PollutionField,
    PollutionSource,
    Wgs84Point,
)


def _surface_module():
    from scripts.lib.lab_sim import surface

    return surface


def _figure_export_module():
    from scripts.lib.lab_sim import figure_export

    return figure_export


def _pair(lat: float, lng: float) -> CoordinatePairRef:
    return CoordinatePairRef(
        wgs84=GeoPoint(lat=lat, lng=lng),
        gcj02=GeoPoint(lat=lat + 0.001, lng=lng + 0.001),
    )


def _water_snapshot() -> WaterSnapshot:
    return WaterSnapshot(
        snapshot_id="water-triangle-v1",
        polygon=(
            _pair(25.274000, 110.296000),
            _pair(25.274000, 110.296900),
            _pair(25.274720, 110.296000),
        ),
    )


def _pollution_field() -> PollutionField:
    return PollutionField(
        origin=Wgs84Point(25.274000, 110.296000),
        background=BackgroundField(mean=0.08),
        sources=(
            PollutionSource(
                location=Wgs84Point(25.274250, 110.296250),
                peak=2.0,
                major_scale_m=85.0,
                minor_scale_m=40.0,
                orientation_deg=25.0,
            ),
        ),
        reference_points=(),
        bounds=ConcentrationBounds(lower=0.0, upper=4.0),
        seed=17,
    )


def _event(
    event_id: str,
    lat: float,
    lng: float,
    concentration: float,
    index: int,
) -> SamplingEvent:
    return SamplingEvent(
        schema_version=2,
        event_id=event_id,
        mode="waypoint",
        route_id="route-surface",
        waypoint_index=index,
        segment_index=None,
        position=_pair(lat, lng),
        analyte_id="nh3n",
        droplets=(),
        mean=concentration,
        median=concentration,
        standard_deviation=0.0,
        valid_count=12,
        quality_flags=(),
        config_droplet_count=12,
    )


def _sampling_events() -> tuple[SamplingEvent, ...]:
    field = _pollution_field()
    positions = (
        ("sample-a", 25.274080, 110.296080),
        ("sample-b", 25.274120, 110.296620),
        ("sample-c", 25.274570, 110.296110),
        ("sample-d", 25.274310, 110.296260),
    )
    return tuple(
        _event(
            event_id,
            lat,
            lng,
            field.concentration_at(Wgs84Point(lat, lng)),
            index,
        )
        for index, (event_id, lat, lng) in enumerate(positions)
    )


def _surface_grid():
    surface = _surface_module()
    return surface.build_surface_grid(
        water=_water_snapshot(),
        pollution_field=_pollution_field(),
        sampling_events=_sampling_events(),
        grid_size=31,
        idw_power=2.0,
        seed=12345,
        work_curve=WorkCurveConfig(k=0.1, b=0.0),
        beer_lambert=BeerLambertConfig(
            dark_voltage=0.0,
            reference_voltage=3.0,
            saturation_voltage=None,
        ),
    )


def _array_hash(grid) -> str:
    names = tuple(sorted(grid.layers))
    digest = hashlib.blake2b(digest_size=16)
    for name in names:
        digest.update(name.encode("utf-8"))
        values = np.nan_to_num(
            grid.layers[name],
            nan=-9999.0,
            posinf=9999.0,
            neginf=-9999.0,
        )
        digest.update(np.asarray(values, dtype=np.float64).tobytes())
    return digest.hexdigest()


def _png_dimensions(path: Path) -> tuple[int, int]:
    with path.open("rb") as handle:
        signature = handle.read(24)
    if signature[:8] != b"\x89PNG\r\n\x1a\n":
        raise AssertionError("not a PNG file")
    return struct.unpack(">II", signature[16:24])


class LabSurfaceExportTests(unittest.TestCase):
    def test_masks_every_out_of_polygon_cell_for_all_layers(self) -> None:
        # Given / When
        grid = _surface_grid()

        # Then
        self.assertEqual(
            set(grid.layers),
            {"truth", "reconstruction", "error", "voltage", "absorbance", "risk"},
        )
        self.assertEqual(grid.bbox_source, "water_snapshot")
        self.assertGreater(np.count_nonzero(grid.outside_water_mask), 0)
        for name, layer in grid.layers.items():
            with self.subTest(layer=name):
                self.assertEqual(layer.shape, (31, 31))
                self.assertTrue(np.isnan(layer[grid.outside_water_mask]).all())
                self.assertTrue(np.isfinite(layer[~grid.outside_water_mask]).all())

    def test_grid_uses_water_snapshot_bbox_not_sample_bbox(self) -> None:
        # Given / When
        grid = _surface_grid()

        # Then
        water_lats = [point.wgs84.lat for point in _water_snapshot().polygon]
        water_lngs = [point.wgs84.lng for point in _water_snapshot().polygon]
        self.assertAlmostEqual(grid.bbox_wgs84.southwest.lat, min(water_lats), places=9)
        self.assertAlmostEqual(grid.bbox_wgs84.southwest.lng, min(water_lngs), places=9)
        self.assertAlmostEqual(grid.bbox_wgs84.northeast.lat, max(water_lats), places=9)
        self.assertAlmostEqual(grid.bbox_wgs84.northeast.lng, max(water_lngs), places=9)

    def test_fixed_seed_surface_array_hash_is_stable(self) -> None:
        # Given / When
        first = _surface_grid()
        second = _surface_grid()

        # Then
        self.assertEqual(_array_hash(first), _array_hash(second))

    def test_exports_300_dpi_raster_vector_and_metadata_files(self) -> None:
        # Given
        figure_export = _figure_export_module()
        if not figure_export.matplotlib_available():
            self.skipTest("matplotlib unavailable; numeric surface tests still run")
        evidence_dir = Path(__file__).resolve().parents[3] / ".omo" / "evidence"
        evidence_dir.mkdir(parents=True, exist_ok=True)

        # When
        result = figure_export.export_surface_figure(
            _surface_grid(),
            output_prefix=evidence_dir / "task-T17-figure",
            size_inches=(2.0, 1.5),
            dpi=300,
            seed=12345,
        )

        # Then
        self.assertEqual(_png_dimensions(result.png_path), (600, 450))
        for path in (result.png_path, result.tiff_path, result.svg_path, result.pdf_path):
            self.assertTrue(path.exists(), path)
            self.assertGreater(path.stat().st_size, 0, path)
        with result.metadata_path.open("r", encoding="utf-8") as handle:
            metadata = json.load(handle)
        self.assertEqual(metadata["dpi"], 300)
        self.assertEqual(metadata["pixel_width"], 600)
        self.assertEqual(metadata["pixel_height"], 450)
        self.assertEqual(metadata["crs"]["truth"], "WGS-84")
        self.assertEqual(metadata["crs"]["grid"], "local ENU")
        self.assertEqual(metadata["layer_list"], ["truth", "reconstruction", "error", "voltage", "absorbance", "risk"])
        self.assertEqual(metadata["seed"], 12345)
        self.assertEqual(metadata["snapshot_hash"], _surface_grid().snapshot_hash)

        try:
            from PIL import Image
        except ModuleNotFoundError:
            return
        with Image.open(result.tiff_path) as image:
            self.assertEqual(image.size, (600, 450))
            dpi_x, dpi_y = image.info.get("dpi", (0.0, 0.0))
        self.assertTrue(math.isclose(dpi_x, 300.0, abs_tol=0.6))
        self.assertTrue(math.isclose(dpi_y, 300.0, abs_tol=0.6))


if __name__ == "__main__":
    unittest.main()

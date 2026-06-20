import importlib.util
import math
import sys
import unittest
from pathlib import Path
from types import ModuleType


MODULE_PATH = (
    Path(__file__).resolve().parents[1]
    / "scripts"
    / "lib"
    / "lab_sim"
    / "pollution_field.py"
)
EARTH_RADIUS_M = 6_378_137.0


def _load_module() -> ModuleType:
    if not MODULE_PATH.is_file():
        raise AssertionError("pollution_field.py has not been implemented")
    spec = importlib.util.spec_from_file_location("lab_sim_pollution_field", MODULE_PATH)
    if spec is None or spec.loader is None:
        raise AssertionError("pollution_field.py cannot be loaded")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _offset(point, north_m: float = 0.0, east_m: float = 0.0):
    latitude = point.latitude_deg + math.degrees(north_m / EARTH_RADIUS_M)
    longitude = point.longitude_deg + math.degrees(
        east_m / (EARTH_RADIUS_M * math.cos(math.radians(point.latitude_deg)))
    )
    return type(point)(latitude, longitude)


class PollutionFieldTests(unittest.TestCase):
    def setUp(self) -> None:
        self.module = _load_module()
        self.origin = self.module.Wgs84Point(25.314167, 110.412778)
        self.bounds = self.module.ConcentrationBounds(0.0, 100.0)

    def _field(self, *, sources=(), background=None, references=(), seed=7):
        if background is None:
            background = self.module.BackgroundField(mean=2.0)
        return self.module.PollutionField(
            origin=self.origin,
            background=background,
            sources=tuple(sources),
            reference_points=tuple(references),
            bounds=self.bounds,
            seed=seed,
        )

    def test_source_peak_when_sampling_at_source(self) -> None:
        # Given
        source = self.module.PollutionSource(
            location=self.origin,
            peak=12.0,
            major_scale_m=40.0,
            minor_scale_m=40.0,
        )
        field = self._field(sources=(source,))

        # When
        concentration = field.concentration_at(self.origin)

        # Then
        self.assertAlmostEqual(concentration, 14.0, places=12)

    def test_distance_attenuation_uses_meter_offsets(self) -> None:
        # Given
        source = self.module.PollutionSource(
            location=self.origin,
            peak=10.0,
            major_scale_m=100.0,
            minor_scale_m=100.0,
        )
        field = self._field(
            sources=(source,),
            background=self.module.BackgroundField(mean=0.0),
        )
        point_100_m_north = _offset(self.origin, north_m=100.0)

        # When
        concentration = field.concentration_at(point_100_m_north)

        # Then
        self.assertAlmostEqual(concentration, 10.0 * math.exp(-0.5), places=5)

    def test_anisotropy_orientation_follows_major_axis(self) -> None:
        # Given
        source = self.module.PollutionSource(
            location=self.origin,
            peak=20.0,
            major_scale_m=120.0,
            minor_scale_m=20.0,
            orientation_deg=0.0,
        )
        field = self._field(
            sources=(source,),
            background=self.module.BackgroundField(mean=0.0),
        )

        # When
        north_value = field.concentration_at(_offset(self.origin, north_m=80.0))
        east_value = field.concentration_at(_offset(self.origin, east_m=80.0))

        # Then
        self.assertGreater(north_value, east_value * 100.0)

    def test_multiple_sources_superpose(self) -> None:
        # Given
        sources = (
            self.module.PollutionSource(self.origin, 4.0, 50.0, 50.0),
            self.module.PollutionSource(self.origin, 7.0, 50.0, 50.0),
        )
        field = self._field(
            sources=sources,
            background=self.module.BackgroundField(mean=1.0),
        )

        # When
        concentration = field.concentration_at(self.origin)

        # Then
        self.assertAlmostEqual(concentration, 12.0, places=12)

    def test_optional_decay_reduces_source_contribution(self) -> None:
        # Given
        point = _offset(self.origin, north_m=60.0)
        common = {
            "location": self.origin,
            "peak": 10.0,
            "major_scale_m": 100.0,
            "minor_scale_m": 100.0,
        }
        without_decay = self._field(
            sources=(self.module.PollutionSource(**common),),
            background=self.module.BackgroundField(mean=0.0),
        )
        with_decay = self._field(
            sources=(
                self.module.PollutionSource(**common, decay_length_m=30.0),
            ),
            background=self.module.BackgroundField(mean=0.0),
        )

        # When
        undecayed = without_decay.concentration_at(point)
        decayed = with_decay.concentration_at(point)

        # Then
        self.assertLess(decayed, undecayed)

    def test_lower_and_upper_bounds_clamp_results(self) -> None:
        # Given
        lower_field = self._field(
            background=self.module.BackgroundField(mean=-5.0),
        )
        upper_source = self.module.PollutionSource(
            self.origin, 200.0, 10.0, 10.0
        )
        upper_field = self._field(sources=(upper_source,))

        # When
        lower_value = lower_field.concentration_at(self.origin)
        upper_value = upper_field.concentration_at(self.origin)

        # Then
        self.assertEqual(lower_value, 0.0)
        self.assertEqual(upper_value, 100.0)

    def test_reference_point_fits_measured_concentration(self) -> None:
        # Given
        location = _offset(self.origin, north_m=35.0, east_m=20.0)
        reference = self.module.FieldReferencePoint(
            location=location,
            concentration=27.5,
            influence_scale_m=50.0,
        )
        field = self._field(references=(reference,))

        # When
        concentration = field.concentration_at(location)

        # Then
        self.assertAlmostEqual(concentration, 27.5, places=12)

    def test_fixed_seed_reproduces_coordinate_noise(self) -> None:
        # Given
        background = self.module.BackgroundField(mean=5.0, noise_std=0.4)
        first = self._field(background=background, seed=20260618)
        second = self._field(background=background, seed=20260618)
        point = _offset(self.origin, north_m=11.0, east_m=-9.0)

        # When
        first_values = [first.concentration_at(point) for _ in range(3)]
        second_value = second.concentration_at(point)

        # Then
        self.assertEqual(first_values, [second_value, second_value, second_value])

    def test_invalid_sources_are_rejected(self) -> None:
        # Given / When / Then
        invalid_sources = (
            {"location": self.origin, "peak": math.nan, "major_scale_m": 1.0, "minor_scale_m": 1.0},
            {"location": self.origin, "peak": -1.0, "major_scale_m": 1.0, "minor_scale_m": 1.0},
            {"location": self.origin, "peak": 1.0, "major_scale_m": 0.0, "minor_scale_m": 1.0},
            {"location": self.origin, "peak": 1.0, "major_scale_m": 1.0, "minor_scale_m": -2.0},
            {"location": self.origin, "peak": 1.0, "major_scale_m": 1.0, "minor_scale_m": 1.0, "decay_length_m": 0.0},
        )
        for kwargs in invalid_sources:
            with self.subTest(kwargs=kwargs):
                with self.assertRaises(self.module.PollutionFieldConfigError):
                    self.module.PollutionSource(**kwargs)

    def test_invalid_coordinates_references_and_bounds_are_rejected(self) -> None:
        # Given / When / Then
        with self.assertRaises(self.module.PollutionFieldConfigError):
            self.module.Wgs84Point(91.0, 110.0)
        with self.assertRaises(self.module.PollutionFieldConfigError):
            self.module.Wgs84Point(25.0, math.inf)
        with self.assertRaises(self.module.PollutionFieldConfigError):
            self.module.FieldReferencePoint(self.origin, math.nan, 10.0)
        with self.assertRaises(self.module.PollutionFieldConfigError):
            self.module.FieldReferencePoint(self.origin, 1.0, 0.0)
        with self.assertRaises(self.module.PollutionFieldConfigError):
            self.module.ConcentrationBounds(3.0, 3.0)


if __name__ == "__main__":
    unittest.main()

import unittest

from scripts.lib.lab_sim.coordinates import Coordinate
from scripts.lib.lab_sim.model_config import (
    Analyte,
    LabConfigV2,
    PollutionSource,
    RouteSnapshot,
    WaterSnapshot,
)
from scripts.lib.lab_sim.model_primitives import CoordinatePairRef, GeoPoint
from scripts.lib.lab_sim.sampling_service import (
    SurveySamplingContext,
    WaypointSamplingContext,
    generate_sampling_event,
)


def _pair(lat: float, lng: float) -> CoordinatePairRef:
    return CoordinatePairRef(
        wgs84=GeoPoint(lat=lat, lng=lng),
        gcj02=GeoPoint(lat=lat + 0.001, lng=lng + 0.001),
    )


def _config(*, droplet_count: int = 12) -> LabConfigV2:
    origin = _pair(25.314167, 110.412778)
    return LabConfigV2(
        schema_version=2,
        coordinate_schema_version=2,
        droplet_count=droplet_count,
        analytes=(
            Analyte(analyte_id="nh3n", name="氨氮", unit="mg/L"),
            Analyte(analyte_id="tp", name="总磷", unit="mg/L"),
        ),
        sources=(
            PollutionSource(
                source_id="outfall-a",
                position=origin,
                concentrations=(("nh3n", 3.0), ("tp", 0.6)),
            ),
        ),
        route=RouteSnapshot(route_id="route-7", kind="manual_route", waypoints=(origin,)),
        water=WaterSnapshot(
            snapshot_id="water-3",
            polygon=(
                _pair(25.313, 110.411),
                _pair(25.316, 110.411),
                _pair(25.316, 110.415),
            ),
        ),
    )


class SamplingServiceTests(unittest.TestCase):
    def test_generates_waypoint_event_when_context_is_waypoint(self) -> None:
        # Given
        config = _config()
        context = WaypointSamplingContext(waypoint_index=0)

        # When
        event = generate_sampling_event(
            Coordinate(25.314167, 110.412778),
            config,
            context=context,
            seed=42,
        )

        # Then
        self.assertEqual(event.mode, "waypoint")
        self.assertEqual(event.route_id, "route-7")
        self.assertEqual(event.waypoint_index, 0)
        self.assertIsNone(event.segment_index)
        self.assertEqual(event.analyte_id, "nh3n")
        self.assertTrue(event.valid)

    def test_generates_survey_event_when_context_is_survey(self) -> None:
        # Given
        config = _config()
        context = SurveySamplingContext(segment_index=2)

        # When
        event = generate_sampling_event(
            Coordinate(25.3142, 110.4128),
            config,
            context=context,
            seed=42,
        )

        # Then
        self.assertEqual(event.mode, "survey")
        self.assertIsNone(event.waypoint_index)
        self.assertEqual(event.segment_index, 2)
        self.assertTrue(event.valid)

    def test_fixed_seed_reproduces_event(self) -> None:
        # Given
        config = _config()
        context = WaypointSamplingContext(waypoint_index=0)
        position = Coordinate(25.314167, 110.412778)

        # When
        first = generate_sampling_event(position, config, context=context, seed=20260618)
        second = generate_sampling_event(position, config, context=context, seed=20260618)

        # Then
        self.assertEqual(first, second)

    def test_selects_analyte_specific_concentration_when_config_is_multi_analyte(self) -> None:
        # Given
        config = _config()
        context = WaypointSamplingContext(waypoint_index=0)
        position = Coordinate(25.314167, 110.412778)

        # When
        nh3n = generate_sampling_event(position, config, context=context, seed=7, analyte_id="nh3n")
        tp = generate_sampling_event(position, config, context=context, seed=7, analyte_id="tp")

        # Then
        self.assertEqual(nh3n.analyte_id, "nh3n")
        self.assertEqual(tp.analyte_id, "tp")
        self.assertGreater(nh3n.mean, tp.mean)

    def test_insufficient_valid_droplets_returns_invalid_event(self) -> None:
        # Given
        config = _config(droplet_count=3)
        context = WaypointSamplingContext(waypoint_index=0)

        # When
        event = generate_sampling_event(
            Coordinate(25.314167, 110.412778),
            config,
            context=context,
            seed=9,
            minimum_valid=4,
        )

        # Then
        self.assertFalse(event.valid)
        self.assertEqual(event.valid_count, 3)
        self.assertIn("insufficient_valid_droplets", event.quality_flags)

    def test_sixty_four_droplets_still_return_one_event_and_one_aggregate(self) -> None:
        # Given
        config = _config(droplet_count=64)
        context = SurveySamplingContext(segment_index=3)

        # When
        event = generate_sampling_event(
            Coordinate(25.314167, 110.412778),
            config,
            context=context,
            seed=64,
        )

        # Then
        self.assertEqual(len(event.droplets), 64)
        self.assertEqual(event.config_droplet_count, 64)
        self.assertFalse(hasattr(event, "map_points"))
        self.assertIsInstance(event.mean, float)


if __name__ == "__main__":
    unittest.main()

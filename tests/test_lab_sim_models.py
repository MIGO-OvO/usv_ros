import json
import math
import unittest
from dataclasses import FrozenInstanceError


class TestLabConfigV2(unittest.TestCase):
    def setUp(self):
        from scripts.lib.lab_sim.models import LabConfigV2

        self.model_type = LabConfigV2
        self.payload = {
            "schema_version": 2,
            "coordinate_schema_version": 2,
            "analytes": [
                {"analyte_id": "nh3n", "name": "氨氮", "unit": "mg/L"},
                {"analyte_id": "tp", "name": "总磷", "unit": "mg/L"},
            ],
            "sources": [
                {
                    "source_id": "outfall-a",
                    "position": {
                        "wgs84": {"lat": 25.314167, "lng": 110.412778},
                        "gcj02": {"lat": 25.3112, "lng": 110.4173},
                    },
                    "concentrations": {"nh3n": 1.8, "tp": 0.42},
                }
            ],
            "route": {
                "route_id": "route-7",
                "kind": "manual_route",
                "waypoints": [
                    {
                        "wgs84": {"lat": 25.314167, "lng": 110.412778},
                        "gcj02": {"lat": 25.3112, "lng": 110.4173},
                    }
                ],
            },
            "water": {
                "snapshot_id": "water-3",
                "polygon": [
                    {
                        "wgs84": {"lat": 25.31, "lng": 110.41},
                        "gcj02": {"lat": 25.307, "lng": 110.4145},
                    },
                    {
                        "wgs84": {"lat": 25.32, "lng": 110.41},
                        "gcj02": {"lat": 25.317, "lng": 110.4145},
                    },
                    {
                        "wgs84": {"lat": 25.32, "lng": 110.42},
                        "gcj02": {"lat": 25.317, "lng": 110.4245},
                    },
                ],
            },
        }

    def test_defaults_droplet_count_to_twelve(self):
        # Given a valid v2 payload without an explicit droplet count
        # When the payload is parsed
        model = self.model_type.from_dict(self.payload)
        # Then the bounded default is twelve
        self.assertEqual(model.droplet_count, 12)

    def test_accepts_minimum_and_maximum_droplet_count(self):
        # Given valid payloads at both supported boundaries
        results = []
        # When each payload is parsed
        for count in (3, 64):
            payload = dict(self.payload, droplet_count=count)
            results.append(self.model_type.from_dict(payload).droplet_count)
        # Then both boundaries are retained
        self.assertEqual(results, [3, 64])

    def test_rejects_droplet_count_outside_bounds(self):
        from scripts.lib.lab_sim.models import ModelParseError

        # Given valid payloads with unsupported counts
        errors = []
        # When each payload is parsed
        for count in (2, 65):
            with self.assertRaises(ModelParseError) as caught:
                self.model_type.from_dict(dict(self.payload, droplet_count=count))
            errors.append(caught.exception.code)
        # Then both are rejected with a typed code
        self.assertEqual(errors, ["droplet_count_range", "droplet_count_range"])

    def test_rejects_ambiguous_bare_coordinates(self):
        from scripts.lib.lab_sim.models import ModelParseError

        # Given a route waypoint with an unlabelled latitude/longitude pair
        payload = dict(self.payload)
        payload["route"] = {
            "route_id": "route-7",
            "kind": "manual_route",
            "waypoints": [{"lat": 25.3, "lng": 110.4}],
        }
        # When the config is parsed
        with self.assertRaises(ModelParseError) as caught:
            self.model_type.from_dict(payload)
        # Then the ambiguous coordinate is rejected
        self.assertEqual(caught.exception.code, "ambiguous_coordinate")

    def test_rejects_missing_crs(self):
        from scripts.lib.lab_sim.models import ModelParseError

        # Given a coordinate pair missing its display CRS
        payload = dict(self.payload)
        payload["route"] = {
            "route_id": "route-7",
            "kind": "manual_route",
            "waypoints": [{"wgs84": {"lat": 25.3, "lng": 110.4}}],
        }
        # When the config is parsed
        with self.assertRaises(ModelParseError) as caught:
            self.model_type.from_dict(payload)
        # Then the missing CRS is rejected
        self.assertEqual(caught.exception.code, "missing_crs")

    def test_rejects_non_finite_coordinate(self):
        from scripts.lib.lab_sim.models import ModelParseError

        # Given a coordinate containing NaN
        payload = dict(self.payload)
        payload["route"] = {
            "route_id": "route-7",
            "kind": "manual_route",
            "waypoints": [{
                "wgs84": {"lat": math.nan, "lng": 110.4},
                "gcj02": {"lat": 25.3, "lng": 110.4},
            }],
        }
        # When the config is parsed
        with self.assertRaises(ModelParseError) as caught:
            self.model_type.from_dict(payload)
        # Then the non-finite value is rejected
        self.assertEqual(caught.exception.code, "non_finite")

    def test_valid_config_round_trips_through_json(self):
        # Given a parsed multi-analyte config
        model = self.model_type.from_dict(self.payload)
        # When it is serialized through JSON and parsed again
        restored = self.model_type.from_dict(json.loads(json.dumps(model.to_dict())))
        # Then the normalized domain value is unchanged
        self.assertEqual(restored, model)

    def test_config_and_nested_fields_are_immutable(self):
        # Given a parsed config
        model = self.model_type.from_dict(self.payload)
        # When mutation is attempted
        with self.assertRaises(FrozenInstanceError):
            model.droplet_count = 3
        # Then nested collections also expose immutable tuples
        self.assertIsInstance(model.analytes, tuple)
        self.assertIsInstance(model.route.waypoints, tuple)


class TestSamplingEvent(unittest.TestCase):
    def test_droplet_and_event_round_trip(self):
        from scripts.lib.lab_sim.models import SamplingEvent

        # Given one complete sampling event payload
        payload = _sampling_event_payload()
        # When it is parsed, serialized, and parsed again
        event = SamplingEvent.from_dict(payload)
        restored = SamplingEvent.from_dict(json.loads(json.dumps(event.to_dict())))
        # Then all bounded droplet and aggregate fields survive
        self.assertEqual(restored, event)
        self.assertEqual(restored.droplets[0].noise_flags, ("shot",))

    def test_droplet_fields_are_immutable(self):
        from scripts.lib.lab_sim.models import SamplingEvent

        # Given a parsed event
        event = SamplingEvent.from_dict(_sampling_event_payload())
        # When mutation is attempted
        with self.assertRaises(FrozenInstanceError):
            event.droplets[0].valid = False
        # Then the droplet remains unchanged
        self.assertTrue(event.droplets[0].valid)


def _sampling_event_payload():
    position = {
        "wgs84": {"lat": 25.314167, "lng": 110.412778},
        "gcj02": {"lat": 25.3112, "lng": 110.4173},
    }
    return {
        "schema_version": 2,
        "event_id": "sample-9",
        "mode": "waypoint",
        "route_ref": {"route_id": "route-7", "waypoint_index": 0},
        "position": position,
        "analyte_id": "nh3n",
        "droplets": [
            {
                "droplet_index": index,
                "offset_ms": 25 + index * 25,
                "voltage": 2.7,
                "absorbance": 0.12,
                "truth_concentration": 1.8,
                "estimated_concentration": 1.75,
                "valid": True,
                "saturated": False,
                "noise_flags": ["shot"] if index == 0 else [],
            }
            for index in range(3)
        ],
        "mean": 1.75,
        "median": 1.75,
        "standard_deviation": 0.0,
        "valid_count": 3,
        "quality_flags": [],
        "config_snapshot": {"schema_version": 2, "droplet_count": 3},
    }

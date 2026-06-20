import math
import unittest

from scripts.lib.lab_sim.coordinates import (
    COORDINATE_SCHEMA_VERSION,
    Coordinate,
    CoordinateBounds,
    CoordinateError,
    CoordinatePair,
    LocalEnu,
    gcj02_to_wgs84,
    haversine_m,
    local_enu_to_wgs84,
    parse_bounds,
    parse_coordinate,
    wgs84_to_gcj02,
    wgs84_to_local_enu,
)


class TestLabSimCoordinates(unittest.TestCase):
    def test_coordinate_pair_uses_schema_version_two(self):
        # Given: a valid WGS-84 coordinate in Guilin.
        wgs84 = Coordinate(lat=25.314167, lng=110.412778)

        # When: the versioned coordinate pair is constructed.
        pair = CoordinatePair.from_wgs84(wgs84)

        # Then: schema v2 and both coordinate systems are explicit.
        self.assertEqual(pair.coordinate_schema_version, COORDINATE_SCHEMA_VERSION)
        self.assertEqual(pair.coordinate_schema_version, 2)
        self.assertEqual(pair.wgs84, wgs84)
        self.assertGreater(haversine_m(pair.wgs84, pair.gcj02), 100.0)

    def test_wgs_gcj_round_trip_stays_within_half_meter(self):
        locations = (
            ("Guilin", Coordinate(25.314167, 110.412778)),
            ("Shanghai", Coordinate(31.2304, 121.4737)),
            ("Beijing", Coordinate(39.9042, 116.4074)),
            ("London", Coordinate(51.5074, -0.1278)),
        )

        for name, wgs84 in locations:
            with self.subTest(name=name):
                # Given: a finite WGS-84 location.
                gcj02 = wgs84_to_gcj02(wgs84)

                # When: GCJ-02 is iteratively converted back to WGS-84.
                restored = gcj02_to_wgs84(gcj02)

                # Then: the ground round-trip error is at most 0.5 m.
                self.assertLessEqual(haversine_m(wgs84, restored), 0.5)

    def test_outside_china_conversion_is_identity(self):
        # Given: a WGS-84 coordinate outside China.
        london = Coordinate(51.5074, -0.1278, 35.0)

        # When: it is converted in both directions.
        gcj02 = wgs84_to_gcj02(london)
        restored = gcj02_to_wgs84(gcj02)

        # Then: no GCJ offset is applied and altitude is preserved.
        self.assertEqual(gcj02, london)
        self.assertEqual(restored, london)

    def test_local_enu_round_trip_preserves_position(self):
        # Given: a Guilin origin and a nearby WGS-84 point.
        origin = Coordinate(25.314167, 110.412778, 120.0)
        target = Coordinate(25.315012, 110.414125, 124.5)

        # When: the target is converted to local ENU and back.
        enu = wgs84_to_local_enu(target, origin)
        restored = local_enu_to_wgs84(enu, origin)

        # Then: horizontal error is sub-centimeter and altitude is restored.
        self.assertLess(haversine_m(target, restored), 0.01)
        self.assertAlmostEqual(restored.alt or 0.0, 124.5, places=9)
        self.assertGreater(enu.east_m, 0.0)
        self.assertGreater(enu.north_m, 0.0)
        self.assertAlmostEqual(enu.up_m, 4.5, places=9)

    def test_haversine_returns_known_equatorial_distance(self):
        # Given: two points one degree apart on the equator.
        start = Coordinate(0.0, 0.0)
        end = Coordinate(0.0, 1.0)

        # When: their great-circle distance is calculated.
        distance_m = haversine_m(start, end)

        # Then: the result matches the mean-Earth-radius distance.
        self.assertAlmostEqual(distance_m, 111195.08, delta=0.5)

    def test_parse_bounds_accepts_valid_rectangle(self):
        # Given: finite southwest and northeast corner values.
        values = (25.22, 110.31, 25.41, 110.51)

        # When: the values are parsed as coordinate bounds.
        bounds = parse_bounds(*values)

        # Then: corners are typed and containment is inclusive.
        self.assertEqual(
            bounds,
            CoordinateBounds(
                southwest=Coordinate(25.22, 110.31),
                northeast=Coordinate(25.41, 110.51),
            ),
        )
        self.assertTrue(bounds.contains(Coordinate(25.314167, 110.412778)))
        self.assertTrue(bounds.contains(bounds.southwest))

    def test_parse_bounds_rejects_inverted_rectangle(self):
        # Given: a southwest corner north of the northeast corner.
        values = (25.41, 110.31, 25.22, 110.51)

        # When/Then: parsing rejects the malformed bounds.
        with self.assertRaises(CoordinateError):
            parse_bounds(*values)

    def test_parse_coordinate_rejects_invalid_and_non_finite_values(self):
        invalid_values = (
            ("latitude below range", -90.000001, 110.0),
            ("latitude above range", 90.000001, 110.0),
            ("longitude below range", 25.0, -180.000001),
            ("longitude above range", 25.0, 180.000001),
            ("not numeric", "north", 110.0),
            ("nan latitude", math.nan, 110.0),
            ("infinite longitude", 25.0, math.inf),
        )

        for name, lat, lng in invalid_values:
            with self.subTest(name=name):
                # Given: malformed or non-finite coordinate input.
                # When/Then: boundary parsing rejects it explicitly.
                with self.assertRaises(CoordinateError):
                    parse_coordinate(lat, lng)

    def test_local_enu_rejects_non_finite_components(self):
        # Given: a local coordinate containing infinity.
        invalid = LocalEnu(east_m=math.inf, north_m=0.0, up_m=0.0)
        origin = Coordinate(25.314167, 110.412778)

        # When/Then: conversion rejects the non-finite local component.
        with self.assertRaises(CoordinateError):
            local_enu_to_wgs84(invalid, origin)


if __name__ == "__main__":
    unittest.main()

import importlib
import math
import unittest


EARTH_RADIUS_M = 6_378_137.0
ORIGIN_LAT = 25.274
ORIGIN_LNG = 110.296


def _planner():
    return importlib.import_module("scripts.lib.lab_sim.route_planner")


def _wgs_polygon(points_m):
    lat_scale = EARTH_RADIUS_M * math.pi / 180.0
    lng_scale = lat_scale * math.cos(math.radians(ORIGIN_LAT))
    return tuple(
        {"lat": ORIGIN_LAT + north / lat_scale, "lng": ORIGIN_LNG + east / lng_scale}
        for east, north in points_m
    )


def _to_m(point):
    lat_scale = EARTH_RADIUS_M * math.pi / 180.0
    lng_scale = lat_scale * math.cos(math.radians(ORIGIN_LAT))
    return (
        (point["lng"] - ORIGIN_LNG) * lng_scale,
        (point["lat"] - ORIGIN_LAT) * lat_scale,
    )


def _point_in_polygon(point, polygon):
    x, y = point
    inside = False
    previous = polygon[-1]
    for current in polygon:
        x1, y1 = previous
        x2, y2 = current
        cross = (x - x2) * (y1 - y2) - (y - y2) * (x1 - x2)
        if abs(cross) <= 1e-7 and min(x1, x2) - 1e-7 <= x <= max(x1, x2) + 1e-7:
            if min(y1, y2) - 1e-7 <= y <= max(y1, y2) + 1e-7:
                return True
        if (y2 > y) != (y1 > y):
            crossing_x = (x1 - x2) * (y - y2) / (y1 - y2) + x2
            if x <= crossing_x:
                inside = not inside
        previous = current
    return inside


def _distance_to_segment(point, start, end):
    px, py = point
    ax, ay = start
    bx, by = end
    dx, dy = bx - ax, by - ay
    length_sq = dx * dx + dy * dy
    ratio = 0.0 if length_sq == 0.0 else ((px - ax) * dx + (py - ay) * dy) / length_sq
    ratio = max(0.0, min(1.0, ratio))
    return math.hypot(px - (ax + ratio * dx), py - (ay + ratio * dy))


def _assert_route_allowed(test, route, polygon_m, margin_m):
    points = tuple(_to_m(point) for point in route)
    for point in points:
        test.assertTrue(_point_in_polygon(point, polygon_m), point)
        edge_dist = min(
            _distance_to_segment(point, polygon_m[index - 1], polygon_m[index])
            for index in range(len(polygon_m))
        )
        test.assertGreaterEqual(edge_dist + 0.02, margin_m, point)
    for start, end in zip(points, points[1:]):
        for step in range(1, 20):
            ratio = step / 20.0
            sample = (
                start[0] + ratio * (end[0] - start[0]),
                start[1] + ratio * (end[1] - start[1]),
            )
            test.assertTrue(_point_in_polygon(sample, polygon_m), (start, end, sample))
            edge_dist = min(
                _distance_to_segment(sample, polygon_m[index - 1], polygon_m[index])
                for index in range(len(polygon_m))
            )
            test.assertGreaterEqual(edge_dist + 0.08, margin_m, sample)


class LabSimRoutePlannerTests(unittest.TestCase):
    def test_convex_polygon_generates_deterministic_boustrophedon_route(self):
        # Given a rectangular WGS-84 water area.
        polygon_m = ((0.0, 0.0), (80.0, 0.0), (80.0, 40.0), (0.0, 40.0))
        polygon = _wgs_polygon(polygon_m)

        # When coverage planning is repeated in fresh calls.
        first = _planner().plan_coverage_route(
            polygon, heading_deg=90.0, strip_spacing_m=10.0, inward_margin_m=2.0
        )
        second = _planner().plan_coverage_route(
            polygon, heading_deg=90.0, strip_spacing_m=10.0, inward_margin_m=2.0
        )

        # Then ordering is stable and every route segment remains in the inset.
        self.assertEqual(first, second)
        self.assertGreaterEqual(len(first), 6)
        _assert_route_allowed(self, first, polygon_m, 2.0)

    def test_concave_polygon_connects_disjoint_scan_runs_inside_water(self):
        # Given a U-shaped concave polygon whose horizontal rows split in two.
        polygon_m = (
            (0.0, 0.0),
            (80.0, 0.0),
            (80.0, 60.0),
            (55.0, 60.0),
            (55.0, 20.0),
            (25.0, 20.0),
            (25.0, 60.0),
            (0.0, 60.0),
        )

        # When turn connections are requested.
        route = _planner().plan_coverage_route(
            _wgs_polygon(polygon_m),
            heading_deg=90.0,
            strip_spacing_m=12.0,
            inward_margin_m=2.0,
            connect_turns=True,
        )

        # Then the connected route never cuts across the dry notch.
        self.assertGreaterEqual(len(route), 8)
        _assert_route_allowed(self, route, polygon_m, 2.0)

    def test_narrow_water_still_gets_one_centered_strip(self):
        # Given a water area narrower than the configured strip spacing.
        polygon_m = ((0.0, 0.0), (60.0, 0.0), (60.0, 5.0), (0.0, 5.0))

        # When it is planned with a safe inward margin.
        route = _planner().plan_coverage_route(
            _wgs_polygon(polygon_m),
            heading_deg=90.0,
            strip_spacing_m=10.0,
            inward_margin_m=1.0,
        )

        # Then one usable scan strip is returned inside the inset.
        self.assertEqual(len(route), 2)
        _assert_route_allowed(self, route, polygon_m, 1.0)

    def test_self_intersecting_polygon_is_rejected(self):
        # Given a bow-tie polygon.
        polygon = _wgs_polygon(((0.0, 0.0), (40.0, 40.0), (0.0, 40.0), (40.0, 0.0)))

        # When coverage planning validates it.
        with self.assertRaises(_planner().RoutePlannerError) as raised:
            _planner().plan_coverage_route(polygon, strip_spacing_m=5.0)

        # Then the error identifies the invalid polygon.
        self.assertEqual(raised.exception.code, "self_intersection")

    def test_inward_margin_is_enforced_for_every_waypoint_and_connection(self):
        # Given a large convex area and a non-axis-aligned scan heading.
        polygon_m = ((0.0, 0.0), (90.0, 0.0), (90.0, 55.0), (0.0, 55.0))

        # When an eight metre inward margin is requested.
        route = _planner().plan_coverage_route(
            _wgs_polygon(polygon_m),
            heading_deg=32.0,
            strip_spacing_m=9.0,
            inward_margin_m=8.0,
        )

        # Then the full route respects that metric margin.
        _assert_route_allowed(self, route, polygon_m, 8.0)

    def test_max_waypoint_limit_rejects_oversized_route(self):
        # Given an area requiring several strips.
        polygon = _wgs_polygon(((0.0, 0.0), (100.0, 0.0), (100.0, 80.0), (0.0, 80.0)))

        # When the waypoint budget is too small.
        with self.assertRaises(_planner().RoutePlannerError) as raised:
            _planner().plan_coverage_route(
                polygon, heading_deg=90.0, strip_spacing_m=5.0, max_waypoints=4
            )

        # Then planning fails instead of truncating the route.
        self.assertEqual(raised.exception.code, "max_waypoints_exceeded")

    def test_malformed_polygon_and_parameters_are_rejected(self):
        # Given malformed polygon and scan parameter variants.
        valid = _wgs_polygon(((0.0, 0.0), (20.0, 0.0), (20.0, 20.0), (0.0, 20.0)))
        cases = (
            (valid[:2], {"strip_spacing_m": 5.0}),
            (valid, {"strip_spacing_m": 0.0}),
            (valid, {"heading_deg": math.nan, "strip_spacing_m": 5.0}),
            (valid, {"heading_deg": 360.1, "strip_spacing_m": 5.0}),
            (valid, {"strip_spacing_m": 5.0, "inward_margin_m": -1.0}),
            (valid, {"strip_spacing_m": 5.0, "max_waypoints": 1}),
        )

        # When each malformed request is planned.
        for polygon, kwargs in cases:
            with self.subTest(kwargs=kwargs):
                with self.assertRaises(_planner().RoutePlannerError):
                    _planner().plan_coverage_route(polygon, **kwargs)

        # Then every malformed request has been rejected by the boundary.

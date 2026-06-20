from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Final, TypedDict

from scripts.lib.lab_sim.route_geometry import (
    EPSILON_M,
    Point,
    connection_nodes,
    connect_route_segment,
    cross_product,
    rotate,
    scan_runs,
    segments_intersect,
    unrotate,
)

EARTH_RADIUS_M: Final = 6_378_137.0
MIN_AREA_M2: Final = 1.0


class Wgs84Coordinate(TypedDict):
    lat: float
    lng: float


@dataclass(frozen=True)
class RoutePlannerError(Exception):
    code: str
    detail: str

    def __str__(self) -> str:
        return f"{self.code}: {self.detail}"


def _fail(code: str, detail: str) -> RoutePlannerError:
    return RoutePlannerError(code=code, detail=detail)


def plan_coverage_route(
    polygon: Sequence[Wgs84Coordinate],
    *,
    heading_deg: float = 0.0,
    strip_spacing_m: float,
    inward_margin_m: float = 0.0,
    connect_turns: bool = True,
    max_waypoints: int = 500,
) -> tuple[Wgs84Coordinate, ...]:
    _validate_parameters(heading_deg, strip_spacing_m, inward_margin_m, max_waypoints)
    enu_polygon, origin_lat, origin_lng = _parse_polygon(polygon)
    heading_rad = math.radians(heading_deg)
    rotated = tuple(rotate(point, heading_rad) for point in enu_polygon)
    clearance = inward_margin_m + EPSILON_M
    runs = scan_runs(rotated, strip_spacing_m, clearance)
    if not runs:
        raise _fail("water_area_too_narrow", "no scan strip fits inside the requested margin")
    route = _connect_ordered_runs(runs, rotated, clearance, connect_turns, max_waypoints)
    return _to_wgs84_route(route, heading_rad, origin_lat, origin_lng)


def _validate_parameters(
    heading_deg: float,
    strip_spacing_m: float,
    inward_margin_m: float,
    max_waypoints: int,
) -> None:
    values = (heading_deg, strip_spacing_m, inward_margin_m)
    if not all(isinstance(value, (int, float)) and math.isfinite(value) for value in values):
        raise _fail("invalid_parameters", "heading, spacing and margin must be finite numbers")
    if not 0.0 <= heading_deg < 360.0 or strip_spacing_m <= 0.0 or inward_margin_m < 0.0:
        raise _fail("invalid_parameters", "heading, spacing or margin is outside its valid range")
    if isinstance(max_waypoints, bool) or not isinstance(max_waypoints, int) or max_waypoints < 2:
        raise _fail("invalid_parameters", "max_waypoints must be an integer of at least two")


def _parse_polygon(polygon: Sequence[Wgs84Coordinate]) -> tuple[tuple[Point, ...], float, float]:
    if len(polygon) < 3:
        raise _fail("invalid_polygon", "at least three vertices are required")
    latitudes, longitudes = _parse_vertices(polygon)
    origin_lat = sum(latitudes) / len(latitudes)
    origin_lng = sum(longitudes) / len(longitudes)
    scale_lat = EARTH_RADIUS_M * math.pi / 180.0
    scale_lng = scale_lat * math.cos(math.radians(origin_lat))
    points = tuple(
        ((lng - origin_lng) * scale_lng, (lat - origin_lat) * scale_lat)
        for lat, lng in zip(latitudes, longitudes)
    )
    _validate_polygon_shape(points)
    return points, origin_lat, origin_lng


def _parse_vertices(polygon: Sequence[Wgs84Coordinate]) -> tuple[list[float], list[float]]:
    latitudes: list[float] = []
    longitudes: list[float] = []
    for raw in polygon:
        try:
            lat, lng = float(raw["lat"]), float(raw["lng"])
        except (KeyError, TypeError, ValueError) as error:
            raise _fail("invalid_polygon", "vertices require numeric lat/lng") from error
        if not math.isfinite(lat) or not math.isfinite(lng) or not -90.0 <= lat <= 90.0:
            raise _fail("invalid_polygon", "latitude/longitude must be finite WGS-84 values")
        if not -180.0 <= lng <= 180.0:
            raise _fail("invalid_polygon", "latitude/longitude must be finite WGS-84 values")
        latitudes.append(lat)
        longitudes.append(lng)
    return latitudes, longitudes


def _validate_polygon_shape(points: tuple[Point, ...]) -> None:
    if len(set(points)) != len(points):
        raise _fail("invalid_polygon", "vertices must be unique")
    for index in range(len(points)):
        for other in range(index + 1, len(points)):
            if other in {index, index + 1} or (index == 0 and other == len(points) - 1):
                continue
            if segments_intersect(points[index - 1], points[index], points[other - 1], points[other]):
                raise _fail("self_intersection", "polygon edges intersect")
    area = abs(sum(cross_product((0.0, 0.0), points[index - 1], points[index]) for index in range(len(points)))) / 2.0
    if area < MIN_AREA_M2:
        raise _fail("polygon_too_small", "polygon area is below one square metre")


def _connect_ordered_runs(
    runs: Sequence[tuple[Point, Point]],
    polygon: Sequence[Point],
    clearance: float,
    connect_turns: bool,
    max_waypoints: int,
) -> list[Point]:
    ordered: list[Point] = []
    for index, run in enumerate(runs):
        ordered.extend(run if index % 2 == 0 else (run[1], run[0]))
    route: list[Point] = [ordered[0]]
    nodes = connection_nodes(polygon, clearance)
    for point in ordered[1:]:
        additions = _turn_additions(route[-1], point, polygon, clearance, nodes, connect_turns)
        route.extend(additions)
        if len(route) > max_waypoints:
            raise _fail("max_waypoints_exceeded", f"route requires more than {max_waypoints} waypoints")
    return route


def _turn_additions(
    start: Point,
    end: Point,
    polygon: Sequence[Point],
    clearance: float,
    nodes: Sequence[Point],
    connect_turns: bool,
) -> list[Point]:
    if not connect_turns:
        return [end]
    additions = connect_route_segment(start, end, polygon, clearance, nodes)
    if additions is None:
        raise _fail("turn_connection_failed", "no inward-safe connection exists between scan strips")
    return additions


def _to_wgs84_route(
    route: Sequence[Point],
    heading_rad: float,
    origin_lat: float,
    origin_lng: float,
) -> tuple[Wgs84Coordinate, ...]:
    scale_lat = EARTH_RADIUS_M * math.pi / 180.0
    scale_lng = scale_lat * math.cos(math.radians(origin_lat))
    return tuple(
        {
            "lat": round(origin_lat + north / scale_lat, 9),
            "lng": round(origin_lng + east / scale_lng, 9),
        }
        for east, north in (unrotate(point, heading_rad) for point in route)
    )

from __future__ import annotations

import math
from typing import Final, NamedTuple, Optional, TypedDict, Union

COORDINATE_SCHEMA_VERSION: Final = 2
_GCJ_A: Final = 6378245.0
_GCJ_EE: Final = 0.00669342162296594323
_MEAN_EARTH_RADIUS_M: Final = 6371008.8
_ENU_EARTH_RADIUS_M: Final = 6378137.0
_INVERSE_ITERATIONS: Final = 12
_INVERSE_TOLERANCE_DEG: Final = 1e-9

CoordinateScalar = Union[int, float, str]


class CoordinateDict(TypedDict):
    lat: float
    lng: float
    alt: Optional[float]


class CoordinatePairDict(TypedDict):
    coordinate_schema_version: int
    wgs84: CoordinateDict
    gcj02: CoordinateDict


class CoordinateError(ValueError):
    __slots__ = ("field", "reason")

    def __init__(self, field: str, reason: str) -> None:
        self.field = field
        self.reason = reason
        super().__init__(str(self))

    def __str__(self) -> str:
        return f"invalid {self.field}: {self.reason}"


class Coordinate(NamedTuple):
    lat: float
    lng: float
    alt: Optional[float] = None

    def as_dict(self) -> CoordinateDict:
        return {"lat": self.lat, "lng": self.lng, "alt": self.alt}


class LocalEnu(NamedTuple):
    east_m: float
    north_m: float
    up_m: float = 0.0


class CoordinateBounds(NamedTuple):
    southwest: Coordinate
    northeast: Coordinate

    def contains(self, point: Coordinate) -> bool:
        valid = _validated_coordinate(point)
        return (
            self.southwest.lat <= valid.lat <= self.northeast.lat
            and self.southwest.lng <= valid.lng <= self.northeast.lng
        )


class CoordinatePair(NamedTuple):
    coordinate_schema_version: int
    wgs84: Coordinate
    gcj02: Coordinate

    @classmethod
    def from_wgs84(cls, coordinate: Coordinate) -> CoordinatePair:
        wgs84 = _validated_coordinate(coordinate)
        return cls(COORDINATE_SCHEMA_VERSION, wgs84, wgs84_to_gcj02(wgs84))

    @classmethod
    def from_gcj02(cls, coordinate: Coordinate) -> CoordinatePair:
        gcj02 = _validated_coordinate(coordinate)
        return cls(COORDINATE_SCHEMA_VERSION, gcj02_to_wgs84(gcj02), gcj02)

    def as_dict(self) -> CoordinatePairDict:
        return {
            "coordinate_schema_version": self.coordinate_schema_version,
            "wgs84": self.wgs84.as_dict(),
            "gcj02": self.gcj02.as_dict(),
        }


def _finite_float(value: CoordinateScalar, field: str) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise CoordinateError(field, "must be numeric") from exc
    if not math.isfinite(parsed):
        raise CoordinateError(field, "must be finite")
    return parsed


def parse_coordinate(
    lat: CoordinateScalar,
    lng: CoordinateScalar,
    alt: Optional[CoordinateScalar] = None,
) -> Coordinate:
    parsed_lat = _finite_float(lat, "latitude")
    parsed_lng = _finite_float(lng, "longitude")
    if not -90.0 <= parsed_lat <= 90.0:
        raise CoordinateError("latitude", "must be within [-90, 90]")
    if not -180.0 <= parsed_lng <= 180.0:
        raise CoordinateError("longitude", "must be within [-180, 180]")
    parsed_alt = None if alt is None else _finite_float(alt, "altitude")
    return Coordinate(parsed_lat, parsed_lng, parsed_alt)


def _validated_coordinate(coordinate: Coordinate) -> Coordinate:
    return parse_coordinate(coordinate.lat, coordinate.lng, coordinate.alt)


def parse_bounds(
    southwest_lat: CoordinateScalar,
    southwest_lng: CoordinateScalar,
    northeast_lat: CoordinateScalar,
    northeast_lng: CoordinateScalar,
) -> CoordinateBounds:
    southwest = parse_coordinate(southwest_lat, southwest_lng)
    northeast = parse_coordinate(northeast_lat, northeast_lng)
    if southwest.lat >= northeast.lat:
        raise CoordinateError("bounds.latitude", "southwest must be south of northeast")
    if southwest.lng >= northeast.lng:
        raise CoordinateError("bounds.longitude", "southwest must be west of northeast")
    return CoordinateBounds(southwest, northeast)


def _outside_china(coordinate: Coordinate) -> bool:
    return not (
        72.004 <= coordinate.lng <= 137.8347
        and 0.8293 <= coordinate.lat <= 55.8271
    )


def _transform_lat(x: float, y: float) -> float:
    result = (
        -100.0
        + 2.0 * x
        + 3.0 * y
        + 0.2 * y * y
        + 0.1 * x * y
        + 0.2 * math.sqrt(abs(x))
    )
    result += (
        20.0 * math.sin(6.0 * x * math.pi)
        + 20.0 * math.sin(2.0 * x * math.pi)
    ) * 2.0 / 3.0
    result += (
        20.0 * math.sin(y * math.pi)
        + 40.0 * math.sin(y / 3.0 * math.pi)
    ) * 2.0 / 3.0
    result += (
        160.0 * math.sin(y / 12.0 * math.pi)
        + 320.0 * math.sin(y * math.pi / 30.0)
    ) * 2.0 / 3.0
    return result


def _transform_lng(x: float, y: float) -> float:
    result = (
        300.0
        + x
        + 2.0 * y
        + 0.1 * x * x
        + 0.1 * x * y
        + 0.1 * math.sqrt(abs(x))
    )
    result += (
        20.0 * math.sin(6.0 * x * math.pi)
        + 20.0 * math.sin(2.0 * x * math.pi)
    ) * 2.0 / 3.0
    result += (
        20.0 * math.sin(x * math.pi)
        + 40.0 * math.sin(x / 3.0 * math.pi)
    ) * 2.0 / 3.0
    result += (
        150.0 * math.sin(x / 12.0 * math.pi)
        + 300.0 * math.sin(x / 30.0 * math.pi)
    ) * 2.0 / 3.0
    return result


def _raw_wgs84_to_gcj02(coordinate: Coordinate) -> Coordinate:
    if _outside_china(coordinate):
        return coordinate
    x = coordinate.lng - 105.0
    y = coordinate.lat - 35.0
    delta_lat = _transform_lat(x, y)
    delta_lng = _transform_lng(x, y)
    rad_lat = math.radians(coordinate.lat)
    magic = math.sin(rad_lat)
    magic = 1.0 - _GCJ_EE * magic * magic
    sqrt_magic = math.sqrt(magic)
    delta_lat = math.degrees(
        delta_lat * math.pi / ((_GCJ_A * (1.0 - _GCJ_EE)) / (magic * sqrt_magic))
    )
    delta_lng = math.degrees(
        delta_lng * math.pi / (_GCJ_A / sqrt_magic * math.cos(rad_lat))
    )
    return Coordinate(
        coordinate.lat + delta_lat,
        coordinate.lng + delta_lng,
        coordinate.alt,
    )


def wgs84_to_gcj02(coordinate: Coordinate) -> Coordinate:
    return _raw_wgs84_to_gcj02(_validated_coordinate(coordinate))


def gcj02_to_wgs84(coordinate: Coordinate) -> Coordinate:
    gcj02 = _validated_coordinate(coordinate)
    if _outside_china(gcj02):
        return gcj02
    estimate = Coordinate(gcj02.lat, gcj02.lng, gcj02.alt)
    for _ in range(_INVERSE_ITERATIONS):
        projected = _raw_wgs84_to_gcj02(estimate)
        delta_lat = projected.lat - gcj02.lat
        delta_lng = projected.lng - gcj02.lng
        estimate = Coordinate(
            estimate.lat - delta_lat,
            estimate.lng - delta_lng,
            gcj02.alt,
        )
        if (
            abs(delta_lat) <= _INVERSE_TOLERANCE_DEG
            and abs(delta_lng) <= _INVERSE_TOLERANCE_DEG
        ):
            break
    return _validated_coordinate(estimate)


def haversine_m(start: Coordinate, end: Coordinate) -> float:
    first = _validated_coordinate(start)
    second = _validated_coordinate(end)
    lat1 = math.radians(first.lat)
    lat2 = math.radians(second.lat)
    delta_lat = lat2 - lat1
    delta_lng = math.radians(second.lng - first.lng)
    haversine = (
        math.sin(delta_lat / 2.0) ** 2
        + math.cos(lat1) * math.cos(lat2) * math.sin(delta_lng / 2.0) ** 2
    )
    angle = 2.0 * math.atan2(math.sqrt(haversine), math.sqrt(1.0 - haversine))
    return _MEAN_EARTH_RADIUS_M * angle


def wgs84_to_local_enu(coordinate: Coordinate, origin: Coordinate) -> LocalEnu:
    point = _validated_coordinate(coordinate)
    reference = _validated_coordinate(origin)
    mean_lat = math.radians((point.lat + reference.lat) / 2.0)
    east_m = (
        math.radians(point.lng - reference.lng)
        * _ENU_EARTH_RADIUS_M
        * math.cos(mean_lat)
    )
    north_m = math.radians(point.lat - reference.lat) * _ENU_EARTH_RADIUS_M
    up_m = (point.alt or 0.0) - (reference.alt or 0.0)
    return LocalEnu(east_m, north_m, up_m)


def local_enu_to_wgs84(coordinate: LocalEnu, origin: Coordinate) -> Coordinate:
    reference = _validated_coordinate(origin)
    east_m = _finite_float(coordinate.east_m, "east_m")
    north_m = _finite_float(coordinate.north_m, "north_m")
    up_m = _finite_float(coordinate.up_m, "up_m")
    lat = reference.lat + math.degrees(north_m / _ENU_EARTH_RADIUS_M)
    mean_lat = math.radians((lat + reference.lat) / 2.0)
    lng = reference.lng + math.degrees(
        east_m / (_ENU_EARTH_RADIUS_M * math.cos(mean_lat))
    )
    alt = (reference.alt or 0.0) + up_m
    return parse_coordinate(lat, lng, alt)

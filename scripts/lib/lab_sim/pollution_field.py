"""Deterministic local-ENU pollution concentration field."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import math
import random
from typing import Final


EARTH_RADIUS_M: Final = 6_378_137.0
REFERENCE_MATCH_TOLERANCE_M: Final = 1e-6


@dataclass(frozen=True)
class PollutionFieldConfigError(Exception):
    field: str
    reason: str

    def __str__(self) -> str:
        return f"invalid {self.field}: {self.reason}"


def _require_finite(field: str, value: float) -> None:
    if not math.isfinite(value):
        raise PollutionFieldConfigError(field, "must be finite")


@dataclass(frozen=True)
class Wgs84Point:
    latitude_deg: float
    longitude_deg: float

    def __post_init__(self) -> None:
        _require_finite("latitude_deg", self.latitude_deg)
        _require_finite("longitude_deg", self.longitude_deg)
        if not -90.0 <= self.latitude_deg <= 90.0:
            raise PollutionFieldConfigError("latitude_deg", "must be within [-90, 90]")
        if not -180.0 <= self.longitude_deg <= 180.0:
            raise PollutionFieldConfigError(
                "longitude_deg", "must be within [-180, 180]"
            )


@dataclass(frozen=True)
class ConcentrationBounds:
    lower: float
    upper: float

    def __post_init__(self) -> None:
        _require_finite("bounds.lower", self.lower)
        _require_finite("bounds.upper", self.upper)
        if self.lower >= self.upper:
            raise PollutionFieldConfigError(
                "bounds", "lower must be less than upper"
            )

    def clamp(self, value: float) -> float:
        return min(self.upper, max(self.lower, value))


@dataclass(frozen=True)
class BackgroundField:
    mean: float
    noise_std: float = 0.0

    def __post_init__(self) -> None:
        _require_finite("background.mean", self.mean)
        _require_finite("background.noise_std", self.noise_std)
        if self.noise_std < 0.0:
            raise PollutionFieldConfigError(
                "background.noise_std", "must be non-negative"
            )


@dataclass(frozen=True)
class PollutionSource:
    location: Wgs84Point
    peak: float
    major_scale_m: float
    minor_scale_m: float
    orientation_deg: float = 0.0
    decay_length_m: float | None = None

    def __post_init__(self) -> None:
        _require_finite("source.peak", self.peak)
        _require_finite("source.major_scale_m", self.major_scale_m)
        _require_finite("source.minor_scale_m", self.minor_scale_m)
        _require_finite("source.orientation_deg", self.orientation_deg)
        if self.peak < 0.0:
            raise PollutionFieldConfigError("source.peak", "must be non-negative")
        if self.major_scale_m <= 0.0:
            raise PollutionFieldConfigError(
                "source.major_scale_m", "must be positive"
            )
        if self.minor_scale_m <= 0.0:
            raise PollutionFieldConfigError(
                "source.minor_scale_m", "must be positive"
            )
        if self.decay_length_m is not None:
            _require_finite("source.decay_length_m", self.decay_length_m)
            if self.decay_length_m <= 0.0:
                raise PollutionFieldConfigError(
                    "source.decay_length_m", "must be positive"
                )


@dataclass(frozen=True)
class FieldReferencePoint:
    location: Wgs84Point
    concentration: float
    influence_scale_m: float

    def __post_init__(self) -> None:
        _require_finite("reference.concentration", self.concentration)
        _require_finite("reference.influence_scale_m", self.influence_scale_m)
        if self.influence_scale_m <= 0.0:
            raise PollutionFieldConfigError(
                "reference.influence_scale_m", "must be positive"
            )


@dataclass(frozen=True)
class PollutionField:
    origin: Wgs84Point
    background: BackgroundField
    sources: tuple[PollutionSource, ...]
    reference_points: tuple[FieldReferencePoint, ...]
    bounds: ConcentrationBounds
    seed: int = 0

    def __post_init__(self) -> None:
        object.__setattr__(self, "sources", tuple(self.sources))
        object.__setattr__(self, "reference_points", tuple(self.reference_points))
        if type(self.seed) is not int:
            raise PollutionFieldConfigError("seed", "must be an integer")
        if not all(type(source) is PollutionSource for source in self.sources):
            raise PollutionFieldConfigError(
                "sources", "must contain PollutionSource values"
            )
        if not all(
            type(reference) is FieldReferencePoint
            for reference in self.reference_points
        ):
            raise PollutionFieldConfigError(
                "reference_points", "must contain FieldReferencePoint values"
            )
        for index, reference in enumerate(self.reference_points):
            if not self.bounds.lower <= reference.concentration <= self.bounds.upper:
                raise PollutionFieldConfigError(
                    f"reference_points[{index}].concentration",
                    "must be within concentration bounds",
                )
            for previous in self.reference_points[:index]:
                if self._distance_m(reference.location, previous.location) <= (
                    REFERENCE_MATCH_TOLERANCE_M
                ):
                    raise PollutionFieldConfigError(
                        "reference_points", "locations must be unique"
                    )

    def concentration_at(self, point: Wgs84Point) -> float:
        for reference in self.reference_points:
            if self._distance_m(point, reference.location) <= (
                REFERENCE_MATCH_TOLERANCE_M
            ):
                return self.bounds.clamp(reference.concentration)

        base_value = self._base_concentration(point)
        weighted_residual = 0.0
        total_weight = 0.0
        for reference in self.reference_points:
            distance_m = self._distance_m(point, reference.location)
            scaled_distance = distance_m / reference.influence_scale_m
            weight = math.exp(-0.5 * scaled_distance * scaled_distance) / (
                scaled_distance * scaled_distance + 1e-12
            )
            residual = (
                reference.concentration
                - self._base_concentration(reference.location)
            )
            weighted_residual += weight * residual
            total_weight += weight
        correction = weighted_residual / (1.0 + total_weight)
        return self.bounds.clamp(base_value + correction)

    def _base_concentration(self, point: Wgs84Point) -> float:
        value = self.background.mean + self._background_noise(point)
        point_north_m, point_east_m = self._enu_m(point)
        for source in self.sources:
            source_north_m, source_east_m = self._enu_m(source.location)
            north_m = point_north_m - source_north_m
            east_m = point_east_m - source_east_m
            angle_rad = math.radians(source.orientation_deg)
            along_m = north_m * math.cos(angle_rad) + east_m * math.sin(angle_rad)
            across_m = -north_m * math.sin(angle_rad) + east_m * math.cos(angle_rad)
            exponent = -0.5 * (
                (along_m / source.major_scale_m) ** 2
                + (across_m / source.minor_scale_m) ** 2
            )
            contribution = source.peak * math.exp(exponent)
            if source.decay_length_m is not None:
                contribution *= math.exp(
                    -math.hypot(north_m, east_m) / source.decay_length_m
                )
            value += contribution
        return value

    def _background_noise(self, point: Wgs84Point) -> float:
        if self.background.noise_std == 0.0:
            return 0.0
        key = (
            f"{self.seed}:{point.latitude_deg:.12f}:"
            f"{point.longitude_deg:.12f}"
        ).encode("ascii")
        digest = hashlib.blake2b(key, digest_size=16).digest()
        coordinate_seed = int.from_bytes(digest, byteorder="big", signed=False)
        return random.Random(coordinate_seed).gauss(0.0, self.background.noise_std)

    def _enu_m(self, point: Wgs84Point) -> tuple[float, float]:
        origin_lat_rad = math.radians(self.origin.latitude_deg)
        point_lat_rad = math.radians(point.latitude_deg)
        longitude_delta_rad = math.radians(
            point.longitude_deg - self.origin.longitude_deg
        )
        longitude_delta_rad = (
            longitude_delta_rad + math.pi
        ) % (2.0 * math.pi) - math.pi
        north_m = EARTH_RADIUS_M * (point_lat_rad - origin_lat_rad)
        east_m = (
            EARTH_RADIUS_M
            * longitude_delta_rad
            * math.cos((origin_lat_rad + point_lat_rad) / 2.0)
        )
        return north_m, east_m

    def _distance_m(self, first: Wgs84Point, second: Wgs84Point) -> float:
        first_north_m, first_east_m = self._enu_m(first)
        second_north_m, second_east_m = self._enu_m(second)
        return math.hypot(
            first_north_m - second_north_m,
            first_east_m - second_east_m,
        )

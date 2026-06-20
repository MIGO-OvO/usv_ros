"""Masked ENU scientific surface generation for lab simulation snapshots."""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from typing import Final, Tuple

import numpy as np

from scripts.lib.lab_sim.calibration import (
    BeerLambertConfig,
    CalibrationInvalid,
    CalibrationSaturated,
    CalibrationValue,
    WorkCurveConfig,
    absorbance_from_concentration,
    voltage_from_absorbance,
)
from scripts.lib.lab_sim.coordinates import (
    Coordinate,
    CoordinateBounds,
    LocalEnu,
    local_enu_to_wgs84,
    parse_bounds,
    wgs84_to_local_enu,
)
from scripts.lib.lab_sim.models import SamplingEvent, WaterSnapshot
from scripts.lib.lab_sim.pollution_field import PollutionField, Wgs84Point


LAYER_NAMES: Final = (
    "truth",
    "reconstruction",
    "error",
    "voltage",
    "absorbance",
    "risk",
)
MIN_GRID_SIZE: Final = 3
MAX_GRID_SIZE: Final = 512
POINT_MATCH_TOLERANCE_M: Final = 1e-6

Point = Tuple[float, float]


@dataclass(frozen=True)
class SurfaceBuildError(ValueError):
    field: str
    reason: str

    def __str__(self) -> str:
        return f"invalid {self.field}: {self.reason}"


@dataclass(frozen=True)
class SurfaceSample:
    east_m: float
    north_m: float
    value: float


@dataclass(frozen=True)
class SurfaceGrid:
    layers: dict[str, np.ndarray]
    x_east_m: np.ndarray
    y_north_m: np.ndarray
    outside_water_mask: np.ndarray
    bbox_wgs84: CoordinateBounds
    bbox_source: str
    enu_origin: Coordinate
    snapshot_hash: str
    grid_size: int
    seed: int
    idw_power: float


def build_surface_grid(
    *,
    water: WaterSnapshot,
    pollution_field: PollutionField,
    sampling_events: tuple[SamplingEvent, ...],
    grid_size: int,
    idw_power: float,
    seed: int,
    work_curve: WorkCurveConfig,
    beer_lambert: BeerLambertConfig,
) -> SurfaceGrid:
    size = _bounded_grid_size(grid_size)
    power = _positive_finite(idw_power, "idw_power")
    bbox = _water_bbox(water)
    origin = bbox.southwest
    northeast_enu = wgs84_to_local_enu(bbox.northeast, origin)
    east_axis = np.linspace(0.0, northeast_enu.east_m, size, dtype=np.float64)
    north_axis = np.linspace(0.0, northeast_enu.north_m, size, dtype=np.float64)
    polygon_enu = tuple(_point_enu(point.wgs84, origin) for point in water.polygon)
    samples = _event_samples(sampling_events, origin)
    layers = _empty_layers(size)
    outside_mask = np.ones((size, size), dtype=bool)

    for y_index, north_m in enumerate(north_axis):
        for x_index, east_m in enumerate(east_axis):
            if not _point_in_polygon((float(east_m), float(north_m)), polygon_enu):
                continue
            outside_mask[y_index, x_index] = False
            wgs84 = local_enu_to_wgs84(LocalEnu(float(east_m), float(north_m)), origin)
            truth = pollution_field.concentration_at(Wgs84Point(wgs84.lat, wgs84.lng))
            reconstruction = _idw_value(float(east_m), float(north_m), samples, power)
            absorbance = _calibration_value(absorbance_from_concentration(truth, work_curve))
            voltage = _calibration_value(voltage_from_absorbance(absorbance, beer_lambert))
            layers["truth"][y_index, x_index] = truth
            layers["reconstruction"][y_index, x_index] = reconstruction
            layers["error"][y_index, x_index] = truth - reconstruction
            layers["absorbance"][y_index, x_index] = absorbance
            layers["voltage"][y_index, x_index] = voltage

    layers["risk"] = _risk_layer(layers["truth"], layers["error"], outside_mask)
    _apply_mask(layers, outside_mask)
    return SurfaceGrid(
        layers=layers,
        x_east_m=east_axis,
        y_north_m=north_axis,
        outside_water_mask=outside_mask,
        bbox_wgs84=bbox,
        bbox_source="water_snapshot",
        enu_origin=origin,
        snapshot_hash=_snapshot_hash(water),
        grid_size=size,
        seed=seed,
        idw_power=power,
    )


def _bounded_grid_size(value: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise SurfaceBuildError("grid_size", "must be an integer")
    if not MIN_GRID_SIZE <= value <= MAX_GRID_SIZE:
        raise SurfaceBuildError("grid_size", "must be between 3 and 512")
    return value


def _positive_finite(value: float, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise SurfaceBuildError(field, "must be numeric")
    parsed = float(value)
    if not math.isfinite(parsed) or parsed <= 0.0:
        raise SurfaceBuildError(field, "must be positive and finite")
    return parsed


def _water_bbox(water: WaterSnapshot) -> CoordinateBounds:
    lats = tuple(point.wgs84.lat for point in water.polygon)
    lngs = tuple(point.wgs84.lng for point in water.polygon)
    return parse_bounds(min(lats), min(lngs), max(lats), max(lngs))


def _point_enu(point, origin: Coordinate) -> Point:
    enu = wgs84_to_local_enu(Coordinate(point.lat, point.lng, point.alt), origin)
    return float(enu.east_m), float(enu.north_m)


def _event_samples(events: tuple[SamplingEvent, ...], origin: Coordinate) -> tuple[SurfaceSample, ...]:
    samples = []
    for event in events:
        value = float(event.mean)
        if event.valid and math.isfinite(value):
            east_m, north_m = _point_enu(event.position.wgs84, origin)
            samples.append(SurfaceSample(east_m=east_m, north_m=north_m, value=value))
    if not samples:
        raise SurfaceBuildError("sampling_events", "at least one finite valid event is required")
    return tuple(samples)


def _empty_layers(size: int) -> dict[str, np.ndarray]:
    return {name: np.full((size, size), np.nan, dtype=np.float64) for name in LAYER_NAMES}


def _idw_value(east_m: float, north_m: float, samples: tuple[SurfaceSample, ...], power: float) -> float:
    weighted_sum = 0.0
    weight_total = 0.0
    for sample in samples:
        distance_m = math.hypot(east_m - sample.east_m, north_m - sample.north_m)
        if distance_m <= POINT_MATCH_TOLERANCE_M:
            return sample.value
        weight = 1.0 / math.pow(distance_m, power)
        weighted_sum += weight * sample.value
        weight_total += weight
    return weighted_sum / weight_total


def _calibration_value(result: CalibrationValue | CalibrationInvalid | CalibrationSaturated) -> float:
    if isinstance(result, CalibrationValue):
        return result.value
    if isinstance(result, CalibrationSaturated):
        return result.value
    return math.nan


def _risk_layer(truth: np.ndarray, error: np.ndarray, outside_mask: np.ndarray) -> np.ndarray:
    risk = np.full(truth.shape, np.nan, dtype=np.float64)
    inside = ~outside_mask
    if not np.any(inside):
        return risk
    truth_values = truth[inside]
    error_values = np.abs(error[inside])
    truth_scale = max(float(np.nanmax(truth_values)), 1.0)
    error_scale = max(float(np.nanmax(error_values)), 1.0)
    risk[inside] = np.clip(
        0.7 * truth_values / truth_scale + 0.3 * error_values / error_scale,
        0.0,
        1.0,
    )
    return risk


def _apply_mask(layers: dict[str, np.ndarray], outside_mask: np.ndarray) -> None:
    for layer in layers.values():
        layer[outside_mask] = np.nan


def _point_in_polygon(point: Point, polygon: tuple[Point, ...]) -> bool:
    inside = False
    previous = polygon[-1]
    for current in polygon:
        if _on_segment(point, previous, current):
            return True
        if (current[1] > point[1]) != (previous[1] > point[1]):
            crossing_x = (previous[0] - current[0]) * (point[1] - current[1]) / (previous[1] - current[1]) + current[0]
            if point[0] <= crossing_x:
                inside = not inside
        previous = current
    return inside


def _on_segment(point: Point, start: Point, end: Point, tolerance: float = 1e-8) -> bool:
    cross = (end[0] - start[0]) * (point[1] - start[1]) - (end[1] - start[1]) * (point[0] - start[0])
    return (
        abs(cross) <= tolerance
        and min(start[0], end[0]) - tolerance <= point[0] <= max(start[0], end[0]) + tolerance
        and min(start[1], end[1]) - tolerance <= point[1] <= max(start[1], end[1]) + tolerance
    )


def _snapshot_hash(water: WaterSnapshot) -> str:
    payload = json.dumps(
        water.to_dict(),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.blake2b(payload, digest_size=16).hexdigest()

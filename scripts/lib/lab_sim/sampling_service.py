from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Final

from scripts.lib.lab_sim.aggregation import aggregate_droplets
from scripts.lib.lab_sim.calibration import (
    BeerLambertConfig,
    CalibrationInvalid,
    CalibrationSaturated,
    CalibrationValue,
    WorkCurveConfig,
    absorbance_from_concentration,
    concentration_from_absorbance,
    voltage_from_absorbance,
)
from scripts.lib.lab_sim.coordinates import Coordinate, CoordinatePair
from scripts.lib.lab_sim.droplet_signal import (
    DropletGenerationConfig,
    DropletResult as GeneratedDropletResult,
    generate_droplets,
)
from scripts.lib.lab_sim.model_config import LabConfigV2
from scripts.lib.lab_sim.model_events import DropletResult, SamplingEvent
from scripts.lib.lab_sim.model_primitives import CoordinatePairRef, GeoPoint
from scripts.lib.lab_sim.pollution_field import (
    BackgroundField,
    ConcentrationBounds,
    PollutionField,
    PollutionSource,
    Wgs84Point,
)


DEFAULT_BACKGROUND_CONCENTRATION: Final = 0.0
DEFAULT_FIELD_SCALE_M: Final = 80.0
DEFAULT_REFERENCE_VOLTAGE: Final = 3.0
DEFAULT_DARK_VOLTAGE: Final = 0.0
DEFAULT_WORK_CURVE_SLOPE: Final = 0.1
DEFAULT_MINIMUM_VALID_DROPLETS: Final = 3


@dataclass(frozen=True)
class WaypointSamplingContext:
    waypoint_index: int


@dataclass(frozen=True)
class SurveySamplingContext:
    segment_index: int


SamplingContext = object


def generate_sampling_event(
    position_wgs84: Coordinate,
    config: LabConfigV2,
    *,
    context: SamplingContext,
    seed: int = 0,
    analyte_id: str | None = None,
    minimum_valid: int = DEFAULT_MINIMUM_VALID_DROPLETS,
) -> SamplingEvent:
    selected_analyte = config.analytes[0].analyte_id if analyte_id is None else analyte_id
    point = Wgs84Point(position_wgs84.lat, position_wgs84.lng)
    field = _field_for_analyte(config, selected_analyte, seed)
    truth_concentration = field.concentration_at(point)
    absorbance = _calibration_value(
        absorbance_from_concentration(
            truth_concentration,
            WorkCurveConfig(k=DEFAULT_WORK_CURVE_SLOPE, b=0.0),
        ),
    )
    voltage = _calibration_value(
        voltage_from_absorbance(
            absorbance,
            BeerLambertConfig(
                dark_voltage=DEFAULT_DARK_VOLTAGE,
                reference_voltage=DEFAULT_REFERENCE_VOLTAGE,
                saturation_voltage=None,
            ),
        ),
    )
    estimated_concentration = _calibration_value(
        concentration_from_absorbance(
            absorbance,
            WorkCurveConfig(k=DEFAULT_WORK_CURVE_SLOPE, b=0.0),
        ),
    )
    droplets = generate_droplets(
        voltage=voltage,
        absorbance=absorbance,
        truth_concentration=truth_concentration,
        estimated_concentration=estimated_concentration,
        seed=_derived_seed(seed, selected_analyte, context),
        config=DropletGenerationConfig(droplet_count=config.droplet_count),
    )
    aggregate = aggregate_droplets(droplets, minimum_valid=minimum_valid)
    route_mode, waypoint_index, segment_index = _route_ref(context)
    return SamplingEvent(
        schema_version=2,
        event_id=_event_id(seed, selected_analyte, context, position_wgs84),
        mode=route_mode,
        route_id=config.route.route_id,
        waypoint_index=waypoint_index,
        segment_index=segment_index,
        position=_coordinate_pair_ref(position_wgs84),
        analyte_id=selected_analyte,
        droplets=tuple(_event_droplet(item) for item in droplets),
        mean=aggregate.summary.estimated_concentration.mean,
        median=aggregate.summary.estimated_concentration.median,
        standard_deviation=aggregate.summary.estimated_concentration.stddev,
        valid_count=aggregate.summary.valid_count,
        quality_flags=aggregate.summary.quality_flags,
        config_droplet_count=config.droplet_count,
    )


def _field_for_analyte(config: LabConfigV2, analyte_id: str, seed: int) -> PollutionField:
    origin = _origin(config)
    peaks = tuple(_source_peak(source.concentrations, analyte_id) for source in config.sources)
    upper = max((sum(peaks), 1.0))
    return PollutionField(
        origin=origin,
        background=BackgroundField(mean=DEFAULT_BACKGROUND_CONCENTRATION),
        sources=tuple(
            PollutionSource(
                location=Wgs84Point(source.position.wgs84.lat, source.position.wgs84.lng),
                peak=peak,
                major_scale_m=DEFAULT_FIELD_SCALE_M,
                minor_scale_m=DEFAULT_FIELD_SCALE_M,
            )
            for source, peak in zip(config.sources, peaks)
            if peak > 0.0
        ),
        reference_points=(),
        bounds=ConcentrationBounds(0.0, upper),
        seed=seed,
    )


def _origin(config: LabConfigV2) -> Wgs84Point:
    if config.route.waypoints:
        point = config.route.waypoints[0].wgs84
        return Wgs84Point(point.lat, point.lng)
    point = config.sources[0].position.wgs84
    return Wgs84Point(point.lat, point.lng)


def _source_peak(concentrations: tuple[tuple[str, float], ...], analyte_id: str) -> float:
    values = tuple(value for key, value in concentrations if key == analyte_id)
    if not values:
        return 0.0
    return max(0.0, values[0])


def _calibration_value(
    result: CalibrationValue | CalibrationInvalid | CalibrationSaturated,
) -> float:
    if isinstance(result, CalibrationValue):
        return max(0.0, result.value)
    if isinstance(result, CalibrationSaturated):
        return max(0.0, result.value)
    if isinstance(result, CalibrationInvalid):
        return 0.0
    raise TypeError("unsupported calibration result: {}".format(type(result).__name__))


def _route_ref(context: SamplingContext) -> tuple[str, int | None, int | None]:
    if isinstance(context, WaypointSamplingContext):
        return "waypoint", context.waypoint_index, None
    if isinstance(context, SurveySamplingContext):
        return "survey", None, context.segment_index
    raise TypeError("unsupported sampling context: {}".format(type(context).__name__))


def _coordinate_pair_ref(position: Coordinate) -> CoordinatePairRef:
    pair = CoordinatePair.from_wgs84(position)
    return CoordinatePairRef(
        wgs84=GeoPoint(pair.wgs84.lat, pair.wgs84.lng, pair.wgs84.alt),
        gcj02=GeoPoint(pair.gcj02.lat, pair.gcj02.lng, pair.gcj02.alt),
    )


def _event_droplet(item: GeneratedDropletResult) -> DropletResult:
    return DropletResult(
        droplet_index=item.droplet_index,
        offset_ms=item.offset_ms,
        voltage=item.voltage,
        absorbance=item.absorbance,
        truth_concentration=item.truth_concentration,
        estimated_concentration=item.estimated_concentration,
        valid=item.valid,
        saturated=item.saturated,
        noise_flags=item.noise_flags,
    )


def _derived_seed(seed: int, analyte_id: str, context: SamplingContext) -> int:
    digest = hashlib.blake2b(
        f"{seed}:{analyte_id}:{_context_key(context)}".encode("utf-8"),
        digest_size=8,
    ).digest()
    return int.from_bytes(digest, byteorder="big", signed=False)


def _event_id(
    seed: int,
    analyte_id: str,
    context: SamplingContext,
    position: Coordinate,
) -> str:
    digest = hashlib.blake2b(
        (
            f"{seed}:{analyte_id}:{_context_key(context)}:"
            f"{position.lat:.9f}:{position.lng:.9f}"
        ).encode("utf-8"),
        digest_size=8,
    ).hexdigest()
    return f"sample-{digest}"


def _context_key(context: SamplingContext) -> str:
    mode, waypoint_index, segment_index = _route_ref(context)
    return f"{mode}:{waypoint_index}:{segment_index}"

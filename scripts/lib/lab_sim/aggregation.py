from __future__ import annotations

import math
import statistics
from dataclasses import dataclass
from collections.abc import Sequence

from scripts.lib.lab_sim.droplet_signal import DropletResult


class DropletAggregationError(ValueError):
    pass


@dataclass(frozen=True)
class DropletMetric:
    mean: float
    median: float
    stddev: float


@dataclass(frozen=True)
class DropletSummary:
    total_count: int
    valid_count: int
    valid: bool
    estimated_concentration: DropletMetric
    quality_flags: tuple[str, ...]


@dataclass(frozen=True)
class MapSample:
    estimated_concentration: float
    valid: bool
    valid_count: int


@dataclass(frozen=True)
class AggregatedDroplets:
    summary: DropletSummary
    map_sample: MapSample


def aggregate_droplets(
    droplets: Sequence[DropletResult],
    *,
    minimum_valid: int = 3,
) -> AggregatedDroplets:
    if isinstance(minimum_valid, bool) or not isinstance(minimum_valid, int):
        raise DropletAggregationError("minimum_valid must be an integer")
    if minimum_valid <= 0:
        raise DropletAggregationError("minimum_valid must be positive")

    valid_values = tuple(
        item.estimated_concentration
        for item in droplets
        if item.valid and _droplet_is_finite(item)
    )
    metric = _metric(valid_values)
    flags = _quality_flags(
        total_count=len(droplets),
        valid_count=len(valid_values),
        minimum_valid=minimum_valid,
    )
    valid = len(valid_values) >= minimum_valid
    return AggregatedDroplets(
        summary=DropletSummary(
            total_count=len(droplets),
            valid_count=len(valid_values),
            valid=valid,
            estimated_concentration=metric,
            quality_flags=flags,
        ),
        map_sample=MapSample(
            estimated_concentration=metric.mean,
            valid=valid,
            valid_count=len(valid_values),
        ),
    )


def _droplet_is_finite(item: DropletResult) -> bool:
    return all(
        math.isfinite(value)
        for value in (
            item.voltage,
            item.absorbance,
            item.truth_concentration,
            item.estimated_concentration,
        )
    )


def _metric(values: tuple[float, ...]) -> DropletMetric:
    if not values:
        return DropletMetric(mean=0.0, median=0.0, stddev=0.0)
    return DropletMetric(
        mean=statistics.fmean(values),
        median=statistics.median(values),
        stddev=statistics.pstdev(values),
    )


def _quality_flags(
    *,
    total_count: int,
    valid_count: int,
    minimum_valid: int,
) -> tuple[str, ...]:
    flags: list[str] = []
    if total_count == 0:
        flags.append("no_droplets")
    if valid_count < total_count:
        flags.append("invalid_droplets_excluded")
    if valid_count < minimum_valid:
        flags.append("insufficient_valid_droplets")
    return tuple(flags)

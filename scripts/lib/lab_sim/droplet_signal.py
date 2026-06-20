"""Bounded synthetic droplet signal generation for lab simulation events."""

from __future__ import annotations

import math
import random
from dataclasses import dataclass, replace


class DropletGenerationError(ValueError):
    """Raised when droplet generation receives malformed bounded input."""


@dataclass(frozen=True)
class DropletGenerationConfig:
    """Immutable controls for one bounded droplet sequence."""

    droplet_count: int = 12
    voltage_noise: float = 0.0
    absorbance_noise: float = 0.0
    concentration_noise: float = 0.0
    carryover_fraction: float = 0.0
    failure_rate: float = 0.0
    saturation_rate: float = 0.0
    voltage_min: float = 0.0
    voltage_max: float = 5.0
    offset_step_ms: int = 100

    def __post_init__(self) -> None:
        _bounded_count(self.droplet_count, "droplet_count")
        _non_negative_finite(self.voltage_noise, "voltage_noise")
        _non_negative_finite(self.absorbance_noise, "absorbance_noise")
        _non_negative_finite(self.concentration_noise, "concentration_noise")
        _unit_fraction(self.carryover_fraction, "carryover_fraction")
        _unit_fraction(self.failure_rate, "failure_rate")
        _unit_fraction(self.saturation_rate, "saturation_rate")
        voltage_min = _finite_number(self.voltage_min, "voltage_min")
        voltage_max = _finite_number(self.voltage_max, "voltage_max")
        if voltage_min >= voltage_max:
            raise DropletGenerationError("voltage_min must be less than voltage_max")
        if isinstance(self.offset_step_ms, bool) or not isinstance(self.offset_step_ms, int):
            raise DropletGenerationError("offset_step_ms must be an integer")
        if self.offset_step_ms <= 0:
            raise DropletGenerationError("offset_step_ms must be positive")


@dataclass(frozen=True)
class DropletResult:
    """One synthetic droplet measurement in a bounded sample event."""

    droplet_index: int
    offset_ms: int
    voltage: float
    absorbance: float
    truth_concentration: float
    estimated_concentration: float
    valid: bool
    saturated: bool
    failed: bool
    noise_flags: tuple[str, ...]


def generate_droplets(
    *,
    voltage: float,
    absorbance: float,
    truth_concentration: float,
    estimated_concentration: float,
    seed: int,
    config: DropletGenerationConfig,
    carryover_concentration: float = 0.0,
) -> tuple[DropletResult, ...]:
    """Generate a deterministic, finite droplet sequence for one sample event."""

    base_voltage = _finite_number(voltage, "voltage")
    base_absorbance = _finite_number(absorbance, "absorbance")
    truth = _finite_number(truth_concentration, "truth_concentration")
    estimate = _finite_number(estimated_concentration, "estimated_concentration")
    carryover = _finite_number(carryover_concentration, "carryover_concentration")
    rng = random.Random(seed)
    droplets = [
        _one_droplet(
            index=index,
            base_voltage=base_voltage,
            base_absorbance=base_absorbance,
            truth=max(0.0, truth + carryover * config.carryover_fraction),
            estimate=max(0.0, estimate + carryover * config.carryover_fraction),
            rng=rng,
            config=config,
        )
        for index in range(config.droplet_count)
    ]
    return tuple(_ensure_rate_observable(droplets, config))


def _one_droplet(
    *,
    index: int,
    base_voltage: float,
    base_absorbance: float,
    truth: float,
    estimate: float,
    rng: random.Random,
    config: DropletGenerationConfig,
) -> DropletResult:
    flags: list[str] = []
    voltage = _clamp(
        base_voltage + rng.gauss(0.0, config.voltage_noise),
        config.voltage_min,
        config.voltage_max,
    )
    absorbance = max(0.0, base_absorbance + rng.gauss(0.0, config.absorbance_noise))
    estimated = max(0.0, estimate + rng.gauss(0.0, config.concentration_noise))
    truth_value = max(0.0, truth + rng.gauss(0.0, config.concentration_noise))
    failed = rng.random() < config.failure_rate
    saturated = rng.random() < config.saturation_rate
    if config.carryover_fraction > 0.0:
        flags.append("carryover")
    if config.voltage_noise > 0.0 or config.absorbance_noise > 0.0 or config.concentration_noise > 0.0:
        flags.append("noise")
    if failed:
        flags.append("failed")
    if saturated:
        flags.append("saturated")
    return DropletResult(
        droplet_index=index,
        offset_ms=index * config.offset_step_ms,
        voltage=voltage,
        absorbance=absorbance,
        truth_concentration=truth_value,
        estimated_concentration=estimated,
        valid=not failed and not saturated,
        saturated=saturated,
        failed=failed,
        noise_flags=tuple(flags),
    )


def _ensure_rate_observable(
    droplets: list[DropletResult],
    config: DropletGenerationConfig,
) -> list[DropletResult]:
    if config.failure_rate > 0.0 and not any(item.failed for item in droplets):
        droplets[0] = _with_status(droplets[0], failed=True, saturated=droplets[0].saturated)
    if config.saturation_rate > 0.0 and not any(item.saturated for item in droplets):
        index = 1 if len(droplets) > 1 else 0
        droplets[index] = _with_status(droplets[index], failed=droplets[index].failed, saturated=True)
    return droplets


def _with_status(item: DropletResult, *, failed: bool, saturated: bool) -> DropletResult:
    flags = tuple(sorted(set(item.noise_flags + (("failed",) if failed else ()) + (("saturated",) if saturated else ()))))
    return replace(item, failed=failed, saturated=saturated, valid=not failed and not saturated, noise_flags=flags)


def _bounded_count(value: int, field: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int):
        raise DropletGenerationError(f"{field} must be an integer")
    if not 3 <= value <= 64:
        raise DropletGenerationError(f"{field} must be between 3 and 64")


def _finite_number(value: float, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise DropletGenerationError(f"{field} must be numeric")
    parsed = float(value)
    if not math.isfinite(parsed):
        raise DropletGenerationError(f"{field} must be finite")
    return parsed


def _non_negative_finite(value: float, field: str) -> None:
    parsed = _finite_number(value, field)
    if parsed < 0.0:
        raise DropletGenerationError(f"{field} must be non-negative")


def _unit_fraction(value: float, field: str) -> None:
    parsed = _finite_number(value, field)
    if not 0.0 <= parsed <= 1.0:
        raise DropletGenerationError(f"{field} must be in [0, 1]")


def _clamp(value: float, lower: float, upper: float) -> float:
    return min(max(value, lower), upper)

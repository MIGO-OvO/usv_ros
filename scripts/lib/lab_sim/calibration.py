"""Pure Beer-Lambert and linear work-curve calibration primitives."""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import Enum
from typing import Protocol


class CalibrationErrorCode(str, Enum):
    """Machine-readable calibration failure categories."""

    MALFORMED_NUMBER = "malformed_number"
    NON_FINITE_NUMBER = "non_finite_number"
    NON_FINITE_RESULT = "non_finite_result"
    NON_POSITIVE_REFERENCE_SIGNAL = "non_positive_reference_signal"
    NON_POSITIVE_SAMPLE_SIGNAL = "non_positive_sample_signal"
    INVALID_SATURATION_THRESHOLD = "invalid_saturation_threshold"
    ZERO_WORK_CURVE_SLOPE = "zero_work_curve_slope"
    ZERO_LEGACY_SLOPE = "zero_legacy_slope"


@dataclass(frozen=True)
class CalibrationError:
    """Structured detail for an invalid calibration operation."""

    __slots__ = ("code", "field", "supplied")

    code: CalibrationErrorCode
    field: str
    supplied: str


@dataclass(frozen=True)
class CalibrationValue:
    """A valid scalar produced by a calibration transform."""

    __slots__ = ("value",)

    value: float


@dataclass(frozen=True)
class CalibrationInvalid:
    """An expected invalid outcome with a typed error."""

    __slots__ = ("error",)

    error: CalibrationError


@dataclass(frozen=True)
class CalibrationSaturated:
    """A finite transform that falls at or below the detector floor."""

    __slots__ = ("value", "threshold_voltage")

    value: float
    threshold_voltage: float


@dataclass(frozen=True)
class BeerLambertConfig:
    """Immutable optical voltages for dark-corrected Beer-Lambert transforms."""

    __slots__ = ("dark_voltage", "reference_voltage", "saturation_voltage")

    dark_voltage: float
    reference_voltage: float
    saturation_voltage: float | None


class CanonicalWorkCurve(Protocol):
    """Capability for conversion to the canonical A = k*C + b form."""

    def to_canonical(self) -> WorkCurveConfig | CalibrationInvalid:
        """Return the canonical work curve or a typed invalid outcome."""


@dataclass(frozen=True)
class WorkCurveConfig:
    """Immutable canonical linear work curve A = k*C + b."""

    __slots__ = ("k", "b")

    k: float
    b: float

    def to_canonical(self) -> WorkCurveConfig:
        return self


@dataclass(frozen=True)
class LegacyWorkCurveConfig:
    """Immutable legacy linear work curve C = m*A + b."""

    __slots__ = ("m", "b")

    m: float
    b: float

    def to_canonical(self) -> WorkCurveConfig | CalibrationInvalid:
        slope, error = _finite_number(self.m, "m")
        if error is not None:
            return error
        intercept, error = _finite_number(self.b, "b")
        if error is not None:
            return error
        if slope == 0.0:
            return _invalid(
                CalibrationErrorCode.ZERO_LEGACY_SLOPE,
                "m",
                self.m,
            )
        return WorkCurveConfig(k=1.0 / slope, b=-intercept / slope)


def _invalid(
    code: CalibrationErrorCode,
    field: str,
    supplied: int | float | str | None,
) -> CalibrationInvalid:
    return CalibrationInvalid(
        error=CalibrationError(code=code, field=field, supplied=repr(supplied)),
    )


def _finite_number(
    value: int | float | str | None,
    field: str,
) -> tuple[float, None] | tuple[None, CalibrationInvalid]:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None, _invalid(CalibrationErrorCode.MALFORMED_NUMBER, field, value)
    if not math.isfinite(parsed):
        return None, _invalid(CalibrationErrorCode.NON_FINITE_NUMBER, field, value)
    return parsed, None


def _optical_values(
    config: BeerLambertConfig,
) -> (
    tuple[float, float, float | None, None]
    | tuple[None, None, None, CalibrationInvalid]
):
    dark, error = _finite_number(config.dark_voltage, "dark_voltage")
    if error is not None:
        return None, None, None, error
    reference, error = _finite_number(config.reference_voltage, "reference_voltage")
    if error is not None:
        return None, None, None, error
    if reference - dark <= 0.0:
        return None, None, None, _invalid(
            CalibrationErrorCode.NON_POSITIVE_REFERENCE_SIGNAL,
            "reference_voltage",
            config.reference_voltage,
        )
    if config.saturation_voltage is None:
        return dark, reference, None, None
    saturation, error = _finite_number(
        config.saturation_voltage,
        "saturation_voltage",
    )
    if error is not None:
        return None, None, None, error
    if saturation <= dark or saturation >= reference:
        return None, None, None, _invalid(
            CalibrationErrorCode.INVALID_SATURATION_THRESHOLD,
            "saturation_voltage",
            config.saturation_voltage,
        )
    return dark, reference, saturation, None


def absorbance_from_concentration(
    concentration: int | float | str | None,
    config: WorkCurveConfig,
) -> CalibrationValue | CalibrationInvalid:
    """Apply the canonical forward work curve A = k*C + b."""

    concentration_value, error = _finite_number(concentration, "concentration")
    if error is not None:
        return error
    slope, error = _finite_number(config.k, "k")
    if error is not None:
        return error
    intercept, error = _finite_number(config.b, "b")
    if error is not None:
        return error
    absorbance = slope * concentration_value + intercept
    if not math.isfinite(absorbance):
        return _invalid(
            CalibrationErrorCode.NON_FINITE_RESULT,
            "absorbance",
            absorbance,
        )
    return CalibrationValue(value=absorbance)


def concentration_from_absorbance(
    absorbance: int | float | str | None,
    config: WorkCurveConfig,
) -> CalibrationValue | CalibrationInvalid:
    """Apply the canonical inverse work curve C = (A-b)/k."""

    absorbance_value, error = _finite_number(absorbance, "absorbance")
    if error is not None:
        return error
    slope, error = _finite_number(config.k, "k")
    if error is not None:
        return error
    intercept, error = _finite_number(config.b, "b")
    if error is not None:
        return error
    if slope == 0.0:
        return _invalid(CalibrationErrorCode.ZERO_WORK_CURVE_SLOPE, "k", config.k)
    concentration = (absorbance_value - intercept) / slope
    if not math.isfinite(concentration):
        return _invalid(
            CalibrationErrorCode.NON_FINITE_RESULT,
            "concentration",
            concentration,
        )
    return CalibrationValue(value=concentration)


def absorbance_from_voltage(
    sample_voltage: int | float | str | None,
    config: BeerLambertConfig,
) -> CalibrationValue | CalibrationInvalid | CalibrationSaturated:
    """Calculate A = log10((Vref-Vdark)/(Vsample-Vdark))."""

    sample, error = _finite_number(sample_voltage, "sample_voltage")
    if error is not None:
        return error
    dark, reference, saturation, error = _optical_values(config)
    if error is not None:
        return error
    sample_signal = sample - dark
    if sample_signal <= 0.0:
        return _invalid(
            CalibrationErrorCode.NON_POSITIVE_SAMPLE_SIGNAL,
            "sample_voltage",
            sample_voltage,
        )
    absorbance = math.log10((reference - dark) / sample_signal)
    if saturation is not None and sample <= saturation:
        return CalibrationSaturated(
            value=absorbance,
            threshold_voltage=saturation,
        )
    return CalibrationValue(value=absorbance)


def voltage_from_absorbance(
    absorbance: int | float | str | None,
    config: BeerLambertConfig,
) -> CalibrationValue | CalibrationInvalid | CalibrationSaturated:
    """Reconstruct optical voltage from dark-corrected absorbance."""

    absorbance_value, error = _finite_number(absorbance, "absorbance")
    if error is not None:
        return error
    dark, reference, saturation, error = _optical_values(config)
    if error is not None:
        return error
    try:
        sample = dark + (reference - dark) * math.pow(10.0, -absorbance_value)
    except OverflowError:
        return _invalid(
            CalibrationErrorCode.NON_FINITE_RESULT,
            "sample_voltage",
            absorbance_value,
        )
    if not math.isfinite(sample):
        return _invalid(
            CalibrationErrorCode.NON_FINITE_RESULT,
            "sample_voltage",
            sample,
        )
    threshold = dark if saturation is None else saturation
    if sample <= threshold:
        return CalibrationSaturated(value=sample, threshold_voltage=threshold)
    return CalibrationValue(value=sample)


def migrate_legacy_work_curve(
    config: CanonicalWorkCurve,
) -> WorkCurveConfig | CalibrationInvalid:
    """Migrate legacy C=m*A+b once; canonical inputs are returned unchanged."""

    return config.to_canonical()

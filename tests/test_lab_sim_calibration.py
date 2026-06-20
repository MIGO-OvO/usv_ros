import math
import unittest

from scripts.lib.lab_sim.calibration import (
    BeerLambertConfig,
    CalibrationErrorCode,
    CalibrationInvalid,
    CalibrationSaturated,
    CalibrationValue,
    LegacyWorkCurveConfig,
    WorkCurveConfig,
    absorbance_from_concentration,
    absorbance_from_voltage,
    concentration_from_absorbance,
    migrate_legacy_work_curve,
    voltage_from_absorbance,
)


class LabSimCalibrationTests(unittest.TestCase):
    def test_work_curve_round_trip_reconstructs_concentration(self):
        # Given
        curve = WorkCurveConfig(k=0.25, b=0.04)
        concentration = 7.2

        # When
        absorbance = absorbance_from_concentration(concentration, curve)
        self.assertIsInstance(absorbance, CalibrationValue)
        reconstructed = concentration_from_absorbance(absorbance.value, curve)

        # Then
        self.assertIsInstance(reconstructed, CalibrationValue)
        self.assertAlmostEqual(reconstructed.value, concentration, places=12)

    def test_dark_corrected_absorbance_round_trip_reconstructs_voltage(self):
        # Given
        optical = BeerLambertConfig(
            dark_voltage=0.1,
            reference_voltage=2.1,
            saturation_voltage=0.11,
        )
        sample_voltage = 0.6

        # When
        absorbance = absorbance_from_voltage(sample_voltage, optical)
        self.assertIsInstance(absorbance, CalibrationValue)
        reconstructed = voltage_from_absorbance(absorbance.value, optical)

        # Then
        self.assertIsInstance(reconstructed, CalibrationValue)
        self.assertAlmostEqual(absorbance.value, math.log10(4.0), places=12)
        self.assertAlmostEqual(reconstructed.value, sample_voltage, places=12)

    def test_zero_or_negative_sample_denominator_is_invalid(self):
        # Given
        optical = BeerLambertConfig(
            dark_voltage=0.2,
            reference_voltage=2.2,
            saturation_voltage=0.21,
        )

        # When
        zero = absorbance_from_voltage(0.2, optical)
        negative = absorbance_from_voltage(0.1, optical)

        # Then
        self.assertEqual(
            [zero.error.code, negative.error.code],
            [
                CalibrationErrorCode.NON_POSITIVE_SAMPLE_SIGNAL,
                CalibrationErrorCode.NON_POSITIVE_SAMPLE_SIGNAL,
            ],
        )

    def test_zero_reference_denominator_is_invalid(self):
        # Given
        optical = BeerLambertConfig(
            dark_voltage=0.2,
            reference_voltage=0.2,
            saturation_voltage=None,
        )

        # When
        result = absorbance_from_voltage(0.5, optical)

        # Then
        self.assertIsInstance(result, CalibrationInvalid)
        self.assertEqual(
            result.error.code,
            CalibrationErrorCode.NON_POSITIVE_REFERENCE_SIGNAL,
        )

    def test_zero_work_curve_slope_is_invalid_for_inverse(self):
        # Given
        curve = WorkCurveConfig(k=0.0, b=0.1)

        # When
        result = concentration_from_absorbance(0.5, curve)

        # Then
        self.assertIsInstance(result, CalibrationInvalid)
        self.assertEqual(result.error.code, CalibrationErrorCode.ZERO_WORK_CURVE_SLOPE)

    def test_detector_floor_returns_saturation_outcome(self):
        # Given
        optical = BeerLambertConfig(
            dark_voltage=0.1,
            reference_voltage=2.1,
            saturation_voltage=0.2,
        )

        # When
        result = absorbance_from_voltage(0.15, optical)

        # Then
        self.assertIsInstance(result, CalibrationSaturated)
        self.assertGreater(result.value, 0.0)
        self.assertEqual(result.threshold_voltage, 0.2)

    def test_legacy_curve_migration_preserves_legacy_values(self):
        # Given
        legacy = LegacyWorkCurveConfig(m=4.0, b=1.5)
        absorbance = 0.7
        legacy_concentration = legacy.m * absorbance + legacy.b

        # When
        migrated = migrate_legacy_work_curve(legacy)
        self.assertIsInstance(migrated, WorkCurveConfig)
        reconstructed = concentration_from_absorbance(absorbance, migrated)

        # Then
        self.assertIsInstance(reconstructed, CalibrationValue)
        self.assertAlmostEqual(reconstructed.value, legacy_concentration, places=12)
        self.assertAlmostEqual(migrated.k, 0.25, places=12)
        self.assertAlmostEqual(migrated.b, -0.375, places=12)

    def test_migration_is_idempotent_for_canonical_curve(self):
        # Given
        canonical = WorkCurveConfig(k=0.25, b=-0.375)

        # When
        migrated_once = migrate_legacy_work_curve(canonical)
        migrated_twice = migrate_legacy_work_curve(migrated_once)

        # Then
        self.assertIs(migrated_once, canonical)
        self.assertIs(migrated_twice, canonical)

    def test_malformed_and_non_finite_inputs_are_typed_invalid(self):
        # Given
        curve = WorkCurveConfig(k=0.25, b=0.04)

        # When
        malformed = absorbance_from_concentration("bad", curve)
        non_finite = absorbance_from_concentration(float("inf"), curve)

        # Then
        self.assertEqual(malformed.error.code, CalibrationErrorCode.MALFORMED_NUMBER)
        self.assertEqual(non_finite.error.code, CalibrationErrorCode.NON_FINITE_NUMBER)

    def test_zero_legacy_slope_is_typed_invalid(self):
        # Given
        legacy = LegacyWorkCurveConfig(m=0.0, b=2.0)

        # When
        result = migrate_legacy_work_curve(legacy)

        # Then
        self.assertIsInstance(result, CalibrationInvalid)
        self.assertEqual(result.error.code, CalibrationErrorCode.ZERO_LEGACY_SLOPE)


if __name__ == "__main__":
    unittest.main()

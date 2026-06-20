import math
import unittest

from scripts.lib.lab_sim.aggregation import aggregate_droplets
from scripts.lib.lab_sim.droplet_signal import (
    DropletGenerationConfig,
    DropletResult,
    generate_droplets,
)


class DropletGenerationTests(unittest.TestCase):
    def test_fixed_seed_is_reproducible(self):
        # Given
        config = DropletGenerationConfig(
            voltage_noise=0.05,
            absorbance_noise=0.02,
            concentration_noise=0.1,
            carryover_fraction=0.25,
            failure_rate=0.15,
            saturation_rate=0.1,
        )

        # When
        first = generate_droplets(
            voltage=2.4,
            absorbance=0.3,
            truth_concentration=4.0,
            estimated_concentration=3.8,
            seed=20260618,
            config=config,
            carryover_concentration=1.0,
        )
        second = generate_droplets(
            voltage=2.4,
            absorbance=0.3,
            truth_concentration=4.0,
            estimated_concentration=3.8,
            seed=20260618,
            config=config,
            carryover_concentration=1.0,
        )

        # Then
        self.assertEqual(first, second)

    def test_default_generation_has_twelve_droplets(self):
        # Given
        config = DropletGenerationConfig()

        # When
        droplets = generate_droplets(
            voltage=2.0,
            absorbance=0.2,
            truth_concentration=3.0,
            estimated_concentration=2.9,
            seed=7,
            config=config,
        )

        # Then
        self.assertEqual(len(droplets), 12)

    def test_count_outside_three_to_sixty_four_is_rejected(self):
        # Given / When / Then
        for count in (2, 65):
            with self.subTest(count=count):
                with self.assertRaises(ValueError):
                    DropletGenerationConfig(droplet_count=count)

    def test_malformed_or_non_finite_config_is_rejected(self):
        # Given / When / Then
        invalid_configs = (
            {"droplet_count": True},
            {"droplet_count": 12.0},
            {"voltage_noise": -0.01},
            {"voltage_noise": math.nan},
            {"carryover_fraction": 1.01},
            {"failure_rate": -0.01},
            {"saturation_rate": math.inf},
        )
        for values in invalid_configs:
            with self.subTest(values=values):
                with self.assertRaises((TypeError, ValueError)):
                    DropletGenerationConfig(**values)

    def test_noise_and_flags_remain_bounded(self):
        # Given
        config = DropletGenerationConfig(
            droplet_count=64,
            voltage_noise=0.2,
            absorbance_noise=0.1,
            concentration_noise=0.3,
            carryover_fraction=0.5,
            failure_rate=0.25,
            saturation_rate=0.25,
            voltage_min=0.0,
            voltage_max=3.3,
        )

        # When
        droplets = generate_droplets(
            voltage=3.2,
            absorbance=0.4,
            truth_concentration=5.0,
            estimated_concentration=4.8,
            seed=19,
            config=config,
            carryover_concentration=1.0,
        )

        # Then
        self.assertTrue(all(0.0 <= item.voltage <= 3.3 for item in droplets))
        self.assertTrue(all(item.absorbance >= 0.0 for item in droplets))
        self.assertTrue(all(item.estimated_concentration >= 0.0 for item in droplets))
        self.assertTrue(any(item.failed for item in droplets))
        self.assertTrue(any(item.saturated for item in droplets))
        self.assertTrue(all(item.valid == (not item.failed and not item.saturated) for item in droplets))

    def test_non_finite_signal_input_is_rejected(self):
        # Given
        config = DropletGenerationConfig()

        # When / Then
        with self.assertRaises(ValueError):
            generate_droplets(
                voltage=math.nan,
                absorbance=0.2,
                truth_concentration=3.0,
                estimated_concentration=2.9,
                seed=1,
                config=config,
            )


class DropletAggregationTests(unittest.TestCase):
    @staticmethod
    def _droplet(index, value, *, valid=True):
        return DropletResult(
            droplet_index=index,
            offset_ms=index * 100,
            voltage=value,
            absorbance=value,
            truth_concentration=value,
            estimated_concentration=value,
            valid=valid,
            saturated=False,
            failed=not valid,
            noise_flags=(),
        )

    def test_twelve_droplets_produce_one_aggregate_and_one_map_sample(self):
        # Given
        droplets = tuple(self._droplet(index, float(index + 1)) for index in range(12))

        # When
        result = aggregate_droplets(droplets)

        # Then
        self.assertEqual(result.summary.total_count, 12)
        self.assertEqual(result.summary.valid_count, 12)
        self.assertFalse(hasattr(result, "map_points"))
        self.assertFalse(hasattr(result.map_sample, "droplets"))

    def test_mean_median_and_population_stddev_are_correct(self):
        # Given
        droplets = tuple(
            self._droplet(index, value)
            for index, value in enumerate((1.0, 2.0, 3.0, 4.0))
        )

        # When
        result = aggregate_droplets(droplets)

        # Then
        metric = result.summary.estimated_concentration
        self.assertAlmostEqual(metric.mean, 2.5)
        self.assertAlmostEqual(metric.median, 2.5)
        self.assertAlmostEqual(metric.stddev, math.sqrt(1.25))

    def test_insufficient_valid_droplets_make_event_invalid(self):
        # Given
        droplets = (
            self._droplet(0, 1.0, valid=True),
            self._droplet(1, 2.0, valid=True),
            self._droplet(2, 3.0, valid=False),
            self._droplet(3, 4.0, valid=False),
        )

        # When
        result = aggregate_droplets(droplets, minimum_valid=3)

        # Then
        self.assertFalse(result.summary.valid)
        self.assertEqual(result.summary.valid_count, 2)
        self.assertIn("insufficient_valid_droplets", result.summary.quality_flags)
        self.assertFalse(result.map_sample.valid)

    def test_non_finite_droplet_values_are_not_aggregated(self):
        # Given
        droplets = (
            self._droplet(0, 1.0),
            self._droplet(1, math.nan),
            self._droplet(2, 3.0),
        )

        # When
        result = aggregate_droplets(droplets, minimum_valid=3)

        # Then
        self.assertFalse(result.summary.valid)
        self.assertEqual(result.summary.valid_count, 2)


if __name__ == "__main__":
    unittest.main()

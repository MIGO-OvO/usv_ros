import unittest

from scripts.web_config_server import build_calibrated_zero_command


class CalibratedZeroCommandTests(unittest.TestCase):
    def test_builds_shortest_relative_pid_moves_to_calibrated_zero(self):
        command = build_calibrated_zero_command(
            {"X": 90.0, "Y": 270.0, "Z": 180.0, "A": 0.0},
            "XYZA",
        )

        self.assertEqual(
            command,
            "XEBR90.000P0.1YEFR90.000P0.1ZEBR180.000P0.1\r\n",
        )

    def test_skips_axes_already_within_zero_tolerance(self):
        command = build_calibrated_zero_command(
            {"X": 0.05, "Y": 359.95},
            "XY",
            precision=0.1,
        )

        self.assertEqual(command, "")


if __name__ == "__main__":
    unittest.main()

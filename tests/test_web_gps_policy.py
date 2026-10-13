import unittest

from gps_fixtures import gps_position
from scripts.lib.sample_recording.record import bind_web_context, sample_record


class WebGPSPolicyTests(unittest.TestCase):
    def test_opt_out_still_binds_valid_hardware(self):
        context = bind_web_context({'source': 'web'}, gps_position(), require_gps=False)
        self.assertEqual(sample_record(context)['latitude'], 30)
        self.assertEqual(sample_record(context)['position_source'], 'gps')

    def test_missing_invalid_and_stale_are_explicit_unlocated_records(self):
        for position in (None, gps_position(age=3), dict(gps_position(), fix_status=-1)):
            with self.subTest(position=position):
                for require_gps in (False, True, 'true', None):
                    context = bind_web_context({}, position, require_gps=require_gps)
                    record = sample_record(context)
                    self.assertIsNone(record['latitude'])
                    self.assertIsNone(record['longitude'])
                    self.assertEqual(record['position_source'], 'web_no_gps')
                    self.assertFalse(record['simulated'])
                self.assertIsNone(sample_record(bind_web_context({}, position))['latitude'])

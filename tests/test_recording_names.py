"""Named acquisition through the production lifecycle, storage and export APIs."""
import copy
import csv
import io
import json
import os
import struct
import tempfile
import unittest
import zipfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch
from urllib.parse import unquote

from scripts.lib.sample_recording.naming import recording_name, recording_stem, sample_name_from_token
from scripts.web_config_server import ConfigManager, FLASK_AVAILABLE, MissionDataManager, String, WebConfigServer


def msg(value):
    result = String()
    result.data = value if isinstance(value, str) else json.dumps(value)
    return result


class RecordingNameTests(unittest.TestCase):
    def test_names_and_filenames_are_portable_and_preserve_unicode(self):
        self.assertEqual(recording_name('  湖心  '), '湖心')
        self.assertRegex(recording_stem('环湖任务', 1794816000.123456, 4), r'^环湖任务-4-20261116_')
        for invalid in ('../x', 'x/y', 'x\\y', 'x\nY', 'x?', 'x.', 'x' * 81, '湖' * 61, '😀' * 46):
            with self.subTest(name=invalid), self.assertRaises(ValueError):
                recording_name(invalid, required=True)
        for invalid in ('', ' ', 123):
            with self.subTest(name=invalid), self.assertRaises(ValueError):
                recording_name(invalid, required=True)


@unittest.skipUnless(FLASK_AVAILABLE, 'Flask unavailable')
class RecordingNameApiTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        with patch.dict(os.environ, {'USV_MISSION_DATA_DIR': self.tmp.name}):
            self.server = WebConfigServer(standalone=True)
        self.addCleanup(self.server.sample_storage.close)
        self.server.config_manager = ConfigManager(os.path.join(self.tmp.name, 'config', 'config.json'))
        self.client = self.server.app.test_client()

    def test_raw_csv_uses_each_sample_start_in_window_mission_and_zip(self):
        manager = self.server.data_manager
        storage = self.server.sample_storage
        manager.start_mission('湖心测绘')
        mission = manager.current_mission_data
        mission_id = mission['mission_id']
        starts = [
            {'lat': 25.1, 'lng': 110.2, 'position_source': 'gps'},
            {'position_source': 'web_no_gps'},
        ]
        for index, snapshot in enumerate(starts):
            window = storage.start_window(mission, {
                'record_id': 'record-%d' % index,
                'sample_name': '采样-%d' % index,
                'gps_snapshot': snapshot,
            })
            storage.append_raw_frame(window, {'voltage': 1.25, 'timestamp_ms': 42, 'valid': True})
            window['outcome'] = 'succeeded'
            storage.close_window(mission, window, {'lat': 26.3, 'lng': 111.4})
        manager.stop_mission()

        base = '/api/data/mission/' + mission_id
        task = self.client.get(base + '/raw.csv')
        self.assertEqual(task.status_code, 200)
        rows = list(csv.DictReader(io.StringIO(task.get_data(as_text=True))))
        self.assertEqual([row['sample_id'] for row in rows], ['record-0', 'record-1'])
        self.assertEqual([(row['wgs84_lat'], row['wgs84_lng']) for row in rows], [('25.1', '110.2'), ('', '')])
        self.assertEqual([row['position_source'] for row in rows], ['gps', 'web_no_gps'])
        self.assertEqual(rows[0]['mission_id'], mission_id)
        self.assertEqual(rows[0]['sample_name'], '采样-0')
        self.assertEqual(rows[0]['sample_started_at'], mission['sample_windows'][0]['start_time'])
        self.assertEqual(rows[0]['sample_outcome'], 'succeeded')
        self.assertEqual(rows[0]['timestamp_ms'], '42')

        raw = self.client.get(base + '/sample/record-0/raw.csv')
        self.assertEqual(raw.status_code, 200)
        self.assertEqual(list(csv.DictReader(io.StringIO(raw.get_data(as_text=True)))), rows[:1])
        archive = self.client.get(base + '/archive')
        self.addCleanup(archive.close)
        self.assertEqual(archive.status_code, 200)
        with zipfile.ZipFile(io.BytesIO(archive.get_data())) as zipped:
            stacked = next(name for name in zipped.namelist() if name.endswith('/raw.csv'))
            single = next(name for name in zipped.namelist() if name.endswith(
                '/raw_csv/' + mission['sample_windows'][0]['file_stem'] + '.csv'))
            self.assertEqual(zipped.read(stacked), task.get_data())
            self.assertEqual(zipped.read(single), raw.get_data())

    def test_prepared_tokens_are_immutable_float32_exact_and_persisted(self):
        def prepare(index):
            with self.server.app.test_client() as client:
                response = client.post('/api/recording/name', json={'name': '采样-%d' % index})
                self.assertEqual(response.status_code, 200)
                return response.get_json()['data']
        with ThreadPoolExecutor(max_workers=4) as pool:
            prepared = list(pool.map(prepare, range(8)))
        loaded = ConfigManager(self.server.config_manager.config_file)
        self.assertTrue(loaded.load())
        config = loaded.get()
        self.assertEqual(len({data['token'] for data in prepared}), 8)
        for data in prepared:
            token = data['token']
            self.assertGreater(token, 0)
            self.assertEqual(struct.unpack('f', struct.pack('f', token))[0], token)
            self.assertEqual(sample_name_from_token(config, token), data['name'])
        self.assertEqual(sample_name_from_token(config, 0), '')
        for token in (-1, 2 ** 24, .5, float('nan'), float('inf'), 'unknown'):
            with self.subTest(token=token), self.assertRaises((ValueError, TypeError)):
                sample_name_from_token(config, token)

    def test_name_cache_eviction_and_failed_save_do_not_return_start_token(self):
        first = None
        for index in range(65):
            data = self.client.post('/api/recording/name', json={'name': str(index)}).get_json()['data']
            first = first or data['token']
        config = copy.deepcopy(self.server.config_manager.get())
        self.assertEqual(len(config['recording']['sample_names']), 64)
        with self.assertRaises(ValueError):
            sample_name_from_token(config, first)
        with patch.object(self.server.config_manager, '_save_locked', return_value=False):
            response = self.client.post('/api/recording/name', json={'name': '不能保存'})
        self.assertEqual(response.status_code, 500)
        self.assertNotIn('data', response.get_json())
        self.assertEqual(self.server.config_manager.get(), config)
        for body in ({'recording': []}, {'recording': {'sample_names': []}}, {'recording': {'mission_name': '../bad'}}):
            self.assertEqual(self.client.post('/api/config', json=body).status_code, 400)

    def test_manual_web_start_pins_name_and_exports_all_named_files(self):
        self.server.standalone = False
        with patch.object(self.server, '_publish_steps', return_value={'attempt_id': 'named-web'}), \
                patch.object(self.server, '_call_control_command', return_value=(True, 'accepted', {})) as control:
            invalid = self.client.post('/api/mission/start', json={'sample_name': '../bad'})
            self.assertEqual(invalid.status_code, 400)
            control.assert_not_called()
            response = self.client.post('/api/mission/start', json={'sample_name': '湖心'})
        self.assertTrue(response.get_json()['success'])
        self.assertEqual(control.call_args.args[1]['sample_name'], '湖心')
        context = dict(self.server._sampling_context)
        for index in range(3):
            self.server._spectrometer_raw_cb(msg({'timestamp_ms': index, 'voltage': 1.2, 'valid': True}))
        self.server._automation_status_cb(msg({'status': 'finished', 'running': False, 'sampling_context': context}))
        mission_id = self.server.data_manager.list_missions()[0]['id']
        self.check_exports(mission_id, '湖心', None)

    def test_waypoints_keep_task_name_and_sequence_through_status_recovery_and_restart(self):
        named = self.client.post('/api/recording/mission', json={'name': '环湖任务'})
        self.assertEqual(named.status_code, 200)
        ids = []
        for seq in (3, 7):
            context = {'source': 'fcu', 'waypoint_seq': seq, 'sample_id': 20 + seq,
                       'record_id': 'record-%d' % seq, 'timestamp_start': 1794816000 + seq,
                       'mission_name': '环湖任务', 'attempt_id': 'attempt-%d' % seq}
            # Latched pump status must recover the name when the one-shot start was lost.
            self.server._automation_status_cb(msg({'running': True, 'sampling_context': context}))
            mission_id = self.server.data_manager.current_mission_data['mission_id']
            ids.append(mission_id)
            self.server._spectrometer_raw_cb(msg({'timestamp_ms': seq, 'voltage': 1.3, 'valid': True}))
            self.server._sampling_result_cb(msg({'attempt_id': context['attempt_id'], 'source': 'fcu',
                'outcome': 'succeeded', 'reason': 'completed', 'sample_record': {}}))
            self.check_exports(mission_id, '环湖任务', seq)
        restarted = MissionDataManager(self.tmp.name)
        self.assertEqual(len(restarted.list_missions()), 2)
        for mission_id in ids:
            mission = restarted.get_mission(mission_id)
            mission['sample_windows'][0]['manual_result']['analyte'] = 'COD'
            self.assertTrue(restarted.save_mission(mission_id, mission))
            self.assertEqual(MissionDataManager(self.tmp.name).get_mission(mission_id)['sample_windows'][0]['manual_result']['analyte'], 'COD')
            self.assertTrue(restarted.delete_mission(mission_id))
            self.assertIsNone(restarted.get_mission(mission_id))
        # Existing mission_ files are still listed/read alongside the new naming scheme.
        restarted.start_mission('legacy')
        legacy = restarted.current_mission_data['mission_id']
        restarted.stop_mission()
        self.assertIsNotNone(MissionDataManager(self.tmp.name).get_mission(legacy))

    def test_repeated_names_use_distinct_start_timestamps_and_never_overwrite(self):
        manager = self.server.data_manager
        stems = [recording_stem('湖心', 1794816000 + index / 1000) for index in range(2)]
        for stem in stems:
            manager.start_mission('湖心', file_stem=stem)
            manager.stop_mission()
        self.assertEqual(len(manager.list_missions()), 2)
        with self.assertRaises(FileExistsError):
            manager.start_mission('湖心', file_stem=stems[0])
        self.assertTrue(all((Path(self.tmp.name) / (stem + '.json')).is_file() for stem in stems))

    def check_exports(self, mission_id, name, seq):
        data = self.server.data_manager.get_mission(mission_id)
        window = data['sample_windows'][0]
        stem = recording_stem(name, window['timestamp_start'], seq)
        self.assertEqual(data['file_stem'], stem)
        self.assertEqual(window['file_stem'], stem)
        self.assertTrue((Path(self.tmp.name) / (stem + '.json')).is_file())
        self.assertEqual(Path(window['spectrometer']['raw_file']).name, stem + '.jsonl')
        sample_id = window['sample_id']
        for suffix, extension in (('csv', '.csv'), ('raw.csv', '.csv'), ('sample/%s/raw.csv' % sample_id, '.csv'), ('archive', '.zip')):
            response = self.client.get('/api/data/mission/%s/%s' % (mission_id, suffix))
            self.assertEqual(response.status_code, 200)
            self.assertEqual(unquote(response.headers['Content-Disposition'].split("filename*=UTF-8''")[1]), stem + extension)
            if extension == '.zip':
                with zipfile.ZipFile(io.BytesIO(response.get_data())) as archive:
                    self.assertIn(stem + '/raw/' + stem + '.jsonl', archive.namelist())
                    self.assertIn(stem + '/raw_csv/' + stem + '.csv', archive.namelist())
            else:
                self.assertGreaterEqual(len(response.get_data(as_text=True).splitlines()), 1 if suffix == 'csv' else 2)
            response.close()
        series = self.client.get('/api/data/voltage-series', query_string={'mission_id': mission_id, 'sample_id': sample_id}).get_json()
        self.assertGreater(series['data']['raw_count'], 0)


if __name__ == '__main__':
    unittest.main()

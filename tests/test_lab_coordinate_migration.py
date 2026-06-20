import copy
import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path

from scripts.lib.lab_sim.coordinates import CoordinatePair, parse_coordinate


REPO_ROOT = Path(__file__).resolve().parents[1]


def _load_web_config_server(module_name):
    script_path = REPO_ROOT / "scripts" / "web_config_server.py"
    spec = importlib.util.spec_from_file_location(module_name, script_path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


class LabCoordinateMigrationTests(unittest.TestCase):
    def _coordinate_pair(self, lat, lng):
        return CoordinatePair.from_gcj02(parse_coordinate(lat, lng)).as_dict()

    def test_legacy_bare_lab_coordinates_migrate_to_schema_v2_pairs(self):
        module = _load_web_config_server("web_config_server_lab_coordinate_migration_test")

        legacy = {
            "sim": {"start_lat": 25.314167, "start_lng": 110.412778},
            "mission": {
                "waypoints": [
                    {"lat": 25.314167, "lng": 110.412778},
                    {"lat": 25.315, "lng": 110.413},
                ]
            },
            "pollution": {"mode": "manual", "source": {"lat": 25.316, "lng": 110.414}},
            "water_area": {
                "enabled": True,
                "polygon": [
                    {"lat": 25.31, "lng": 110.41},
                    {"lat": 25.32, "lng": 110.41},
                    {"lat": 25.32, "lng": 110.42},
                ],
            },
        }

        migrated = module.normalize_lab_config(legacy)

        waypoint = migrated["mission"]["waypoints"][0]
        self.assertEqual(waypoint["coordinate_schema_version"], 2)
        self.assertIn("wgs84", waypoint)
        self.assertIn("gcj02", waypoint)
        self.assertAlmostEqual(waypoint["gcj02"]["lat"], 25.314167, places=9)
        self.assertAlmostEqual(waypoint["gcj02"]["lng"], 110.412778, places=9)
        self.assertNotAlmostEqual(waypoint["wgs84"]["lat"], waypoint["gcj02"]["lat"], places=6)
        self.assertIn("wgs84", migrated["sim"]["start"])
        self.assertIn("gcj02", migrated["pollution"]["source"])
        self.assertIn("wgs84", migrated["water_area"]["polygon"][0])

    def test_coordinate_migration_is_idempotent(self):
        module = _load_web_config_server("web_config_server_lab_coordinate_idempotence_test")
        legacy = {
            "sim": {"start_lat": 25.314167, "start_lng": 110.412778},
            "mission": {"waypoints": [{"lat": 25.314167, "lng": 110.412778}]},
            "pollution": {"mode": "manual", "source": {"lat": 25.316, "lng": 110.414}},
            "water_area": {
                "enabled": True,
                "polygon": [
                    {"lat": 25.31, "lng": 110.41},
                    {"lat": 25.32, "lng": 110.41},
                    {"lat": 25.32, "lng": 110.42},
                ],
            },
        }

        once = module.normalize_lab_config(legacy)
        twice = module.normalize_lab_config(copy.deepcopy(once))

        self.assertEqual(twice, once)

    def test_new_lab_config_write_rejects_coordinate_missing_crs(self):
        module = _load_web_config_server("web_config_server_lab_coordinate_missing_crs_test")
        with tempfile.TemporaryDirectory() as tmpdir:
            config_file = str(Path(tmpdir) / "sampling_config.json")

            class TempConfigManager(module.ConfigManager):
                def __init__(self, _config_file=config_file):
                    super().__init__(_config_file)

            module.ConfigManager = TempConfigManager
            server = module.WebConfigServer(standalone=True)
            client = server.app.test_client()

            response = client.post("/api/lab/mission", json={
                "waypoints": [{"wgs84": {"lat": 25.314167, "lng": 110.412778}}]
            })

        payload = response.get_json()
        self.assertEqual(response.status_code, 400)
        self.assertEqual(payload["error"]["code"], "missing_crs")

    def test_lab_mission_accepts_gcj02_labeled_click_input(self):
        module = _load_web_config_server("web_config_server_lab_gcj02_click_test")
        with tempfile.TemporaryDirectory() as tmpdir:
            config_file = str(Path(tmpdir) / "sampling_config.json")

            class TempConfigManager(module.ConfigManager):
                def __init__(self, _config_file=config_file):
                    super().__init__(_config_file)

            module.ConfigManager = TempConfigManager
            server = module.WebConfigServer(standalone=True)
            client = server.app.test_client()
            click = {"lat": 25.314167, "lng": 110.412778}

            response = client.post("/api/lab/mission", json={
                "waypoints": [{"input_crs": "GCJ02", "gcj02": click}]
            })

        payload = response.get_json()
        self.assertEqual(response.status_code, 200)
        waypoint = payload["data"]["waypoints"][0]
        self.assertEqual(waypoint["coordinate_schema_version"], 2)
        self.assertAlmostEqual(waypoint["gcj02"]["lat"], click["lat"], places=9)
        self.assertAlmostEqual(waypoint["gcj02"]["lng"], click["lng"], places=9)
        self.assertNotAlmostEqual(waypoint["wgs84"]["lat"], click["lat"], places=6)
        self.assertNotAlmostEqual(waypoint["wgs84"]["lng"], click["lng"], places=6)

    def test_default_lab_config_uses_schema_v2_start_pair(self):
        module = _load_web_config_server("web_config_server_lab_coordinate_default_test")

        default_lab = module.ConfigManager().get()["lab_mode"]

        self.assertEqual(default_lab["coordinate_schema_version"], 2)
        self.assertEqual(default_lab["sim"]["start"]["coordinate_schema_version"], 2)
        self.assertIn("wgs84", default_lab["sim"]["start"])
        self.assertIn("gcj02", default_lab["sim"]["start"])

    def test_loading_legacy_config_writes_schema_v2_once(self):
        module = _load_web_config_server("web_config_server_lab_coordinate_writeback_test")
        with tempfile.TemporaryDirectory() as tmpdir:
            config_file = Path(tmpdir) / "sampling_config.json"
            config_file.write_text(json.dumps({
                "lab_mode": {
                    "sim": {"start_lat": 25.314167, "start_lng": 110.412778},
                    "mission": {"waypoints": [{"lat": 25.314167, "lng": 110.412778}]},
                }
            }), encoding="utf-8")
            manager = module.ConfigManager(str(config_file))

            manager.load()
            first_write = json.loads(config_file.read_text(encoding="utf-8"))
            manager.load()
            second_write = json.loads(config_file.read_text(encoding="utf-8"))

        self.assertEqual(first_write, second_write)
        self.assertIn("wgs84", first_write["lab_mode"]["mission"]["waypoints"][0])


if __name__ == "__main__":
    unittest.main()

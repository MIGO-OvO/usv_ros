import importlib.util
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


def _coordinate_pair(lat, lng):
    return CoordinatePair.from_wgs84(parse_coordinate(lat, lng)).as_dict()


def _valid_polygon():
    return [
        _coordinate_pair(25.274000, 110.296000),
        _coordinate_pair(25.274000, 110.296800),
        _coordinate_pair(25.274360, 110.296800),
        _coordinate_pair(25.274360, 110.296000),
    ]


class LabAutoScanApiTests(unittest.TestCase):
    def _client(self, module_name):
        module = _load_web_config_server(module_name)
        tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(tempdir.cleanup)
        config_file = str(Path(tempdir.name) / "sampling_config.json")

        class TempConfigManager(module.ConfigManager):
            def __init__(self, _config_file=config_file):
                super().__init__(_config_file)

        module.ConfigManager = TempConfigManager
        server = module.WebConfigServer(standalone=True)
        return server.app.test_client(), server

    def test_generates_preview_route_with_dual_coordinates_and_snapshot_hash(self):
        # Given a schema-v2 WGS/GCJ water polygon and valid scan parameters.
        client, server = self._client("web_config_server_auto_scan_success_test")

        # When the route is generated in preview mode.
        response = client.post("/api/lab/route/auto-scan", json={
            "polygon": _valid_polygon(),
            "strip_spacing_m": 10.0,
            "heading_deg": 90.0,
            "inward_margin_m": 1.0,
            "max_waypoints": 200,
            "preview": True,
        })

        # Then the API returns a generated route without saving current mission config.
        payload = response.get_json()
        self.assertEqual(response.status_code, 200)
        self.assertTrue(payload["success"])
        self.assertFalse(payload["data"]["saved"])
        self.assertRegex(payload["data"]["water_snapshot_hash"], r"^[0-9a-f]{64}$")
        self.assertEqual(payload["data"]["waypoint_count"], len(payload["data"]["route_waypoints"]))
        self.assertGreaterEqual(payload["data"]["waypoint_count"], 2)
        first = payload["data"]["route_waypoints"][0]
        self.assertEqual(first["coordinate_schema_version"], 2)
        self.assertIn("wgs84", first)
        self.assertIn("gcj02", first)
        self.assertEqual(server.config_manager.get()["lab_mode"]["mission"]["waypoints"], [])

    def test_rejects_polygon_with_too_few_vertices(self):
        # Given a polygon with fewer than three vertices.
        client, _server = self._client("web_config_server_auto_scan_polygon_test")

        # When auto-scan is requested.
        response = client.post("/api/lab/route/auto-scan", json={
            "polygon": _valid_polygon()[:2],
            "strip_spacing_m": 10.0,
            "preview": True,
        })

        # Then a typed invalid_polygon 400 is returned.
        payload = response.get_json()
        self.assertEqual(response.status_code, 400)
        self.assertEqual(payload["error"]["code"], "invalid_polygon")

    def test_rejects_invalid_strip_parameters(self):
        # Given an illegal strip spacing.
        client, _server = self._client("web_config_server_auto_scan_strip_test")

        # When auto-scan is requested.
        response = client.post("/api/lab/route/auto-scan", json={
            "polygon": _valid_polygon(),
            "strip_spacing_m": 0.0,
            "heading_deg": 90.0,
            "preview": True,
        })

        # Then a typed invalid_parameters 400 is returned.
        payload = response.get_json()
        self.assertEqual(response.status_code, 400)
        self.assertEqual(payload["error"]["code"], "invalid_parameters")

    def test_rejects_route_exceeding_max_waypoints(self):
        # Given a waypoint budget too small for the generated scan route.
        client, _server = self._client("web_config_server_auto_scan_max_test")

        # When auto-scan is requested.
        response = client.post("/api/lab/route/auto-scan", json={
            "polygon": _valid_polygon(),
            "strip_spacing_m": 5.0,
            "heading_deg": 90.0,
            "max_waypoints": 3,
            "preview": True,
        })

        # Then the planner limit is surfaced as a typed 400.
        payload = response.get_json()
        self.assertEqual(response.status_code, 400)
        self.assertEqual(payload["error"]["code"], "max_waypoints_exceeded")


if __name__ == "__main__":
    unittest.main()

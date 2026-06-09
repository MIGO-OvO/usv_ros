import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
TESTS_DIR = Path(__file__).resolve().parent
if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))

import pollution_map_fixtures


def _load_script(module_name, relative_path):
    script_path = REPO_ROOT / relative_path
    spec = importlib.util.spec_from_file_location(module_name, script_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class PollutionMapFixtureTests(unittest.TestCase):
    def test_fixture_feeds_backend_geojson_and_surface(self):
        module = _load_script("web_config_server_pollution_map_fixture_test", "scripts/web_config_server.py")

        with tempfile.TemporaryDirectory() as tmpdir:
            fixture = pollution_map_fixtures.create_pollution_map_fixture(module, tmpdir)
            server = module.WebConfigServer(standalone=True)
            server.data_manager = module.MissionDataManager(fixture["missions_dir"])
            client = server.app.test_client()

            geojson_resp = client.get(
                f"/api/data/mission/{fixture['mission_id']}/geojson?metric=concentration"
            )
            surface_resp = client.get(
                f"/api/data/mission/{fixture['mission_id']}/surface?metric=concentration&size=4"
            )

        geojson = geojson_resp.get_json()["data"]
        surface = surface_resp.get_json()["data"]
        sample_features = [
            feature for feature in geojson["features"]
            if feature["properties"].get("layer") == "sample"
        ]

        self.assertEqual(geojson_resp.status_code, 200)
        self.assertEqual(surface_resp.status_code, 200)
        self.assertGreaterEqual(len(sample_features), 3)
        self.assertTrue(surface["valid"])
        self.assertEqual(surface["point_count"], 3)
        self.assertGreaterEqual(surface["excluded_count"], 1)
        self.assertEqual(surface["metric_label"], "COD")
        self.assertEqual(surface["unit"], "mg/L")

    def test_frontend_declares_runnable_map_smoke_script(self):
        package_path = REPO_ROOT / "frontend" / "package.json"
        package = json.loads(package_path.read_text(encoding="utf-8"))
        script_path = REPO_ROOT / "frontend" / "scripts" / "smoke-map.mjs"

        self.assertEqual(package["scripts"]["smoke:map"], "node scripts/smoke-map.mjs")
        self.assertTrue(script_path.exists())

        env = dict(os.environ)
        env["NO_COLOR"] = "1"
        npm = shutil.which("npm") or shutil.which("npm.cmd")
        self.assertIsNotNone(npm)
        result = subprocess.run(
            [npm, "run", "smoke:map"],
            cwd=str(REPO_ROOT / "frontend"),
            env=env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=30,
        )

        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertIn("pollution map smoke ok", result.stdout)

    def test_frontend_declares_browser_map_smoke_script(self):
        package_path = REPO_ROOT / "frontend" / "package.json"
        package = json.loads(package_path.read_text(encoding="utf-8"))
        browser_script_path = REPO_ROOT / "frontend" / "scripts" / "smoke-map-browser.mjs"
        browser_script = browser_script_path.read_text(encoding="utf-8")

        self.assertEqual(
            package["scripts"]["smoke:map:browser"],
            "node scripts/smoke-map-browser.mjs",
        )
        self.assertTrue(browser_script_path.exists())
        self.assertIn("task-T18-pollution-web-map.png", browser_script)
        self.assertIn("/map?mode=history", browser_script)
        self.assertIn("--window-size=1600,900", browser_script)
        self.assertIn("pollution map browser smoke ok", browser_script)


if __name__ == "__main__":
    unittest.main()

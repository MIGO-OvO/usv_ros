import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = REPO_ROOT / "scripts"


class BootServiceScriptTests(unittest.TestCase):
    def _read_script(self, name):
        path = SCRIPTS_DIR / name
        self.assertTrue(path.exists(), f"missing script: {path}")
        text = path.read_text(encoding="utf-8")
        self.assertTrue(text.startswith("#!/usr/bin/env bash"), f"{name} must use bash shebang")
        self.assertIn("set -euo pipefail", text)
        return text

    def test_install_script_writes_systemd_unit_with_start_stop_and_user_env(self):
        text = self._read_script("install_boot_service.sh")

        self.assertIn("usv-boot.service", text)
        self.assertIn("ExecStart=", text)
        self.assertIn("usv_boot_start.sh", text)
        self.assertIn("ExecStop=", text)
        self.assertIn("usv_boot_stop.sh", text)
        self.assertIn("USV_RUN_USER=", text)
        self.assertIn("USV_HOTSPOT_SSID=", text)
        self.assertIn("USV_HOTSPOT_PASSWORD=", text)
        self.assertIn("systemctl enable --now", text)

    def test_boot_start_runs_hotspot_then_ros_then_self_check_log(self):
        text = self._read_script("usv_boot_start.sh")

        main_index = text.index("main()")
        hotspot_index = text.index("setup_hotspot.sh", main_index)
        ros_index = text.index("start_usv_all.sh", main_index)
        check_index = text.index("run_self_check", main_index)
        self.assertLess(hotspot_index, ros_index)
        self.assertLess(ros_index, check_index)
        self.assertIn("boot_check.log", text)
        self.assertIn("wait_for_hotspot", text)
        self.assertIn("wait_for_web", text)
        self.assertIn("run_as_usv_user", text)
        self.assertIn("require_command nmcli", text)
        self.assertIn("require_command ip", text)
        self.assertIn("require_command mavlink-routerd", text)

    def test_boot_stop_stops_ros_before_hotspot(self):
        text = self._read_script("usv_boot_stop.sh")

        ros_stop_index = text.index("stop_usv_all.sh")
        hotspot_stop_index = text.index("stop_hotspot.sh")
        self.assertLess(ros_stop_index, hotspot_stop_index)
        self.assertIn("run_as_usv_user", text)

    def test_uninstall_script_disables_and_removes_systemd_unit(self):
        text = self._read_script("uninstall_boot_service.sh")

        self.assertIn("systemctl disable --now", text)
        self.assertIn("/etc/systemd/system/usv-boot.service", text)
        self.assertIn("systemctl daemon-reload", text)


if __name__ == "__main__":
    unittest.main()

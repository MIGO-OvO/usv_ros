import unittest
import subprocess
import stat
import tempfile
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = REPO_ROOT / "scripts"


class BootServiceScriptTests(unittest.TestCase):
    def _bash_path(self, path):
        resolved = Path(path).resolve()
        if resolved.drive:
            drive = resolved.drive.rstrip(":").lower()
            rest = str(resolved)[3:].replace("\\", "/")
            return f"/mnt/{drive}/{rest}"
        return resolved.as_posix()

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
        self.assertIn("USV_ENABLE_HOTSPOT=", text)
        self.assertIn('INTERNET_IFACE="${INTERNET_IFACE:-wlan0}"', text)
        self.assertIn('INTERNET_BAND="${INTERNET_BAND:-2.4g}"', text)
        self.assertIn('HOTSPOT_IFACE="${HOTSPOT_IFACE:-wlan1}"', text)
        self.assertIn("INTERNET_IFACE=", text)
        self.assertIn("INTERNET_BAND=", text)
        self.assertIn("HOTSPOT_ROUTE_METRIC=", text)
        self.assertIn("HOTSPOT_BAND=", text)
        self.assertIn("HOTSPOT_CHANNEL=", text)
        self.assertIn('USV_BOOT_START_NOW="${USV_BOOT_START_NOW:-false}"', text)
        self.assertIn('systemctl enable "$SERVICE_NAME"', text)
        self.assertIn('systemctl reset-failed "$SERVICE_NAME"', text)
        self.assertIn('systemctl start "$SERVICE_NAME"', text)
        self.assertNotIn("systemctl enable --now", text)

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

    def test_boot_start_can_skip_hotspot_when_disabled(self):
        text = self._read_script("usv_boot_start.sh")

        self.assertIn('USV_ENABLE_HOTSPOT="${USV_ENABLE_HOTSPOT:-false}"', text)
        self.assertIn("is_hotspot_enabled", text)
        self.assertIn("skip hotspot setup: disabled", text)
        self.assertIn("require_hotspot_self_check", text)
        self.assertIn("hotspot self-check skipped: disabled", text)

    def test_boot_stop_can_skip_hotspot_when_disabled(self):
        text = self._read_script("usv_boot_stop.sh")

        self.assertIn('USV_ENABLE_HOTSPOT="${USV_ENABLE_HOTSPOT:-false}"', text)
        self.assertIn("is_hotspot_enabled", text)
        self.assertIn("skip hotspot stop: disabled", text)

    def test_setup_hotspot_does_not_take_default_route(self):
        text = self._read_script("setup_hotspot.sh")

        self.assertIn("HOTSPOT_ROUTE_METRIC", text)
        self.assertIn("ipv4.never-default yes", text)
        self.assertIn("ipv6.never-default yes", text)
        self.assertIn("ipv4.route-metric", text)
        self.assertIn("ipv6.route-metric", text)

    def test_setup_hotspot_prefers_5ghz_with_fixed_channel(self):
        text = self._read_script("setup_hotspot.sh")

        self.assertIn('INTERFACE="${HOTSPOT_IFACE:-wlan1}"', text)
        self.assertIn('INTERNET_IFACE="${INTERNET_IFACE:-wlan0}"', text)
        self.assertIn('INTERNET_BAND="${INTERNET_BAND:-2.4g}"', text)
        self.assertIn('HOTSPOT_BAND="${HOTSPOT_BAND:-5g}"', text)
        self.assertIn("HOTSPOT_CHANNEL=", text)
        self.assertIn("NM_HOTSPOT_BAND", text)
        self.assertIn('802-11-wireless.band "$NM_HOTSPOT_BAND"', text)
        self.assertIn('802-11-wireless.channel "$HOTSPOT_CHANNEL"', text)
        self.assertIn("configure_internet_wifi_band", text)
        self.assertIn("INTERNET_WIFI_RECONNECT", text)
        self.assertIn("HOTSPOT_ALLOW_INTERNET_IFACE", text)
        self.assertIn("default-route-uses-hotspot-interface", text)
        self.assertIn("HOTSPOT_BAND=2.4g HOTSPOT_CHANNEL=6", text)

    def test_boot_start_assigns_internet_wifi_to_24ghz(self):
        text = self._read_script("usv_boot_start.sh")

        self.assertIn('INTERNET_IFACE="${INTERNET_IFACE:-wlan0}"', text)
        self.assertIn('INTERNET_BAND="${INTERNET_BAND:-2.4g}"', text)
        self.assertIn("configure_internet_wifi_band", text)
        self.assertIn("normalize_nm_wifi_band", text)
        self.assertIn('802-11-wireless.band "$nm_band"', text)
        self.assertIn("INTERNET_WIFI_RECONNECT", text)

    def test_status_reports_external_internet_path(self):
        text = self._read_script("status_usv_all.sh")

        self.assertIn("print_internet_status", text)
        self.assertIn("ip route show default", text)
        self.assertIn("get_wifi_iface_band", text)
        self.assertIn("target_band=$INTERNET_BAND", text)
        self.assertIn("internet-band-not-2.4g", text)
        self.assertIn("getent hosts github.com", text)
        self.assertIn("https://github.com/", text)
        self.assertIn("internet:", text)

    def test_status_can_print_quick_access_addresses(self):
        text = self._read_script("status_usv_all.sh")

        self.assertIn("print_access_addresses", text)
        self.assertIn("is_management_iface", text)
        self.assertIn("l4tbr*", text)
        self.assertIn("docker*", text)
        self.assertIn("web_tunnel:", text)
        self.assertIn("ssh:", text)
        self.assertIn("hotspot_web:", text)
        self.assertIn('case "${1:-full}" in', text)

    def test_addr_skips_jetson_usb_and_docker_bridge_addresses(self):
        with tempfile.TemporaryDirectory(dir=REPO_ROOT) as tmp:
            fake_ip = Path(tmp) / "ip"
            fake_ip.write_bytes(
                b"""#!/usr/bin/env bash
set -euo pipefail
if [[ "$*" == "route show default" ]]; then
    echo "default via 192.168.55.100 dev l4tbr0 proto static metric 100"
    exit 0
fi
if [[ "$*" == "-o -4 addr show dev l4tbr0 scope global" ]]; then
    echo "9: l4tbr0    inet 192.168.55.1/24 brd 192.168.55.255 scope global l4tbr0"
    exit 0
fi
if [[ "$*" == "-o -4 addr show dev wlan0 scope global" ]]; then
    echo "3: wlan0    inet 10.33.44.167/24 brd 10.33.44.255 scope global wlan0"
    exit 0
fi
if [[ "$*" == "-o -4 addr show scope global" ]]; then
    echo "9: l4tbr0    inet 192.168.55.1/24 brd 192.168.55.255 scope global l4tbr0"
    echo "5: docker0    inet 172.17.0.1/16 brd 172.17.255.255 scope global docker0"
    echo "3: wlan0    inet 10.33.44.167/24 brd 10.33.44.255 scope global wlan0"
    exit 0
fi
if [[ "$*" == "-4 addr show wlan0" ]]; then
    exit 1
fi
exit 1
"""
            )
            fake_ip.chmod(fake_ip.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
            command = "cd '{}' && export PATH='{}:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin' && bash scripts/status_usv_all.sh addr".format(
                self._bash_path(REPO_ROOT),
                self._bash_path(tmp),
            )
            result = subprocess.run(
                ["bash", "-lc", command],
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=True,
            )

        self.assertIn("lan_ip: 10.33.44.167 iface=wlan0", result.stdout)
        self.assertIn("ssh: ssh jetson@10.33.44.167", result.stdout)
        self.assertNotIn("ssh jetson@192.168.55.1", result.stdout)

    def test_status_flags_internet_wifi_band_mismatch(self):
        with tempfile.TemporaryDirectory(dir=REPO_ROOT) as tmp:
            fake_ip = Path(tmp) / "ip"
            fake_ip.write_bytes(
                b"""#!/usr/bin/env bash
set -euo pipefail
if [[ "$*" == "route show default" ]]; then
    echo "default via 10.33.44.1 dev wlan0 proto dhcp metric 600"
    exit 0
fi
if [[ "$*" == "link show wlan1" ]]; then
    exit 0
fi
if [[ "$*" == "-4 addr show wlan1" ]]; then
    exit 1
fi
exit 1
"""
            )
            fake_iw = Path(tmp) / "iw"
            fake_iw.write_bytes(
                b"""#!/usr/bin/env bash
set -euo pipefail
if [[ "$*" == "dev wlan0 link" ]]; then
    echo "Connected to 00:11:22:33:44:55 (on wlan0)"
    echo "freq: 5180"
    exit 0
fi
exit 1
"""
            )
            fake_nmcli = Path(tmp) / "nmcli"
            fake_nmcli.write_bytes(
                b"""#!/usr/bin/env bash
set -euo pipefail
if [[ "$*" == "-t -f NAME con show" ]]; then
    exit 0
fi
if [[ "$*" == "-t -f NAME con show --active" ]]; then
    echo "HomeWiFi"
    exit 0
fi
exit 0
"""
            )
            fake_getent = Path(tmp) / "getent"
            fake_getent.write_bytes(
                b"""#!/usr/bin/env bash
set -euo pipefail
if [[ "$*" == "hosts github.com" ]]; then
    echo "140.82.113.4 github.com"
    exit 0
fi
exit 1
"""
            )
            fake_curl = Path(tmp) / "curl"
            fake_curl.write_bytes(b"#!/usr/bin/env bash\nexit 0\n")

            for fake_cmd in (fake_ip, fake_iw, fake_nmcli, fake_getent, fake_curl):
                fake_cmd.chmod(fake_cmd.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)

            command = "cd '{}' && export PATH='{}:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin' && INTERNET_IFACE=wlan0 INTERNET_BAND=2.4g HOTSPOT_IFACE=wlan1 bash scripts/status_usv_all.sh full".format(
                self._bash_path(REPO_ROOT),
                self._bash_path(tmp),
            )
            result = subprocess.run(
                ["bash", "-lc", command],
                text=True,
                encoding="utf-8",
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=True,
            )

        self.assertIn("internet: route=present iface=wlan0 source=external band=5g", result.stdout)
        self.assertIn("target_iface=wlan0 target_band=2.4g", result.stdout)
        self.assertIn("issue=internet-band-not-2.4g", result.stdout)

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

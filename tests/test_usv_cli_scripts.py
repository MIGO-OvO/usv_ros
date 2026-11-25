import os
import shutil
import socket
import subprocess
import tempfile
import textwrap
import unittest
from pathlib import Path
from urllib.request import urlopen


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = REPO_ROOT / "scripts"


class UsvCliScriptTests(unittest.TestCase):
    def _read_script(self, name):
        path = SCRIPTS_DIR / name
        self.assertTrue(path.exists(), f"missing script: {path}")
        text = path.read_text(encoding="utf-8")
        self.assertTrue(text.startswith("#!/usr/bin/env bash"), f"{name} must use bash shebang")
        self.assertIn("set -euo pipefail", text)
        return text

    def test_web_server_bootstraps_package_root_before_lab_imports(self):
        path = SCRIPTS_DIR / "web_config_server.py"
        text = path.read_text(encoding="utf-8")

        package_root_index = text.index("REPO_ROOT = os.path.dirname(SCRIPT_DIR)")
        lab_import_index = text.index("from scripts.lib.lab_sim.coordinates import")

        self.assertLess(package_root_index, lab_import_index)
        self.assertIn("for _path in (REPO_ROOT, SCRIPT_DIR, MAP_RESOURCES_DIR):", text[:lab_import_index])
        self.assertIn("sys.path.insert(0, _path)", text[:lab_import_index])

    def test_lab_sim_node_bootstraps_package_root_before_lab_imports(self):
        path = SCRIPTS_DIR / "lab_sim_node.py"
        text = path.read_text(encoding="utf-8")

        package_root_index = text.index("REPO_ROOT = os.path.dirname(SCRIPT_DIR)")
        lab_import_index = text.index("from scripts.lib.lab_sim.vessel_model import")

        self.assertLess(package_root_index, lab_import_index)
        self.assertIn("for _path in (REPO_ROOT, SCRIPT_DIR):", text[:lab_import_index])
        self.assertIn("sys.path.insert(0, _path)", text[:lab_import_index])


    def test_cli_and_installer_scripts_exist(self):
        self._read_script("usvctl.sh")
        self._read_script("install_usv_commands.sh")

    def test_installer_links_all_public_commands(self):
        text = self._read_script("install_usv_commands.sh")

        for name in [
            "usvctl",
            "usvon",
            "usvoff",
            "usvrestart",
            "usvstatus",
            "usvhotspot",
            "usvaddr",
            "usvupdate",
            "usvbuild",
            "usvdeploy",
        ]:
            self.assertIn(name, text)

        self.assertIn('TARGET_DIR="${USV_COMMAND_DIR:-$HOME/.local/bin}"', text)
        self.assertIn('ln -s -- "$USVCTL_SCRIPT" "$target"', text)
        self.assertIn("uninstall_commands", text)
        self.assertIn("~/.bashrc", text)

    @unittest.skipUnless(os.name == 'posix' and shutil.which('bash'), 'requires Linux/WSL Bash')
    def test_installer_backs_up_legacy_files_and_broken_symlinks(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            installer = root / 'install_usv_commands.sh'
            installer.write_text((SCRIPTS_DIR / installer.name).read_text(encoding='utf-8'))
            (root / 'usvctl.sh').write_text('#!/usr/bin/env bash\nexit 0\n')
            target = root / 'bin'
            target.mkdir()
            (target / 'usvon').write_text('user command\n')
            (target / 'usvstatus').symlink_to('missing-old-helper')
            env = dict(os.environ, USV_COMMAND_DIR=str(target))
            for _ in range(2):
                result = subprocess.run(['bash', str(installer), 'install'], env=env,
                                        capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            backups = list(target.glob('.usv-commands-backup.*'))
            self.assertEqual(len(backups), 1)
            self.assertEqual((backups[0] / 'usvon').read_text(), 'user command\n')
            self.assertEqual(os.readlink(backups[0] / 'usvstatus'), 'missing-old-helper')
            for name in ('usvon', 'usvstatus', 'usvlogs'):
                self.assertEqual((target / name).resolve(), root / 'usvctl.sh')
            result = subprocess.run(['bash', str(installer), 'uninstall'], env=env,
                                    capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertFalse((target / 'usvon').exists())
            self.assertTrue((backups[0] / 'usvon').exists())

    def test_usvctl_dispatches_to_existing_runtime_scripts(self):
        text = self._read_script("usvctl.sh")

        self.assertIn('start_system "$@"', text)
        self.assertIn('"$SCRIPT_DIR/start_usv_all.sh" "$@"', text)
        self.assertIn('"$SCRIPT_DIR/stop_usv_all.sh"', text)
        self.assertIn('"$SCRIPT_DIR/restart_usv_all.sh" "$@"', text)
        self.assertIn('"$SCRIPT_DIR/status_usv_all.sh"', text)

    def test_usvctl_dispatches_hotspot_and_address_helpers(self):
        text = self._read_script("usvctl.sh")

        self.assertIn("hotspot_system()", text)
        self.assertIn("addr_system()", text)
        self.assertIn('sudo -E bash "$SCRIPT_DIR/setup_hotspot.sh" "$@"', text)
        self.assertIn('sudo -E bash "$SCRIPT_DIR/stop_hotspot.sh"', text)
        self.assertIn('"$SCRIPT_DIR/status_usv_all.sh" hotspot', text)
        self.assertIn('"$SCRIPT_DIR/status_usv_all.sh" addr', text)
        self.assertIn("usvhotspot)", text)
        self.assertIn("usvaddr)", text)
        self.assertIn("hotspot|ap)", text)
        self.assertIn("addr|address)", text)

    def test_update_uses_ros_package_git_pull_only(self):
        text = self._read_script("usvctl.sh")

        self.assertIn('git -C "$PKG_DIR" pull --ff-only', text)
        self.assertNotIn('git -C "$WS_DIR" pull', text)

    def test_usvctl_can_restore_runtime_script_permissions(self):
        text = self._read_script("usvctl.sh")

        self.assertIn("ensure_scripts_executable()", text)
        self.assertIn('chmod +x "$SCRIPT_DIR"/*.sh "$SCRIPT_DIR"/*.py "$SCRIPT_DIR"/map_resources/*.sh "$SCRIPT_DIR"/map_resources/*.py', text)

    def test_build_uses_ros_setup_and_catkin_make(self):
        text = self._read_script("usvctl.sh")
        common_env = self._read_script("common_env.sh")

        self.assertIn('ROS_SETUP="/opt/ros/noetic/setup.bash"', common_env)
        self.assertIn('source "$ROS_SETUP"', text)
        self.assertIn('catkin_make "$@"', text)

    @unittest.skipUnless(os.name == 'posix' and shutil.which('bash'), 'requires Linux/WSL Bash')
    def test_router_defaults_to_flight_controller_baud_and_port(self):
        env = dict(os.environ)
        for key in ("FCU_UART_BAUD", "ROUTER_TCP_PORT"):
            env.pop(key, None)
        with tempfile.TemporaryDirectory() as tmp:
            scripts = Path(tmp) / "ws/src/usv_ros/scripts"
            scripts.mkdir(parents=True)
            # Keep host-specific router settings outside the test.
            common = self._read_script("common_env.sh").replace(
                "/etc/default/usv-ros-router", str(Path(tmp) / "router.env"))
            (scripts / "common_env.sh").write_text(common, encoding="utf-8")
            result = subprocess.check_output(
                ["bash", "-c", 'source "$1"; printf "%s %s" "$FCU_UART_BAUD" "$ROUTER_TCP_PORT"',
                 "bash", str(scripts / "common_env.sh")], env=env, text=True)
            self.assertEqual("460800 5760", result)
            (Path(tmp) / "router.env").write_text(
                "FCU_UART_BAUD=230400\nROUTER_TCP_PORT=5761\n", encoding="utf-8")
            result = subprocess.check_output(
                ["bash", "-c", 'source "$1"; printf "%s %s" "$FCU_UART_BAUD" "$ROUTER_TCP_PORT"',
                 "bash", str(scripts / "common_env.sh")], env=env, text=True)
            self.assertEqual("230400 5761", result)

    @unittest.skipUnless(os.name == "posix" and all(shutil.which(cmd) for cmd in
        ("bash", "ss", "curl", "flock")), "requires Linux runtime commands")
    def test_restart_waits_for_its_new_web_listener(self):
        with tempfile.TemporaryDirectory() as tmp, socket.socket() as router, socket.socket() as web:
            root = Path(tmp)
            router.bind(("127.0.0.1", 0))
            web.bind(("127.0.0.1", 0))
            router_port, web_port = router.getsockname()[1], web.getsockname()[1]
            router.close()
            web.close()
            scripts = root / "ws/src/usv_ros/scripts"
            scripts.mkdir(parents=True)
            setup = root / "mock-setup.bash"
            setup.touch()
            for name in ("start_usv_all.sh", "stop_usv_all.sh", "restart_usv_all.sh",
                         "common_env.sh", "mavlink-router-supervisor.sh"):
                script = (SCRIPTS_DIR / name).read_text(encoding="utf-8")
                script = (script.replace("/opt/ros/noetic/setup.bash", str(setup))
                          .replace("$WS_DIR/devel/setup.bash", str(setup))
                          .replace("/etc/default/usv-ros-router", str(root / "router.env")))
                (scripts / name).write_text(script, encoding="utf-8")
            commands = root / "bin"
            commands.mkdir()

            def command(name, source):
                path = commands / name
                path.write_text("#!/usr/bin/env python3\n" + textwrap.dedent(source), encoding="utf-8")
                path.chmod(0o755)
                return path

            command("roscore", "import signal\nsignal.pause()\n")
            command("rostopic", "import os\nfrom pathlib import Path\n"
                    "raise SystemExit(not Path(os.environ['ROSCORE_PID_FILE']).is_file())\n")
            command("rosnode", "import sys\nassert sys.argv[1:] == ['ping', '-c', '1', '/web_config_server']\n")
            command("mavlink-routerd", """
                import http.server, sys
                assert sys.argv[-1] == "/dev/ttyTHS1:460800"
                port = int(sys.argv[sys.argv.index("-t") + 1])
                http.server.HTTPServer(("127.0.0.1", port), http.server.BaseHTTPRequestHandler).serve_forever()
            """)
            web_script = command("web_config_server.py", """
                import http.server, os, sys, time
                class Handler(http.server.BaseHTTPRequestHandler):
                    def do_GET(self):
                        self.send_response(200)
                        self.end_headers()
                        self.wfile.write(str(os.getpid()).encode())
                time.sleep(1)
                http.server.HTTPServer(("127.0.0.1", int(sys.argv[1])), Handler).serve_forever()
            """)
            command("roslaunch", """
                import os, signal, subprocess, sys
                port = next(arg.split(":=", 1)[1] for arg in sys.argv[1:] if arg.startswith("web_port:="))
                child = subprocess.Popen([sys.executable, os.environ["WEB_SCRIPT"], port], start_new_session=True)
                def stop(signum, frame):
                    child.terminate()
                signal.signal(signal.SIGTERM, stop)
                child.wait()
            """)
            env = dict(os.environ, PATH=f"{commands}:{os.environ['PATH']}", FCU_UART_BAUD="",
                       ROUTER_TCP_PORT=str(router_port), WEB_PORT=str(web_port), USV_BOOT_WAIT_SECONDS="10",
                       ROSCORE_PID_FILE=str(root / "ws/.usv_run/roscore.pid"), WEB_SCRIPT=str(web_script))
            env.pop("USV_RUNTIME_LOCKED", None)
            env.pop("MAVLINK_ROUTERD_BIN", None)

            def lifecycle(name):
                result = subprocess.run(["bash", str(scripts / name)], env=env,
                                        capture_output=True, text=True, timeout=25)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

            web_url = f"http://127.0.0.1:{web_port}/api/ui/debug"
            try:
                lifecycle("start_usv_all.sh")
                with urlopen(web_url, timeout=2) as response:
                    pid = response.read()
                for _ in range(2):
                    lifecycle("restart_usv_all.sh")
                    with urlopen(web_url, timeout=2) as response:
                        new_pid = response.read()
                    self.assertNotEqual(pid, new_pid)
                    pid = new_pid
            finally:
                lifecycle("stop_usv_all.sh")
            for port in (router_port, web_port):
                with socket.socket() as probe:
                    self.assertNotEqual(0, probe.connect_ex(("127.0.0.1", port)))

    def test_update_and_build_refuse_running_system(self):
        text = self._read_script("usvctl.sh")

        update_index = text.index("update_system()")
        build_index = text.index("build_system()")
        self.assertIn('require_system_stopped "update"', text[update_index:build_index])
        self.assertIn('require_system_stopped "build"', text[build_index:])
        self.assertIn("use 'usvdeploy'", text)

    def test_deploy_order_is_stop_update_chmod_build_start(self):
        text = self._read_script("usvctl.sh")

        deploy_index = text.index("deploy_system()")
        stop_index = text.index("stop_system", deploy_index)
        update_index = text.index("update_system", deploy_index)
        chmod_index = text.find("ensure_scripts_executable", deploy_index)
        build_index = text.index("build_system", deploy_index)
        start_index = text.index("start_system", deploy_index)

        self.assertNotEqual(chmod_index, -1, "deploy must chmod runtime scripts after update")
        self.assertLess(stop_index, update_index)
        self.assertLess(update_index, chmod_index)
        self.assertLess(chmod_index, build_index)
        self.assertLess(build_index, start_index)


if __name__ == "__main__":
    unittest.main()

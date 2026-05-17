import unittest
from pathlib import Path


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
            "usvupdate",
            "usvbuild",
            "usvdeploy",
        ]:
            self.assertIn(name, text)

        self.assertIn('TARGET_DIR="${USV_COMMAND_DIR:-$HOME/.local/bin}"', text)
        self.assertIn('ln -sfn "$USVCTL_SCRIPT" "$target"', text)
        self.assertIn("uninstall_commands", text)
        self.assertIn("~/.bashrc", text)

    def test_usvctl_dispatches_to_existing_runtime_scripts(self):
        text = self._read_script("usvctl.sh")

        self.assertIn('start_system "$@"', text)
        self.assertIn('"$SCRIPT_DIR/start_usv_all.sh" "$@"', text)
        self.assertIn('"$SCRIPT_DIR/stop_usv_all.sh"', text)
        self.assertIn('"$SCRIPT_DIR/restart_usv_all.sh" "$@"', text)
        self.assertIn('"$SCRIPT_DIR/status_usv_all.sh"', text)

    def test_update_uses_ros_package_git_pull_only(self):
        text = self._read_script("usvctl.sh")

        self.assertIn('git -C "$PKG_DIR" pull --ff-only', text)
        self.assertNotIn('git -C "$WS_DIR" pull', text)

    def test_build_uses_ros_setup_and_catkin_make(self):
        text = self._read_script("usvctl.sh")
        common_env = self._read_script("common_env.sh")

        self.assertIn('ROS_SETUP="/opt/ros/noetic/setup.bash"', common_env)
        self.assertIn('source "$ROS_SETUP"', text)
        self.assertIn('catkin_make "$@"', text)

    def test_update_and_build_refuse_running_system(self):
        text = self._read_script("usvctl.sh")

        update_index = text.index("update_system()")
        build_index = text.index("build_system()")
        self.assertIn('require_system_stopped "update"', text[update_index:build_index])
        self.assertIn('require_system_stopped "build"', text[build_index:])
        self.assertIn("use 'usvdeploy'", text)

    def test_deploy_order_is_stop_update_build_start(self):
        text = self._read_script("usvctl.sh")

        deploy_index = text.index("deploy_system()")
        stop_index = text.index("stop_system", deploy_index)
        update_index = text.index("update_system", deploy_index)
        build_index = text.index("build_system", deploy_index)
        start_index = text.index("start_system", deploy_index)

        self.assertLess(stop_index, update_index)
        self.assertLess(update_index, build_index)
        self.assertLess(build_index, start_index)


if __name__ == "__main__":
    unittest.main()

import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"


@unittest.skipUnless(
    os.name == "posix" and shutil.which("bash") and shutil.which("git"),
    "requires Bash and Git on Linux/WSL",
)
class UsvCliUpdateTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="usv-cli-update-")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.remote = self.root / "origin.git"
        self.upstream = self.root / "upstream"
        self.workspace = self.root / "workspace"
        self.repo = self.workspace / "src" / "usv_ros"
        self.env = dict(os.environ)
        self.env.update(
            GIT_AUTHOR_NAME="USV CLI Test",
            GIT_AUTHOR_EMAIL="usv-cli-test@example.invalid",
            GIT_COMMITTER_NAME="USV CLI Test",
            GIT_COMMITTER_EMAIL="usv-cli-test@example.invalid",
            GIT_CONFIG_NOSYSTEM="1",
            GIT_CONFIG_GLOBAL=os.devnull,
            GIT_TERMINAL_PROMPT="0",
        )
        self._run(["git", "init", "--bare", str(self.remote)])
        self._git(self.remote, "symbolic-ref", "HEAD", "refs/heads/main")
        self._run(["git", "clone", str(self.remote), str(self.upstream)])
        for name in ("usvctl.sh", "common_env.sh"):
            self._write(self.upstream, "scripts/" + name, (SCRIPTS_DIR / name).read_text(encoding="utf-8"))
        # Match the tracked executable mode of the public CLI entry point.
        (self.upstream / "scripts/usvctl.sh").chmod(0o755)
        self._write(self.upstream, ".gitignore", "*.log\n")
        for name in ("mavlink_trigger_node.py", "pump_control_node.py", "web_config_server.py"):
            self._write(self.upstream, "scripts/" + name, "initial code\n")
        self._commit(self.upstream, "initial code")
        self._git(self.upstream, "push", "origin", "main")
        self.repo.parent.mkdir(parents=True)
        self._run(["git", "clone", str(self.remote), str(self.repo)])

    def _run(self, command, check=True):
        result = subprocess.run(
            command, env=self.env, stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT, encoding="utf-8", timeout=30,
        )
        if check:
            self.assertEqual(result.returncode, 0, result.stdout)
        return result

    def _git(self, repo, *args, check=True):
        return self._run(["git", "-C", str(repo), *args], check=check).stdout.strip()

    def _write(self, repo, name, content):
        path = repo / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    def _commit(self, repo, message):
        self._git(repo, "add", "--all")
        self._git(repo, "commit", "-m", message)

    def _push_update(self):
        for name in ("mavlink_trigger_node.py", "pump_control_node.py", "web_config_server.py"):
            self._write(self.upstream, "scripts/" + name, "latest remote code\n")
        self._write(self.upstream, "scratch notes.txt", "remote file\n")
        self._commit(self.upstream, "remote update")
        self._git(self.upstream, "push", "origin", "main")

    def _update(self):
        return self._run(["bash", str(self.repo / "scripts/usvctl.sh"), "update"], check=False)

    def test_commands_accept_readable_non_executable_shell_scripts(self):
        commands = (
            ("start", "start_usv_all.sh", ["port:=a b"]),
            ("stop", "stop_usv_all.sh", []),
            ("restart", "restart_usv_all.sh", ["port:=a b"]),
            ("status", "status_usv_all.sh", []),
            ("addr", "status_usv_all.sh", ["addr"]),
            ("hotspot", "status_usv_all.sh", ["hotspot"]),
            ("import", "import_hotspot_update.sh", ["bundle path", "--build"]),
        )
        for command, script, expected_args in commands:
            with self.subTest(command=command):
                self._write(self.repo, "scripts/" + script,
                            '#!/usr/bin/env bash\nprintf "called\\n"\nprintf "<%s>\\n" "$@"\nexit 7\n')
                (self.repo / "scripts" / script).chmod(0o644)
                args = [] if command in ("addr", "hotspot") else expected_args
                result = self._run([
                    "bash", str(self.repo / "scripts/usvctl.sh"), command, *args,
                ], check=False)
                self.assertEqual(result.returncode, 7, result.stdout)
                self.assertIn("called", result.stdout)
                for arg in expected_args:
                    self.assertIn("<" + arg + ">", result.stdout)

    def _deploy(self):
        self.env["USV_TEST_EVENTS"] = str(self.root / "deploy.events")
        driver = r'''
source "$1" help >/dev/null
stop_system() { printf 'stop\n' >> "$USV_TEST_EVENTS"; }
ensure_scripts_executable() { printf 'chmod\n' >> "$USV_TEST_EVENTS"; }
build_system() { printf 'build\n' >> "$USV_TEST_EVENTS"; }
start_system() { printf 'start %s\n' "$*" >> "$USV_TEST_EVENTS"; }
deploy_system test_arg:=value
'''
        return self._run(["bash", "-c", driver, "usv-test", str(self.repo / "scripts/usvctl.sh")], check=False)

    def test_installed_status_survives_update_removing_manual_execute_bit(self):
        self._write(self.upstream, "scripts/status_usv_all.sh",
                    '#!/usr/bin/env bash\necho status-ok\n')
        self._commit(self.upstream, "status without execute bit")
        self._git(self.upstream, "push", "origin", "main")
        self.assertEqual(self._update().returncode, 0)
        status = self.repo / "scripts/status_usv_all.sh"
        status.chmod(0o755)
        self._write(self.repo, "scripts/install_usv_commands.sh",
                    (SCRIPTS_DIR / "install_usv_commands.sh").read_text(encoding="utf-8"))
        self.env["USV_COMMAND_DIR"] = str(self.root / "bin")
        self._run(["bash", str(self.repo / "scripts/install_usv_commands.sh"), "install"])
        self._push_update()

        result = self._update()

        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertFalse(status.stat().st_mode & 0o111)
        result = self._run([str(self.root / "bin/usvstatus")], check=False)
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertIn("status-ok", result.stdout)

    def test_restart_runs_non_executable_children_and_stops_on_failure(self):
        self._write(self.repo, "scripts/restart_usv_all.sh",
                    (SCRIPTS_DIR / "restart_usv_all.sh").read_text(encoding="utf-8"))
        self._write(self.repo, "scripts/start_usv_all.sh",
                    '#!/usr/bin/env bash\nprintf "start <%s>\\n" "$@"\n')
        for stop_code in (0, 9):
            with self.subTest(stop_code=stop_code):
                self._write(self.repo, "scripts/stop_usv_all.sh",
                            '#!/usr/bin/env bash\necho stopped\nexit %d\n' % stop_code)
                for name in ("restart_usv_all.sh", "start_usv_all.sh", "stop_usv_all.sh"):
                    (self.repo / "scripts" / name).chmod(0o644)
                result = self._run([
                    "bash", str(self.repo / "scripts/usvctl.sh"), "restart", "port:=a b",
                ], check=False)
                self.assertEqual(result.returncode, stop_code, result.stdout)
                self.assertIn("stopped", result.stdout)
                if stop_code == 0:
                    self.assertIn("start <port:=a b>", result.stdout)
                else:
                    self.assertNotIn("start", result.stdout)

    def test_update_backs_up_staged_unstaged_deleted_and_untracked_files(self):
        self._write(self.repo, "old-local.txt", "previous backup\n")
        self._git(self.repo, "stash", "push", "--include-untracked", "-m", "previous stash")
        previous_stash = self._git(self.repo, "rev-parse", "refs/stash")
        self._write(self.repo, "scripts/mavlink_trigger_node.py", "local trigger\n")
        self._write(self.repo, "scripts/pump_control_node.py", "staged pump\n")
        self._git(self.repo, "add", "scripts/pump_control_node.py")
        self._write(self.repo, "scripts/pump_control_node.py", "unstaged pump\n")
        (self.repo / "scripts/web_config_server.py").unlink()
        self._write(self.repo, "scratch notes.txt", "local untracked file\n")
        self._write(self.repo, "mission.log", "ignored mission data\n")
        self._push_update()

        result = self._update()

        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertEqual(self._git(self.repo, "rev-parse", "HEAD"), self._git(self.remote, "rev-parse", "main"))
        self.assertEqual(self._git(self.repo, "status", "--porcelain"), "")
        self.assertEqual((self.repo / "scripts/mavlink_trigger_node.py").read_text(), "latest remote code\n")
        self.assertEqual((self.repo / "scratch notes.txt").read_text(), "remote file\n")
        self.assertEqual((self.repo / "mission.log").read_text(), "ignored mission data\n")
        stash = self._git(self.repo, "rev-parse", "refs/stash")
        self.assertNotEqual(stash, previous_stash)
        self.assertEqual(self._git(self.repo, "rev-parse", "stash@{1}"), previous_stash)
        self.assertEqual(self._git(self.repo, "show", stash + ":scripts/mavlink_trigger_node.py"), "local trigger")
        self.assertEqual(self._git(self.repo, "show", stash + "^2:scripts/pump_control_node.py"), "staged pump")
        self.assertEqual(self._git(self.repo, "show", stash + ":scripts/pump_control_node.py"), "unstaged pump")
        self.assertNotIn("scripts/web_config_server.py", self._git(self.repo, "ls-tree", "-r", "--name-only", stash).splitlines())
        self.assertEqual(self._git(self.repo, "show", stash + "^3:scratch notes.txt"), "local untracked file")
        self.assertIn(stash, result.stdout)
        self.assertIn("stash apply --index", result.stdout)

        result = self._update()
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertEqual(self._git(self.repo, "rev-parse", "refs/stash"), stash)
        self.assertEqual(len(self._git(self.repo, "stash", "list").splitlines()), 2)

        # Restore on the original base to verify both the index and file contents.
        self._git(self.repo, "checkout", "--detach", stash + "^1")
        self._git(self.repo, "stash", "apply", "--index", stash)
        self.assertEqual((self.repo / "scripts/mavlink_trigger_node.py").read_text(), "local trigger\n")
        self.assertEqual(self._git(self.repo, "show", ":scripts/pump_control_node.py"), "staged pump")
        self.assertEqual((self.repo / "scripts/pump_control_node.py").read_text(), "unstaged pump\n")
        self.assertFalse((self.repo / "scripts/web_config_server.py").exists())
        self.assertEqual((self.repo / "scratch notes.txt").read_text(), "local untracked file\n")
        self.assertEqual((self.repo / "mission.log").read_text(), "ignored mission data\n")
        self.assertEqual(self._git(self.repo, "rev-parse", "refs/stash"), stash)

    def test_clean_update_does_not_create_a_stash(self):
        self._push_update()

        result = self._update()

        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertEqual(self._git(self.repo, "rev-parse", "HEAD"), self._git(self.remote, "rev-parse", "main"))
        self.assertEqual(self._git(self.repo, "stash", "list"), "")

    def test_untracked_only_changes_are_backed_up(self):
        self._write(self.repo, "scratch notes.txt", "local untracked file\n")
        self._push_update()

        result = self._update()

        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertEqual((self.repo / "scratch notes.txt").read_text(), "remote file\n")
        self.assertEqual(self._git(self.repo, "show", "refs/stash^3:scratch notes.txt"), "local untracked file")

    def test_failed_pull_keeps_backup_and_stops_deploy_before_build(self):
        self._write(self.repo, "scripts/mavlink_trigger_node.py", "local trigger\n")
        original_head = self._git(self.repo, "rev-parse", "HEAD")
        self._git(self.repo, "remote", "set-url", "origin", str(self.root / "missing.git"))

        result = self._deploy()

        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertEqual(self._git(self.repo, "rev-parse", "HEAD"), original_head)
        self.assertEqual(self._git(self.repo, "show", "refs/stash:scripts/mavlink_trigger_node.py"), "local trigger")
        self.assertIn(self._git(self.repo, "rev-parse", "refs/stash"), result.stdout)
        self.assertEqual((self.root / "deploy.events").read_text(), "stop\n")

    def test_diverged_commits_are_not_discarded(self):
        self._write(self.repo, "scripts/mavlink_trigger_node.py", "local committed code\n")
        self._commit(self.repo, "local commit")
        local_head = self._git(self.repo, "rev-parse", "HEAD")
        self._push_update()

        result = self._update()

        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertEqual(self._git(self.repo, "rev-parse", "HEAD"), local_head)
        self.assertEqual((self.repo / "scripts/mavlink_trigger_node.py").read_text(), "local committed code\n")

    def test_stash_failure_keeps_conflicts_and_stops_deploy_before_build(self):
        self._git(self.repo, "checkout", "-b", "local-side")
        self._write(self.repo, "scripts/mavlink_trigger_node.py", "side code\n")
        self._commit(self.repo, "side commit")
        self._git(self.repo, "checkout", "main")
        self._write(self.repo, "scripts/mavlink_trigger_node.py", "main code\n")
        self._commit(self.repo, "main commit")
        merge = self._run(["git", "-C", str(self.repo), "merge", "local-side"], check=False)
        self.assertNotEqual(merge.returncode, 0, merge.stdout)
        original_head = self._git(self.repo, "rev-parse", "HEAD")
        original_status = self._git(self.repo, "status", "--porcelain")

        result = self._deploy()

        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertIn("could not stash local changes", result.stdout)
        self.assertEqual(self._git(self.repo, "rev-parse", "HEAD"), original_head)
        self.assertEqual(self._git(self.repo, "status", "--porcelain"), original_status)
        self.assertEqual(self._git(self.repo, "stash", "list"), "")
        self.assertEqual((self.root / "deploy.events").read_text(), "stop\n")

    def test_update_refuses_running_system_without_stashing(self):
        self._write(self.repo, "scripts/mavlink_trigger_node.py", "local trigger\n")
        run_dir = self.workspace / ".usv_run"
        run_dir.mkdir()
        (run_dir / "usv_system.pid").write_text(str(os.getpid()), encoding="utf-8")
        self._push_update()

        result = self._update()

        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertIn("cannot update while usv_system is running", result.stdout)
        self.assertEqual(self._git(self.repo, "stash", "list"), "")
        self.assertEqual((self.repo / "scripts/mavlink_trigger_node.py").read_text(), "local trigger\n")

    def test_deploy_updates_dirty_repo_before_build_and_start(self):
        self._write(self.repo, "scripts/mavlink_trigger_node.py", "local trigger\n")
        self._push_update()

        result = self._deploy()

        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertEqual(self._git(self.repo, "rev-parse", "HEAD"), self._git(self.remote, "rev-parse", "main"))
        self.assertEqual((self.root / "deploy.events").read_text(), "stop\nchmod\nbuild\nstart test_arg:=value\n")
        self.assertEqual(self._git(self.repo, "show", "refs/stash:scripts/mavlink_trigger_node.py"), "local trigger")


if __name__ == "__main__":
    unittest.main()

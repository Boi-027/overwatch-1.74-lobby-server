"""Launcher checks use disposable sockets and never launch Overwatch."""
import argparse
import importlib.util
import json
from pathlib import Path
import socket
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

LAUNCHER_PATH = Path(__file__).resolve().parents[1] / "tools" / "launch_retail.py"
launcher = None
if LAUNCHER_PATH.is_file():
    spec = importlib.util.spec_from_file_location("launch_retail", LAUNCHER_PATH)
    launcher = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(launcher)


class Process:
    def __init__(self, pid=123, exit_code=None):
        self.pid, self.exit_code = pid, exit_code
        self.terminated = self.killed = False

    def poll(self):
        return self.exit_code

    def terminate(self):
        self.terminated = True
        self.exit_code = 0

    def wait(self, timeout=None):
        return self.exit_code

    def kill(self):
        self.killed = True
        self.exit_code = -1


class LauncherTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(launcher, "The reliable retail launcher is missing")
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def listening_port(self):
        listener = socket.socket()
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        self.addCleanup(listener.close)
        return listener.getsockname()[1]

    def service(self, name, ports):
        return launcher.Service(name, tuple(ports), ["python", "helper.py"], self.root)

    def test_override_and_default_candidates_preserve_unicode_and_spaces(self):
        directory = self.root / "Игра с пробелами"
        directory.mkdir()
        game = directory / "Overwatch.exe"
        game.touch()
        self.assertEqual(launcher.select_game(str(game)), game.resolve())
        self.assertEqual(launcher.select_game(candidates=[self.root / "missing.exe", game]), game.resolve())

    def test_missing_explicit_game_does_not_fall_back(self):
        available = self.root / "Overwatch.exe"
        available.touch()
        with self.assertRaises(launcher.LaunchError):
            launcher.select_game(self.root / "missing.exe", candidates=[available])

    def test_timeout_must_be_finite_positive_and_representable_in_milliseconds(self):
        for value in ("nan", "inf", "-inf", "0", "-1", "0.0001", "4294967.295", "bad"):
            with self.subTest(value=value), self.assertRaises(argparse.ArgumentTypeError):
                launcher.timeout_seconds(value)
        self.assertEqual(launcher.timeout_seconds("2.5"), 2.5)

    def test_preflight_checks_dll_before_any_process_start(self):
        game = self.root / "Overwatch.exe"
        game.touch()
        with patch.object(launcher.subprocess, "Popen") as popen:
            with self.assertRaises(launcher.LaunchError):
                launcher.validate_prerequisites(self.root, game)
        popen.assert_not_called()

    def test_preflight_rejects_missing_python_dependency(self):
        game = self.root / "Overwatch.exe"
        game.touch()
        (self.root / "relay").mkdir()
        (self.root / "relay" / "owwfd_relay.dll").touch()
        with patch.object(launcher.importlib, "import_module", side_effect=ImportError("missing protobuf")):
            with self.assertRaisesRegex(launcher.LaunchError, "protobuf"):
                launcher.validate_prerequisites(self.root, game)

    def test_partial_service_group_fails_without_starting_or_stopping_processes(self):
        port = self.listening_port()
        with socket.socket() as unused:
            unused.bind(("127.0.0.1", 0))
            closed_port = unused.getsockname()[1]
        manager = launcher.ServiceManager([self.service("partial", [port, closed_port])], self.root)
        with patch.object(launcher.subprocess, "Popen") as popen:
            with self.assertRaisesRegex(launcher.LaunchError, "partial"):
                manager.ensure(0.1)
        popen.assert_not_called()
        self.assertEqual(manager.owned, [])
        self.assertTrue(launcher.port_open(port))

    def test_fully_reachable_services_are_reused_and_never_owned(self):
        port = self.listening_port()
        manager = launcher.ServiceManager([self.service("existing", [port])], self.root)
        with patch.object(launcher.subprocess, "Popen") as popen:
            manager.ensure(0.1)
            manager.stop_owned()
        popen.assert_not_called()
        self.assertEqual(manager.records[0]["state"], "reused")
        self.assertEqual(manager.owned, [])
        self.assertTrue(launcher.port_open(port))

    def test_missing_helper_is_started_hidden_and_only_owned_helper_is_stopped(self):
        port = self.listening_port()
        service = self.service("new", [port])
        process = Process()
        manager = launcher.ServiceManager([service], self.root)
        with patch.object(launcher, "inspect_services", return_value=[{"name": "new", "ports": [port], "state": "missing"}]), patch.object(launcher.subprocess, "Popen", return_value=process) as popen:
            manager.ensure(0.1)
            manager.stop_owned()
        self.assertEqual(popen.call_args.args[0], ["python", "helper.py"])
        self.assertEqual(popen.call_args.kwargs["creationflags"], launcher.subprocess.CREATE_NO_WINDOW)
        self.assertTrue(process.terminated)
        self.assertEqual(manager.records[0]["state"], "owned")
        self.assertEqual(manager.records[0]["pid"], 123)

    def test_readiness_requires_every_port_and_fails_if_helper_exits(self):
        port = self.listening_port()
        with socket.socket() as unused:
            unused.bind(("127.0.0.1", 0))
            closed_port = unused.getsockname()[1]
        with self.assertRaises(launcher.LaunchError):
            launcher.wait_for_ports([port, closed_port], 0.02)
        with self.assertRaisesRegex(launcher.LaunchError, "exited"):
            launcher.wait_for_ports([closed_port], 0.1, Process(exit_code=7))
        launcher.wait_for_ports([port], 0.1)

    def test_running_game_is_rejected_and_absence_is_allowed(self):
        existing = SimpleNamespace(find_pid=lambda: 987)
        with self.assertRaisesRegex(launcher.LaunchError, "987"):
            launcher.reject_existing_game(existing)
        def absent():
            raise RuntimeError("process Overwatch.exe not found")
        launcher.reject_existing_game(SimpleNamespace(find_pid=absent))
        def ambiguous():
            raise RuntimeError("multiple processes named Overwatch.exe: [1, 2]; specify a PID")
        with self.assertRaisesRegex(launcher.LaunchError, "multiple"):
            launcher.reject_existing_game(SimpleNamespace(find_pid=ambiguous))

    def test_loader_readiness_is_polled_on_the_new_process_pid(self):
        calls = []
        def loader(pid):
            calls.append(pid)
            if len(calls) == 1:
                raise OSError("loader is not initialized")
            return 0x123456789
        launcher.wait_for_loader(Process(pid=456), SimpleNamespace(_remote_load_library=loader), 0.5)
        self.assertEqual(calls, [456, 456])

    def test_loader_readiness_stops_on_exit_or_timeout(self):
        injector = SimpleNamespace(_remote_load_library=lambda pid: 0)
        with self.assertRaisesRegex(launcher.LaunchError, "exited"):
            launcher.wait_for_loader(Process(exit_code=3), injector, 0.1)
        with self.assertRaises(launcher.LaunchError):
            launcher.wait_for_loader(Process(), injector, 0.02)

    def test_check_only_never_starts_a_helper_or_game(self):
        injector = SimpleNamespace(find_pid=lambda: 987)
        with patch.object(launcher, "ROOT", self.root), patch.object(launcher, "validate_prerequisites", return_value=self.root / "Overwatch.exe"), patch.object(launcher, "load_injector", return_value=injector), patch.object(launcher, "inspect_services", return_value=[]), patch.object(launcher.subprocess, "Popen") as popen:
            self.assertEqual(launcher.main(["--check-only"]), 0)
        popen.assert_not_called()

    def test_injection_failure_rolls_back_owned_helpers_and_preserves_game_and_reused_service(self):
        self.launch_scenario(TimeoutError("verified injection timed out"), expected_status=1)

    def test_success_manifest_records_verified_base_and_leaves_owned_helpers_running(self):
        self.launch_scenario(0x123456789, expected_status=0)

    def test_interrupted_injection_also_cleans_up_owned_helpers(self):
        self.launch_scenario(KeyboardInterrupt(), expected_status=1)

    def launch_scenario(self, injection_result, expected_status):
        port = self.listening_port()
        helper, game = Process(pid=123), Process(pid=789)
        injection_calls = []
        def absent():
            raise RuntimeError("process Overwatch.exe not found")
        def inject(dll, pid, timeout_ms):
            injection_calls.append((dll, pid, timeout_ms))
            if isinstance(injection_result, BaseException):
                raise injection_result
            return injection_result
        injector = SimpleNamespace(find_pid=absent, _remote_load_library=lambda pid: 0x1000, inject=inject)
        services = [self.service("existing", [port]), self.service("new", [port])]
        records = [{"name": "existing", "ports": [port], "state": "reused"},
                   {"name": "new", "ports": [port], "state": "missing"}]
        game_path = self.root / "Игра с пробелами" / "Overwatch.exe"
        with patch.object(launcher, "ROOT", self.root), patch.object(launcher, "validate_prerequisites", return_value=game_path), patch.object(launcher, "load_injector", return_value=injector), patch.object(launcher, "services_for", return_value=services), patch.object(launcher, "inspect_services", side_effect=lambda services: [dict(record) for record in records]), patch.object(launcher.subprocess, "Popen", side_effect=[helper, game]) as popen:
            try:
                result = launcher.main(["--timeout", "0.5"])
            except KeyboardInterrupt:
                self.fail("Launcher must clean up owned helpers when injection is interrupted")
            self.assertEqual(result, expected_status)
        manifest = json.loads(next((self.root / "logs").glob("launch-*/manifest.json")).read_text(encoding="utf-8"))
        self.assertEqual(manifest["services"][0]["state"], "reused")
        self.assertEqual(manifest["services"][1]["state"], "owned")
        self.assertEqual(manifest["game"]["pid"], 789)
        self.assertTrue(manifest["game"]["left_running"])
        self.assertEqual(popen.call_args.args[0], [str(game_path), "--BNetServer=127.0.0.1:1119", "--console"])
        self.assertEqual(injection_calls, [(str(self.root / "relay" / "owwfd_relay.dll"), 789, 500)])
        self.assertFalse(game.terminated)
        self.assertTrue(launcher.port_open(port))
        if expected_status:
            self.assertTrue(helper.terminated)
            self.assertEqual(manifest["status"], "failed")
            self.assertTrue(manifest["services"][1]["stopped"])
        else:
            self.assertFalse(helper.terminated)
            self.assertEqual(manifest["status"], "verified")
            self.assertEqual(manifest["relay_base"], "0x123456789")


if __name__ == "__main__":
    unittest.main()

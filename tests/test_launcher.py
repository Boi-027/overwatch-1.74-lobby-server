"""The launcher: finding the game, its language, closing a leftover copy, the relay DLL and packages."""

import hashlib
import io
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ow174.launcher import LaunchError, game, relay, requirements


class TempDirTest(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="ow174 test ")
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)

    def make_game(self, folder="Game"):
        path = self.root / folder / "Overwatch.exe"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"")
        return path


class FindGameTests(TempDirTest):
    def test_an_explicit_path_with_spaces_and_unicode_is_used(self):
        exe = self.make_game("Spiele über Überall")
        self.assertEqual(game.find_game(exe, saved=self.root / "saved.txt"), exe.resolve())

    def test_an_explicit_path_that_is_not_the_game_does_not_fall_back(self):
        saved = self.root / "saved.txt"
        saved.write_text(str(self.make_game()), encoding="utf-8")
        with self.assertRaises(LaunchError):
            game.find_game(self.root / "missing" / "Overwatch.exe", saved=saved)

    def test_the_game_picked_last_time_is_reused_without_asking(self):
        exe = self.make_game()
        saved = self.root / "saved.txt"
        saved.write_text(str(exe) + "\n", encoding="utf-8")
        with patch.object(game, "pick_game") as pick:
            self.assertEqual(game.find_game(saved=saved), exe.resolve())
        pick.assert_not_called()

    def test_a_picked_game_is_remembered(self):
        exe = self.make_game()
        saved = self.root / "saved.txt"
        with patch.object(game, "UNPACKED_GAME_PATHS", ()), patch.object(game, "pick_game", return_value=exe):
            self.assertEqual(game.find_game(saved=saved), exe)
        self.assertEqual(saved.read_text(encoding="utf-8"), str(exe))


class LocaleTests(TempDirTest):
    """A build without the language the game picks by itself starts with a black screen."""

    TAGS = (
        "DevReg EExt deDE speech?:DevReg EExt deDE text?:DevReg EExt enUS speech?:"
        "DevReg EExt esES text?:DevReg EExt ruRU text?"
    )

    def installed_game(self):
        exe = self.make_game("Overwatch/_retail_")
        info = "Branch!STRING:0|Active!DEC:1|Tags!STRING:0|Product!STRING:0\nkr|1|" + self.TAGS + "|pro\n"
        (exe.parent.parent / ".build.info").write_text(info, encoding="utf-8")
        return exe

    def test_installed_locales_come_from_the_tags(self):
        self.assertEqual(game.installed_locales(self.installed_game()), {"deDE", "enUS", "esES", "ruRU"})

    def test_system_language_is_used_when_the_build_has_it(self):
        self.assertEqual(game.choose_locale({"deDE", "enUS"}, "deDE"), "deDE")

    def test_same_language_is_used_when_the_exact_code_is_missing(self):
        self.assertEqual(game.choose_locale({"esES", "esMX", "enUS"}, "es419"), "esES")

    def test_english_is_the_fallback(self):
        self.assertEqual(game.choose_locale({"deDE", "enUS"}, "itIT"), "enUS")

    def test_nothing_is_chosen_when_there_is_no_information(self):
        self.assertIsNone(game.choose_locale(set(), "deDE"))
        self.assertIsNone(game.choose_locale({"deDE"}, "itIT"))
        self.assertEqual(game.installed_locales(Path("Z:/no/such/_retail_/Overwatch.exe")), set())

    def test_option_overrides_detection(self):
        exe = self.installed_game()
        with patch.object(game, "system_locale", return_value="ruRU"):
            self.assertEqual(game.game_locale(exe), "ruRU")
            self.assertEqual(game.game_locale(exe, "deDE"), "deDE")
            self.assertIsNone(game.game_locale(exe, "none"))


class RunningCopyTests(TempDirTest):
    def test_only_this_copy_of_the_game_is_closed(self):
        ours, other = self.make_game("Game"), self.make_game("Overwatch 2")
        running = {10: str(ours), 11: str(other), 12: None}
        killed = []

        def taskkill(command, **kwargs):
            killed.append(command[-1])
            running.pop(int(command[-1]), None)

        with (
            patch.object(game.inject, "find_pids", side_effect=lambda name: sorted(running)),
            patch.object(game.inject, "image_path", side_effect=running.get),
            patch.object(game.subprocess, "run", side_effect=taskkill),
        ):
            game.close_running_copy(ours)
        self.assertEqual(killed, ["10"])
        self.assertEqual(sorted(running), [11, 12])

    def test_nothing_is_closed_when_only_another_overwatch_is_running(self):
        with (
            patch.object(game.inject, "find_pids", return_value=[11]),
            patch.object(game.inject, "image_path", return_value=r"C:\Other\Overwatch.exe"),
            patch.object(game.subprocess, "run") as run,
        ):
            game.close_running_copy(self.make_game())
        run.assert_not_called()

    def test_a_failed_process_listing_stops_with_a_clear_error(self):
        broken = subprocess.CalledProcessError(1, "tasklist")
        with (
            patch.object(game.inject, "find_pids", side_effect=broken),
            self.assertRaisesRegex(LaunchError, "Cannot check for a running Overwatch"),
        ):
            game.close_running_copy(self.make_game())


class Process:
    def __init__(self, pid=456, exit_code=None):
        self.pid = pid
        self.returncode = exit_code

    def poll(self):
        return self.returncode


class InjectRelayTests(unittest.TestCase):
    def test_the_new_process_is_polled_until_its_loader_is_ready(self):
        polled = []

        def loader(pid):
            polled.append(pid)
            if len(polled) < 3:
                raise OSError("loader is not initialized")
            return 0x1000

        with (
            patch.object(game.inject, "remote_load_library", side_effect=loader),
            patch.object(game.inject, "inject", return_value=0x180000000) as inject,
        ):
            self.assertEqual(game.inject_relay(Process(pid=456), Path("relay.dll"), timeout=1), 0x180000000)
        self.assertEqual(polled, [456, 456, 456])
        inject.assert_called_once_with("relay.dll", 456, 1000)

    def test_it_stops_when_the_game_exits_or_the_time_runs_out(self):
        with (
            patch.object(game.inject, "remote_load_library", return_value=0),
            self.assertRaisesRegex(LaunchError, "exited with code 3"),
        ):
            game.inject_relay(Process(exit_code=3), Path("relay.dll"), timeout=0.1)
            with self.assertRaisesRegex(LaunchError, "did not become ready"):
                game.inject_relay(Process(), Path("relay.dll"), timeout=0.02)

    def test_a_failed_injection_is_a_launch_error(self):
        with (
            patch.object(game.inject, "remote_load_library", return_value=0x1000),
            patch.object(game.inject, "inject", side_effect=OSError("access denied")),
            self.assertRaisesRegex(LaunchError, "access denied"),
        ):
            game.inject_relay(Process(), Path("relay.dll"), timeout=1)


class RelayDllTests(TempDirTest):
    OFFICIAL = b"official relay build"

    def setUp(self):
        super().setUp()
        self.dll = self.root / "relay" / "owwfd_relay.dll"
        official_sha = hashlib.sha256(self.OFFICIAL).hexdigest()
        patcher = patch.object(relay, "RELEASE_SHA256", official_sha)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_a_self_built_dll_is_kept_and_nothing_is_downloaded(self):
        self.dll.parent.mkdir()
        self.dll.write_bytes(b"built from source")
        with patch.object(relay.urllib.request, "urlopen") as urlopen:
            self.assertEqual(relay.ensure_relay_dll(self.dll), self.dll)
        urlopen.assert_not_called()
        self.assertEqual(self.dll.read_bytes(), b"built from source")

    def test_a_missing_dll_is_downloaded(self):
        with patch.object(relay.urllib.request, "urlopen", return_value=io.BytesIO(self.OFFICIAL)):
            relay.ensure_relay_dll(self.dll)
        self.assertEqual(self.dll.read_bytes(), self.OFFICIAL)

    def test_a_download_that_is_not_the_official_build_is_not_saved(self):
        with (
            patch.object(relay.urllib.request, "urlopen", return_value=io.BytesIO(b"tampered")),
            self.assertRaisesRegex(LaunchError, "not the official build"),
        ):
            relay.ensure_relay_dll(self.dll)
        self.assertFalse(self.dll.exists())

    def test_a_failed_download_says_where_to_put_the_dll(self):
        with (
            patch.object(relay.urllib.request, "urlopen", side_effect=OSError("offline")),
            self.assertRaisesRegex(LaunchError, "Releases page"),
        ):
            relay.ensure_relay_dll(self.dll)


class RequirementsTests(TempDirTest):
    def requirements(self, text):
        path = self.root / "requirements.txt"
        path.write_text(text, encoding="utf-8")
        return path

    def test_pinned_and_installed_packages_are_satisfied(self):
        versions = {"protobuf": "6.33.6", "websockets": "17.0.1"}
        path = self.requirements("protobuf==6.33.6  # comment\n\nwebsockets==17.0.1\n")
        with patch.object(requirements.metadata, "version", side_effect=versions.get):
            self.assertEqual(requirements.missing_requirements(path), [])

    def test_other_versions_missing_packages_and_loose_pins_are_missing(self):
        def version(name):
            if name == "absent":
                raise requirements.metadata.PackageNotFoundError(name)
            return "1.0"

        path = self.requirements("protobuf==6.33.6\nabsent==1.0\nloose>=1.0\n")
        with patch.object(requirements.metadata, "version", side_effect=version):
            self.assertEqual(
                requirements.missing_requirements(path), ["protobuf==6.33.6", "absent==1.0", "loose>=1.0"]
            )

    def test_pip_runs_only_when_something_is_missing(self):
        path = self.requirements("")
        with patch.object(requirements.subprocess, "run") as run:
            requirements.ensure_requirements(path)
        run.assert_not_called()

    def test_a_failed_install_is_a_launch_error(self):
        path = self.requirements("absent==1.0\n")
        failed = subprocess.CompletedProcess([], 1)
        with (
            patch.object(
                requirements.metadata, "version", side_effect=requirements.metadata.PackageNotFoundError
            ),
            patch.object(requirements.subprocess, "run", return_value=failed),
            self.assertRaisesRegex(LaunchError, "internet"),
        ):
            requirements.ensure_requirements(path)


if __name__ == "__main__":
    unittest.main()

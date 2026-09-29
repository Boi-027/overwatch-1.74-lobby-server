import ctypes
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ow174.launcher import inject as injector


class Kernel:
    def __init__(self, wait=0, short_write=False):
        self.wait = wait
        self.short_write = short_write
        self.path = None
        self.freed = []
        self.closed = []
        self.opens = 0

    def OpenProcess(self, *args):
        self.opens += 1
        return 0x100000001

    def VirtualAllocEx(self, *args):
        return 0x700000000

    def WriteProcessMemory(self, process, remote, data, size, written):
        self.path = ctypes.string_at(data, size)
        written._obj.value = size - 1 if self.short_write else size
        return 1

    def GetModuleHandleA(self, *args):
        return 0x1000

    def GetProcAddress(self, *args):
        return 0x1100

    def CreateRemoteThread(self, *args):
        return 0x200000002

    def WaitForSingleObject(self, *args):
        return self.wait

    def GetExitCodeThread(self, thread, code):
        code._obj.value = 0  # A 64-bit HMODULE may have a zero low DWORD.
        return 1

    def VirtualFreeEx(self, process, remote, size, kind):
        self.freed.append(remote)
        return 1

    def CloseHandle(self, handle):
        self.closed.append(handle)
        return 1


class InjectionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="ow-inject-")
        self.addCleanup(self.temp.cleanup)
        self.dll = Path(self.temp.name) / "folder with spaces" / "relay.dll"
        self.dll.parent.mkdir()
        self.dll.write_bytes(b"unit fixture; never loaded by Windows")

    def invoke(self, kernel, modules=(0, 0x180000000)):
        with (
            patch.object(injector, "k32", kernel),
            patch.object(injector, "_loaded_module", side_effect=modules, create=True),
            patch.object(injector, "remote_load_library", return_value=0x123456789, create=True),
        ):
            return injector.inject(str(self.dll), 123)

    def test_unicode_path_and_actual_64_bit_module_confirm_success(self):
        kernel = Kernel()
        result = self.invoke(kernel)
        self.assertEqual(result, 0x180000000)
        self.assertEqual(kernel.path, (str(self.dll.resolve()) + "\0").encode("utf-16-le"))
        self.assertEqual(kernel.freed, [0x700000000])
        self.assertCountEqual(kernel.closed, [0x100000001, 0x200000002])

    def test_timeout_is_error_and_does_not_free_buffer_used_by_loader(self):
        kernel = Kernel(wait=258)
        with self.assertRaises(TimeoutError):
            self.invoke(kernel)
        self.assertEqual(kernel.freed, [])
        self.assertCountEqual(kernel.closed, [0x100000001, 0x200000002])

    def test_finished_thread_without_loaded_module_is_failure(self):
        kernel = Kernel()
        with self.assertRaises(RuntimeError):
            self.invoke(kernel, modules=(0, 0))
        self.assertEqual(kernel.freed, [0x700000000])
        self.assertCountEqual(kernel.closed, [0x100000001, 0x200000002])

    def test_short_remote_write_does_not_start_loader(self):
        kernel = Kernel(short_write=True)
        with self.assertRaises(OSError):
            self.invoke(kernel)
        self.assertEqual(kernel.freed, [0x700000000])
        self.assertEqual(kernel.closed, [0x100000001])

    def test_missing_file_fails_before_opening_process(self):
        kernel = Kernel()
        with patch.object(injector, "k32", kernel), self.assertRaises(FileNotFoundError):
            injector.inject(str(self.dll.parent / "missing.dll"), 123)
        self.assertEqual(kernel.opens, 0)

    def test_already_loaded_dll_is_not_loaded_twice(self):
        kernel = Kernel()
        self.assertEqual(self.invoke(kernel, modules=(0x180000000,)), 0x180000000)
        self.assertEqual(kernel.opens, 0)


class ModuleSnapshotRetryTests(unittest.TestCase):
    """A game that is still loading makes the module snapshot fail for a moment (seen in the wild
    as 'WinError 24 ... command length is incorrect'); that must not abort the launch."""

    def fail_then_succeed(self, code, failures):
        calls = []

        def snapshot(pid):
            calls.append(pid)
            if len(calls) <= failures:
                raise ctypes.WinError(code)
            return [("C:\\game\\Overwatch.exe", 0x1000)]

        return snapshot, calls

    def test_transient_errors_are_retried_until_the_list_is_ready(self):
        for code in (24, 18, 299):
            snapshot, calls = self.fail_then_succeed(code, failures=3)
            with patch.object(injector, "_snapshot_modules", snapshot):
                self.assertEqual(injector._modules(42), [("C:\\game\\Overwatch.exe", 0x1000)])
            self.assertEqual(len(calls), 4, code)

    def test_other_errors_are_raised_immediately(self):
        snapshot, calls = self.fail_then_succeed(5, failures=10)
        with (
            patch.object(injector, "_snapshot_modules", snapshot),
            self.assertRaises(OSError) as raised,
        ):
            injector._modules(42)
        self.assertEqual(raised.exception.winerror, 5)
        self.assertEqual(len(calls), 1)

    def test_a_snapshot_that_never_recovers_is_raised_after_the_patience_runs_out(self):
        snapshot, calls = self.fail_then_succeed(24, failures=10**9)
        with (
            patch.object(injector, "_snapshot_modules", snapshot),
            self.assertRaises(OSError) as raised,
        ):
            injector._modules(42, patience=0.05)
        self.assertEqual(raised.exception.winerror, 24)
        self.assertGreater(len(calls), 1)


class ImagePathTests(unittest.TestCase):
    def test_image_path_of_a_real_process_and_of_a_missing_one(self):
        import os
        import sys

        path = injector.image_path(os.getpid())
        self.assertEqual(Path(path).name.lower(), Path(sys.executable).name.lower())
        self.assertIsNone(injector.image_path(4194301))


if __name__ == "__main__":
    unittest.main()

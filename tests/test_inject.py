import ctypes
import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location('relay_inject', Path(__file__).resolve().parents[1] / 'relay/inject.py')
injector = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(injector)


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
        self.temp = tempfile.TemporaryDirectory(prefix='ow-inject-')
        self.addCleanup(self.temp.cleanup)
        self.dll = Path(self.temp.name) / 'folder with spaces' / 'relay.dll'
        self.dll.parent.mkdir()
        self.dll.write_bytes(b'unit fixture; never loaded by Windows')

    def invoke(self, kernel, modules=(0, 0x180000000)):
        with patch.object(injector, 'k32', kernel), \
             patch.object(injector, '_loaded_module', side_effect=modules, create=True), \
             patch.object(injector, '_remote_load_library', return_value=0x123456789, create=True):
            return injector.inject(str(self.dll), 123)

    def test_unicode_path_and_actual_64_bit_module_confirm_success(self):
        kernel = Kernel()
        result = self.invoke(kernel)
        self.assertEqual(result, 0x180000000)
        self.assertEqual(kernel.path, (str(self.dll.resolve()) + '\0').encode('utf-16-le'))
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
        with patch.object(injector, 'k32', kernel):
            with self.assertRaises(FileNotFoundError):
                injector.inject(str(self.dll.parent / 'missing.dll'), 123)
        self.assertEqual(kernel.opens, 0)

    def test_already_loaded_dll_is_not_loaded_twice(self):
        kernel = Kernel()
        self.assertEqual(self.invoke(kernel, modules=(0x180000000,)), 0x180000000)
        self.assertEqual(kernel.opens, 0)

    def test_multiple_matching_processes_require_explicit_pid(self):
        result = type('Result', (), {'stdout': '"Overwatch.exe","111","Console","1","1 K"\n"Overwatch.exe","222","Console","1","1 K"\n', 'returncode': 0})()
        with patch.object(injector.subprocess, 'run', return_value=result):
            with self.assertRaisesRegex(RuntimeError, 'multiple|Multiple|ambiguous'):
                injector.find_pid()


if __name__ == '__main__':
    unittest.main()

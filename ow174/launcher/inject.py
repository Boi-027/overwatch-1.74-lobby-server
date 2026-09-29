"""Loading the relay DLL into the game process, and finding running copies of the game (Windows)."""

import csv
import ctypes
import ctypes.wintypes as wt
import os
import subprocess
import time
from pathlib import Path

k32 = ctypes.WinDLL("kernel32", use_last_error=True)
PROCESS_INJECT = 0x0002 | 0x0008 | 0x0010 | 0x0020 | 0x0400
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
MEM_COMMIT_RESERVE, MEM_RELEASE, PAGE_READWRITE = 0x3000, 0x8000, 0x04
WAIT_OBJECT_0, WAIT_TIMEOUT = 0, 258
INVALID_HANDLE = ctypes.c_void_p(-1).value


class ModuleEntry(ctypes.Structure):
    _fields_ = [
        ("dwSize", wt.DWORD),
        ("th32ModuleID", wt.DWORD),
        ("th32ProcessID", wt.DWORD),
        ("GlblcntUsage", wt.DWORD),
        ("ProccntUsage", wt.DWORD),
        ("modBaseAddr", ctypes.c_void_p),
        ("modBaseSize", wt.DWORD),
        ("hModule", wt.HMODULE),
        ("szModule", wt.WCHAR * 256),
        ("szExePath", wt.WCHAR * 260),
    ]


def _declare(name, restype, *argtypes):
    fn = getattr(k32, name)
    fn.restype, fn.argtypes = restype, list(argtypes)


_declare("OpenProcess", wt.HANDLE, wt.DWORD, wt.BOOL, wt.DWORD)
_declare("CloseHandle", wt.BOOL, wt.HANDLE)
_declare("VirtualAllocEx", ctypes.c_void_p, wt.HANDLE, ctypes.c_void_p, ctypes.c_size_t, wt.DWORD, wt.DWORD)
_declare("VirtualFreeEx", wt.BOOL, wt.HANDLE, ctypes.c_void_p, ctypes.c_size_t, wt.DWORD)
_declare(
    "WriteProcessMemory",
    wt.BOOL,
    wt.HANDLE,
    ctypes.c_void_p,
    ctypes.c_void_p,
    ctypes.c_size_t,
    ctypes.POINTER(ctypes.c_size_t),
)
_declare("GetModuleHandleW", wt.HMODULE, wt.LPCWSTR)
_declare("GetModuleHandleExW", wt.BOOL, wt.DWORD, wt.LPCWSTR, ctypes.POINTER(wt.HMODULE))
_declare("GetModuleFileNameW", wt.DWORD, wt.HMODULE, wt.LPWSTR, wt.DWORD)
_declare("GetProcAddress", ctypes.c_void_p, wt.HMODULE, wt.LPCSTR)
_declare(
    "CreateRemoteThread",
    wt.HANDLE,
    wt.HANDLE,
    ctypes.c_void_p,
    ctypes.c_size_t,
    ctypes.c_void_p,
    ctypes.c_void_p,
    wt.DWORD,
    ctypes.POINTER(wt.DWORD),
)
_declare("WaitForSingleObject", wt.DWORD, wt.HANDLE, wt.DWORD)
_declare("GetExitCodeThread", wt.BOOL, wt.HANDLE, ctypes.POINTER(wt.DWORD))
_declare("QueryFullProcessImageNameW", wt.BOOL, wt.HANDLE, wt.DWORD, wt.LPWSTR, ctypes.POINTER(wt.DWORD))
_declare("CreateToolhelp32Snapshot", wt.HANDLE, wt.DWORD, wt.DWORD)
_declare("Module32FirstW", wt.BOOL, wt.HANDLE, ctypes.POINTER(ModuleEntry))
_declare("Module32NextW", wt.BOOL, wt.HANDLE, ctypes.POINTER(ModuleEntry))


# While a process is still loading, its module list is unstable: the snapshot can fail with
# ERROR_BAD_LENGTH (24, MSDN says to retry), come back empty (18) or be cut short (299).
TRANSIENT_SNAPSHOT_ERRORS = frozenset({24, 18, 299})


def _modules(pid, patience=2.0):
    """Modules of a process, retrying briefly while the target is still loading."""
    deadline = time.monotonic() + patience
    while True:
        try:
            return _snapshot_modules(pid)
        except OSError as error:
            if (
                getattr(error, "winerror", None) not in TRANSIENT_SNAPSHOT_ERRORS
                or time.monotonic() >= deadline
            ):
                raise
            time.sleep(0.01)


def _snapshot_modules(pid):
    handle = k32.CreateToolhelp32Snapshot(0x08 | 0x10, pid)
    if handle == INVALID_HANDLE:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        entry = ModuleEntry()
        entry.dwSize = ctypes.sizeof(entry)
        if not k32.Module32FirstW(handle, ctypes.byref(entry)):
            raise ctypes.WinError(ctypes.get_last_error())
        result = []
        while True:
            result.append((entry.szExePath, entry.modBaseAddr))
            if not k32.Module32NextW(handle, ctypes.byref(entry)):
                error = ctypes.get_last_error()
                if error != 18:
                    raise ctypes.WinError(error)
                return result
    finally:
        k32.CloseHandle(handle)


def _loaded_module(pid, path):
    wanted = os.path.normcase(str(Path(path).resolve()))
    return next(
        (base for name, base in _modules(pid) if os.path.normcase(str(Path(name).resolve())) == wanted), 0
    )


def remote_load_library(pid):
    """Resolve the remote export by its owning DLL, including forwarded exports."""
    address = k32.GetProcAddress(k32.GetModuleHandleW("kernel32.dll"), b"LoadLibraryW")
    if not address:
        raise ctypes.WinError(ctypes.get_last_error())
    owner = wt.HMODULE()
    if not k32.GetModuleHandleExW(0x06, ctypes.cast(address, wt.LPCWSTR), ctypes.byref(owner)):
        raise ctypes.WinError(ctypes.get_last_error())
    name = ctypes.create_unicode_buffer(32768)
    if not k32.GetModuleFileNameW(owner, name, len(name)):
        raise ctypes.WinError(ctypes.get_last_error())
    wanted = Path(name.value).name.lower()
    for path, base in _modules(pid):
        if Path(path).name.lower() == wanted:
            return base + address - owner.value
    raise RuntimeError(f"Loader DLL {wanted} not found in target {pid}; check process architecture")


def image_path(pid):
    """Full path of a process's executable, or None when it cannot be read."""
    process = k32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not process:
        return None
    try:
        size = wt.DWORD(32768)
        buffer = ctypes.create_unicode_buffer(size.value)
        return (
            buffer.value if k32.QueryFullProcessImageNameW(process, 0, buffer, ctypes.byref(size)) else None
        )
    finally:
        k32.CloseHandle(process)


def find_pids(name="Overwatch.exe"):
    result = subprocess.run(
        ["tasklist", "/FI", f"IMAGENAME eq {name}", "/FO", "CSV", "/NH"],
        capture_output=True,
        text=True,
        check=True,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    return [
        int(row[1])
        for row in csv.reader(result.stdout.splitlines())
        if len(row) > 1 and row[0].lower() == name.lower()
    ]


def inject(dll_path: str, pid: int, timeout_ms: int = 10000) -> int:
    path = Path(dll_path).resolve(strict=True)
    if not path.is_file():
        raise ValueError(f"DLL path is not a file: {path}")
    if pid <= 0 or not 0 < timeout_ms < 0xFFFFFFFF:
        raise ValueError("PID and timeout must be positive and finite")
    if ctypes.sizeof(ctypes.c_void_p) != 8:
        raise RuntimeError("Use 64-bit Python for the x64 client")
    loaded = _loaded_module(pid, path)
    if loaded:
        return loaded
    loader = remote_load_library(pid)
    dll = (str(path) + "\0").encode("utf-16-le")
    process = k32.OpenProcess(PROCESS_INJECT, False, pid)
    if not process:
        raise ctypes.WinError(ctypes.get_last_error())
    remote, thread, finished = None, None, False
    try:
        remote = k32.VirtualAllocEx(process, None, len(dll), MEM_COMMIT_RESERVE, PAGE_READWRITE)
        if not remote:
            raise ctypes.WinError(ctypes.get_last_error())
        written = ctypes.c_size_t()
        if not k32.WriteProcessMemory(process, remote, dll, len(dll), ctypes.byref(written)):
            raise ctypes.WinError(ctypes.get_last_error())
        if written.value != len(dll):
            raise OSError(f"Incomplete remote write: {written.value}/{len(dll)} bytes")
        thread = k32.CreateRemoteThread(process, None, 0, loader, remote, 0, None)
        if not thread:
            raise ctypes.WinError(ctypes.get_last_error())
        wait = k32.WaitForSingleObject(thread, timeout_ms)
        if wait == WAIT_TIMEOUT:
            raise TimeoutError(f"DLL loader in PID {pid} timed out; remote path retained until process exit")
        if wait != WAIT_OBJECT_0:
            raise ctypes.WinError(ctypes.get_last_error())
        finished = True
        code = wt.DWORD()
        if not k32.GetExitCodeThread(thread, ctypes.byref(code)):
            raise ctypes.WinError(ctypes.get_last_error())
        loaded = _loaded_module(pid, path)
        if not loaded:
            raise RuntimeError(f"DLL did not load in PID {pid} (thread exit {code.value:#x}): {path}")
        return loaded
    finally:
        if remote and (not thread or finished):
            k32.VirtualFreeEx(process, remote, 0, MEM_RELEASE)
        if thread:
            k32.CloseHandle(thread)
        k32.CloseHandle(process)

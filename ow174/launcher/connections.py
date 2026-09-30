"""Which process owns a local TCP connection (Windows), so the Battle.net emulator can tell two games
on one PC apart by the connection each one makes."""

import ctypes
import ctypes.wintypes as wt
import socket

AF_INET = 2
TCP_TABLE_OWNER_PID_ALL = 5

iphlpapi = ctypes.WinDLL("iphlpapi")
iphlpapi.GetExtendedTcpTable.restype = wt.DWORD
iphlpapi.GetExtendedTcpTable.argtypes = [
    ctypes.c_void_p,
    ctypes.POINTER(wt.DWORD),
    wt.BOOL,
    wt.ULONG,
    ctypes.c_int,
    wt.ULONG,
]


class TcpRow(ctypes.Structure):
    """MIB_TCPROW_OWNER_PID. Ports are in network order in the low 16 bits."""

    _fields_ = [
        ("state", wt.DWORD),
        ("local_addr", wt.DWORD),
        ("local_port", wt.DWORD),
        ("remote_addr", wt.DWORD),
        ("remote_port", wt.DWORD),
        ("pid", wt.DWORD),
    ]


def connection_owner(local_port: int, remote_port: int) -> int | None:
    """The PID of the process whose IPv4 TCP connection goes from local_port to remote_port."""
    size = wt.DWORD(0)
    iphlpapi.GetExtendedTcpTable(None, ctypes.byref(size), False, AF_INET, TCP_TABLE_OWNER_PID_ALL, 0)
    buffer = ctypes.create_string_buffer(size.value)
    if iphlpapi.GetExtendedTcpTable(buffer, ctypes.byref(size), False, AF_INET, TCP_TABLE_OWNER_PID_ALL, 0):
        return None
    count = wt.DWORD.from_buffer(buffer).value
    rows = (TcpRow * count).from_buffer(buffer, ctypes.sizeof(wt.DWORD))
    for row in rows:
        if _port(row.local_port) == local_port and _port(row.remote_port) == remote_port:
            return row.pid
    return None


def _port(value: int) -> int:
    return socket.ntohs(value & 0xFFFF)

"""Run the Battle.net emulator next to the lobby server, in a background thread.

Ports:
  1119   the port the client dials (--BNetServer=127.0.0.1:1119). We accept and hold the connection
         open without answering, so the client's TLS stream stays alive until the injected relay DLL
         swaps it for a plaintext pipe.
  21119  BGS RPC over WebSocket; the relay pipes the plaintext here.
  6969   the web-auth login form.
"""

import asyncio
import logging
import threading
from concurrent.futures import Future

from ow174.bnet.rpc_server import BNetRpcServer
from ow174.bnet.webauth import start_web_server

log = logging.getLogger("ow174.bnet")

STALL_PORT = 1119
RPC_PORT = 21119
WEB_PORT = 6969


def start_bnet(
    host: str = "127.0.0.1", ports: tuple[int, int, int] = (STALL_PORT, RPC_PORT, WEB_PORT)
) -> None:
    """Start the emulator in a background thread and return once its (stall, RPC, web) ports are bound.

    Raises OSError when a port is taken.
    """
    ready: Future = Future()
    threading.Thread(target=asyncio.run, args=(_serve(host, ports, ready),), daemon=True, name="bnet").start()
    ready.result()


async def _serve(host: str, ports: tuple[int, int, int], ready: Future) -> None:
    stall_port, rpc_port, web_port = ports
    web = stall = None
    try:
        web = start_web_server(host, web_port)
        stall = await asyncio.start_server(_hold_open, host, stall_port)
        rpc = await BNetRpcServer(host, rpc_port).start()
    except OSError as error:
        if stall:
            stall.close()
        if web:
            web.shutdown()
            web.server_close()
        ready.set_exception(error)
        return
    log.info("[stall] holding client dials on %s:%d", host, stall_port)
    ready.set_result(None)
    async with stall, rpc:
        await asyncio.Future()


async def _hold_open(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    """Keep the client's original TLS connection open, discarding what it sends, until it closes."""
    peer = writer.get_extra_info("peername")
    log.info("[stall] %s connected (holding open for the relay swap)", peer)
    try:
        while await reader.read(4096):
            pass
    except (ConnectionError, asyncio.CancelledError):
        pass
    finally:
        writer.close()
        log.info("[stall] %s closed", peer)

"""Run the local Battle.net emulator.

Ports:
  1119  "stall" listener - the port the client dials (--BNetServer=127.0.0.1:1119). We accept and hold
        the connection open without answering, so the client's TLS stream object stays alive long enough
        for the injected relay DLL to swap it to a plaintext pipe.
  21119 BGS RPC over WebSocket - the relay pipes the stripped plaintext here.
  6969  web-auth login form.

Launch the client WITHOUT --tank_TournamentMode, WITH --BNetServer=127.0.0.1:1119, and inject the relay
DLL. The client logs in, gets a ReferralInfo to 127.0.0.1:3724, and connects to the lobby server.
"""
import argparse
import asyncio

try:
    from .rpc_server import BNetRpcServer
    from .webauth import start_web_server
except ImportError:  # run as a script
    from rpc_server import BNetRpcServer
    from webauth import start_web_server


def _logger(msg: str):
    print(msg, flush=True)


async def _stall_client(reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
    """Hold a client's real (pre-swap) TLS connection open; drain and discard until it closes."""
    peer = writer.get_extra_info("peername")
    _logger(f"[stall] {peer} connected (holding open for relay swap)")
    try:
        while True:
            data = await reader.read(4096)
            if not data:
                break
    except (ConnectionError, asyncio.CancelledError):
        pass
    finally:
        try:
            writer.close()
        except Exception:
            pass
        _logger(f"[stall] {peer} closed")


async def _run(args):
    start_web_server(args.host, args.web_port, logger=_logger)
    stall = await asyncio.start_server(_stall_client, args.host, args.stall_port)
    _logger(f"[stall] holding client dials on {args.host}:{args.stall_port}")
    server = BNetRpcServer(host=args.host, port=args.rpc_port, logger=_logger)
    async with stall:
        await server.serve()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--stall-port", type=int, default=1119)
    parser.add_argument("--rpc-port", type=int, default=21119)
    parser.add_argument("--web-port", type=int, default=6969)
    args = parser.parse_args()
    try:
        asyncio.run(_run(args))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()

"""The one entry point, `py -m ow174` (START.bat): start the servers, then the game.

Modes:
  retail      the full main menu with the lobby hero: the lobby, a local Battle.net and the game
              with the relay DLL, which strips TLS from its Battle.net connection (default)
  tournament  a simpler menu without the hero; the game dials the lobby directly, no relay needed
  server      only the lobby server, for example for players on the LAN (--host 0.0.0.0)
"""

import argparse
import logging
import sys
import threading
from pathlib import Path

from ow174.accounts.profile import load_or_create_profile
from ow174.dashboard.server import start_dashboard
from ow174.launcher import LaunchError
from ow174.launcher.game import close_running_copy, find_game, inject_relay, start_game
from ow174.launcher.relay import ensure_relay_dll
from ow174.launcher.requirements import ensure_requirements
from ow174.lobby.research import watch_inject_file
from ow174.lobby.server import LobbyServer
from ow174.lobby.settings import Settings
from ow174.log import setup_logging
from ow174.paths import LOG_FILE, Paths

log = logging.getLogger("ow174")

MODES = ("retail", "tournament", "server")
MODE_MENU = """
Which mode?
  1  Retail: the full main menu with the hero in the lobby
  2  Tournament: a simpler menu; use it if the login fails
  3  Server only: you start the game yourself
"""
BNET_ADDRESS = "127.0.0.1:1119"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    defaults = Settings()
    parser = argparse.ArgumentParser(
        prog="START.bat", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--mode", choices=MODES, help="what to start; without it you are asked")
    parser.add_argument(
        "--game-exe", type=Path, help="Overwatch.exe to start (default: the one picked before)"
    )
    parser.add_argument(
        "--locale",
        default="auto",
        help="game language such as enUS; 'auto' picks one the build has, 'none' none",
    )
    parser.add_argument(
        "--timeout", type=float, default=30.0, help="seconds to wait for the game (default: 30)"
    )
    parser.add_argument(
        "--host", default=defaults.host, help="lobby address (default: 127.0.0.1; 0.0.0.0 for LAN)"
    )
    parser.add_argument("--port", type=int, default=defaults.port, help="lobby port (default: 3724)")
    parser.add_argument(
        "--dashboard-port",
        type=int,
        default=defaults.dashboard_port,
        help="dashboard port (default: 3725, 0: off)",
    )
    parser.add_argument(
        "--game-port",
        type=int,
        default=defaults.game_port,
        help="first UDP port of game instances (default: 3730, 0: off)",
    )
    parser.add_argument(
        "--save", type=Path, default=defaults.paths.template, help="template profile for new accounts"
    )
    return parser.parse_args(argv)


def ask_mode() -> str:
    """Ask for the mode in the console. Enter picks retail."""
    print(MODE_MENU)
    while True:
        answer = input("Press 1, 2 or 3, then Enter (just Enter for 1): ").strip()
        if answer in ("", "1", "2", "3"):
            return MODES[int(answer or "1") - 1]


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.mode is None:
        args.mode = ask_mode() if sys.stdin.isatty() else "retail"
    setup_logging(LOG_FILE)
    try:
        run(args)
    except LaunchError as error:
        log.error("%s", error)
        return 1
    except KeyboardInterrupt:
        log.info("Server shutdown.")
    return 0


def run(args: argparse.Namespace) -> None:
    settings = Settings(
        host=args.host,
        port=args.port,
        dashboard_port=args.dashboard_port,
        game_port=args.game_port,
        paths=Paths(template=args.save),
    )
    game = relay = None
    if args.mode != "server":
        game = find_game(args.game_exe)
        close_running_copy(game)
    if args.mode == "retail":
        ensure_requirements()
        relay = ensure_relay_dll()

    load_or_create_profile(settings.paths.template)
    server = LobbyServer(settings)
    listener = _bind(server)
    if settings.dashboard_port > 0:
        start_dashboard(server, port=settings.dashboard_port)
    threading.Thread(
        target=watch_inject_file, args=(server, settings.paths.inject_file), daemon=True, name="inject"
    ).start()
    if args.mode == "retail":
        _start_bnet()
    _log_banner(server)

    if args.mode == "retail":
        process = start_game(game, [f"--BNetServer={BNET_ADDRESS}"], args.locale)
        base = inject_relay(process, relay, args.timeout)
        log.info("[+] Relay loaded into Overwatch (PID %d) at 0x%X.", process.pid, base)
    elif args.mode == "tournament":
        start_game(game, ["--tank_TournamentMode", f"--lobbyServer=127.0.0.1:{settings.port}"], args.locale)
    else:
        log.info(
            "[+] Start the game with: Overwatch.exe --tank_TournamentMode --lobbyServer=%s:%d",
            settings.host,
            settings.port,
        )
    log.info("Keep this window open while you play; closing it stops the server.")
    server.serve_forever(listener)


def _bind(server: LobbyServer):
    try:
        return server.listen()
    except OSError as error:
        raise LaunchError(
            f"Port {server.settings.port} is taken ({error}). "
            "Is the server already running in another window?"
        ) from error


def _start_bnet() -> None:
    from ow174.bnet.service import start_bnet  # needs the packages ensure_requirements installs

    try:
        start_bnet()
    except OSError as error:
        raise LaunchError(
            f"The Battle.net ports are taken ({error}). Is the server already running in another window?"
        ) from error


def _log_banner(server: LobbyServer) -> None:
    settings = server.settings
    groups = server.schemas.groups
    log.info("OVERWATCH 1.74 LOBBY SERVER")
    log.info(" [*] Bind:           %s:%d", settings.host, settings.port)
    log.info(" [*] Accounts:       %s (profiles/)", ", ".join(server.accounts.all_saved()) or "none yet")
    log.info(" [*] New accounts:   copy of %s", settings.paths.template.name)
    log.info(" [*] Schemas:        %d messages in %d protocols", sum(map(len, groups.values())), len(groups))
    log.info(" [*] Log file:       %s", LOG_FILE)

"""The retail games one server starts, each with its own account.

The first game plays the dashboard's account, so "Play as" can switch it. A second game plays the
account it was started for. The Battle.net emulator and the lobby tell the games apart by the process
that owns each connection.
"""

import logging
import subprocess
import threading
from pathlib import Path

from ow174.launcher import LaunchError
from ow174.launcher.connections import connection_owner
from ow174.launcher.game import inject_relay, start_game

log = logging.getLogger("ow174.launcher")

BNET_ADDRESS = "127.0.0.1:1119"
# A second game must not take the screen from the first in exclusive fullscreen. The client only masks
# that mode while it runs; the display setting it saves stays as it was.
SECOND_GAME_ARGUMENTS = ("--tank_NoFullScreen=1",)


class RetailGames:
    def __init__(self, game: Path, relay: Path, locale: str, timeout: float) -> None:
        self._game = game
        self._relay = relay
        self._locale = locale
        self._timeout = timeout
        self._lock = threading.Lock()
        self._second: dict[int, tuple[subprocess.Popen, str]] = {}  # PID -> (game, account name)

    def start(self, account: str | None = None) -> subprocess.Popen:
        """Start a game with the relay in it: for `account`, or for the dashboard's account."""
        arguments = [f"--BNetServer={BNET_ADDRESS}", *(SECOND_GAME_ARGUMENTS if account else ())]
        process = start_game(self._game, arguments, self._locale)
        if account:
            with self._lock:
                self._second[process.pid] = (process, account)
        try:
            base = inject_relay(process, self._relay, self._timeout)
        except LaunchError:
            process.kill()  # without the relay the game waits at its login screen forever
            raise
        playing = f" for {account}" if account else ""
        log.info("[+] Relay loaded into Overwatch (PID %d) at 0x%X%s.", process.pid, base, playing)
        return process

    def account_of(self, peer_port: int, server_port: int) -> str | None:
        """The account of the second game that owns the connection from peer_port to server_port."""
        pid = connection_owner(peer_port, server_port)
        with self._lock:
            game, account = self._second.get(pid, (None, None))
        return account if game is not None and game.poll() is None else None

    def second_accounts(self) -> set[str]:
        """Accounts a second game is playing."""
        with self._lock:
            return {account for game, account in self._second.values() if game.poll() is None}

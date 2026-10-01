"""How the lobby server is configured."""

from dataclasses import dataclass, field

from ow174.paths import Paths


@dataclass(frozen=True)
class Settings:
    host: str = "127.0.0.1"  # 0.0.0.0 lets players on the LAN connect
    port: int = 3724
    dashboard_port: int = 3725  # 0 turns the dashboard off
    game_port: int = 3730  # the game server's UDP port; 0 turns it off
    # The address games are sent to for a match. Empty: the address the game reached the lobby at,
    # which is wrong only behind a router's port forward (then give the public address).
    game_host: str = ""
    # 0: a match starts with full teams, as the queue's mode has them. N: it starts as soon as N
    # players search the same queue (for tests with fewer players).
    test_players: int = 0
    paths: Paths = field(default_factory=Paths)

"""How the lobby server is configured."""

from dataclasses import dataclass, field

from ow174.paths import Paths


@dataclass(frozen=True)
class Settings:
    host: str = "127.0.0.1"  # 0.0.0.0 lets players on the LAN connect
    port: int = 3724
    dashboard_port: int = 3725  # 0 turns the dashboard off
    game_port: int = 3730  # first UDP port for game instances; 0 turns instances off
    paths: Paths = field(default_factory=Paths)

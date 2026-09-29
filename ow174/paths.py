"""Where things live on disk. Everything is relative to the repository folder."""

from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
WEB_DIR = Path(__file__).resolve().parent / "dashboard" / "web"
LOGS_DIR = ROOT / "logs"
LOG_FILE = LOGS_DIR / "ow174.log"
GAME_LOG_FILE = LOGS_DIR / "game.log"
RELAY_DLL = ROOT / "relay" / "owwfd_relay.dll"
GAME_PATH_FILE = ROOT / "game_path.txt"  # the Overwatch.exe picked on the first start
REQUIREMENTS = ROOT / "requirements.txt"


@dataclass(frozen=True)
class Paths:
    """Files the lobby server reads and writes. Tests point these at a temporary folder."""

    profiles: Path = ROOT / "profiles"
    template: Path = ROOT / "profile.json"
    matches: Path = LOGS_DIR / "matches"
    client_log: Path = ROOT / "client_msgs.log"
    inject_file: Path = ROOT / "inject.jsonl"

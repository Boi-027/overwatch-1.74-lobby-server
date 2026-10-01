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
SERVER_ADDRESS_FILE = ROOT / "server_address.txt"  # the server joined last time
PLAYER_NAME_FILE = ROOT / "player_name.txt"  # the name used to join last time
REQUIREMENTS = ROOT / "requirements.txt"


@dataclass(frozen=True)
class Paths:
    """Files the lobby server reads and writes. Tests point these at a temporary folder."""

    profiles: Path = ROOT / "profiles"
    template: Path = ROOT / "profile.json"
    default_account: Path = ROOT / "default_account.txt"  # the account a started game logs in as
    client_log: Path = ROOT / "client_msgs.log"
    inject_file: Path = ROOT / "inject.jsonl"
    log_file: Path = LOG_FILE

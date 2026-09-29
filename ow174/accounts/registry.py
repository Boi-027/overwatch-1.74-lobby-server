"""Accounts: every typed name is an account with its own saved profile and a stable account id."""

import shutil
import threading
import zlib
from dataclasses import dataclass
from pathlib import Path

from ow174.accounts.profile import Profile, load_or_create_profile, save_profile
from ow174.jam.values import id16

BOT_NAME = "Bot"


def account_id_for(name: str) -> int:
    """A stable id derived from the name, so a player keeps their id between runs."""
    return 0x10000000 | (zlib.crc32(name.strip().lower().encode("utf-8")) & 0x0FFFFFFF)


@dataclass(eq=False)
class Account:
    name: str
    account_lo: int
    profile: Profile
    path: Path | None = None
    virtual: bool = False  # the built-in bot has no file

    @property
    def account(self) -> dict:
        """The account id as a protocol value."""
        return id16(self.account_lo)

    def save(self) -> None:
        if self.path and not self.virtual:
            save_profile(self.profile, self.path)


class Accounts:
    """The saved profiles, loaded on demand. New accounts start as a copy of the template profile."""

    def __init__(self, profiles_dir: Path, template: Path) -> None:
        self.directory = profiles_dir
        self.template = template
        self.directory.mkdir(parents=True, exist_ok=True)
        self._by_name: dict[str, Account] = {}
        self._lock = threading.Lock()
        bot_profile = Profile(player_name=BOT_NAME, level=250, endorsement_level=5)
        self.bot = Account(BOT_NAME, account_id_for(BOT_NAME), bot_profile, virtual=True)

    def _path_for(self, name: str) -> Path:
        """The profile file for a name, keeping only characters that are safe in a file name."""
        safe_chars = []
        for char in name:
            if char.isalnum() or char in "-_ ":
                safe_chars.append(char)
        file_name = "".join(safe_chars).strip() or "player"
        return self.directory / f"{file_name}.json"

    def get(self, name: str) -> Account:
        """The account for a name, creating its profile file on first use."""
        key = name.strip().lower()
        with self._lock:
            if key in self._by_name:
                return self._by_name[key]
            path = self._path_for(name)
            if not path.exists() and self.template.exists():
                shutil.copyfile(self.template, path)
            profile = load_or_create_profile(path)
            profile.player_name = name.strip()
            save_profile(profile, path)
            account = Account(profile.player_name, account_id_for(profile.player_name), profile, path)
            self._by_name[key] = account
            return account

    def all_saved(self) -> list[str]:
        """Names of every saved profile plus the accounts opened this run."""
        names = {path.stem for path in self.directory.glob("*.json")}
        for account in self._by_name.values():
            names.add(account.name)
        return sorted(names, key=str.lower)

    def by_id(self, account_lo: int) -> Account | None:
        """An account opened this run, or the bot. Returns None for any other id."""
        if account_lo == self.bot.account_lo:
            return self.bot
        for account in self._by_name.values():
            if account.account_lo == account_lo:
                return account
        return None

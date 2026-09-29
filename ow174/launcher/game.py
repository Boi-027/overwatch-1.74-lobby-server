"""Finding Overwatch.exe, picking its language and starting it on our servers."""

import ctypes
import logging
import os
import re
import subprocess
import time
from pathlib import Path

from ow174.launcher import LaunchError, inject
from ow174.paths import GAME_LOG_FILE, GAME_PATH_FILE, ROOT

log = logging.getLogger("ow174.launcher")

GAME_EXE = "Overwatch.exe"
UNPACKED_GAME_PATHS = (
    ROOT / "game" / "Overwatch" / "_retail_" / GAME_EXE,
    ROOT / "game" / "_retail_" / GAME_EXE,
    ROOT / "game" / GAME_EXE,
)
LOCALE_TAG = re.compile(r"\b[a-z]{2}[A-Z]{2}\b")
POLL_SECONDS = 0.05


# --- which game ------------------------------------------------------------------------------------


def find_game(explicit: Path | None = None, saved: Path = GAME_PATH_FILE) -> Path:
    """The game to start: the given path, the one picked last time, an unpacked copy in game/, or one
    the player picks now (remembered for next time)."""
    if explicit is not None:
        if not is_game(explicit):
            raise LaunchError(f"{explicit} is not {GAME_EXE}.")
        return explicit.resolve()
    if saved.is_file():
        # utf-8-sig: older launchers saved the path with a BOM
        remembered = Path(saved.read_text(encoding="utf-8-sig").strip())
        if is_game(remembered):
            return remembered.resolve()
    for candidate in UNPACKED_GAME_PATHS:
        if is_game(candidate):
            return candidate.resolve()
    picked = pick_game()
    saved.write_text(str(picked), encoding="utf-8")
    return picked


def is_game(path: Path) -> bool:
    return path.name.lower() == GAME_EXE.lower() and path.is_file()


def pick_game() -> Path:
    """Ask the player for Overwatch.exe in a file dialog."""
    import tkinter
    from tkinter import filedialog

    log.info("Select the game's %s (the game itself, not a shortcut or a launcher).", GAME_EXE)
    root = tkinter.Tk()
    root.withdraw()
    root.attributes("-topmost", True)
    try:
        while True:
            chosen = filedialog.askopenfilename(
                title=f"Select {GAME_EXE}", filetypes=[(GAME_EXE, GAME_EXE)], parent=root
            )
            if not chosen:
                raise LaunchError(f"No game selected. Start again and pick {GAME_EXE}.")
            if is_game(Path(chosen)):
                return Path(chosen).resolve()
            log.warning("That is not %s. Pick the file with exactly that name.", GAME_EXE)
    finally:
        root.destroy()


# --- language --------------------------------------------------------------------------------------


def game_locale(game: Path, requested: str = "auto") -> str | None:
    """The --tank_Locale to pass: 'auto' detects one, 'none' passes nothing, anything else as given."""
    if requested == "none":
        return None
    if requested != "auto":
        return requested
    return choose_locale(installed_locales(game), system_locale())


def choose_locale(installed: set[str], system: str | None) -> str | None:
    """A build without the language the game picks by itself shows a black screen. So pass the system
    language when the build has it, else a language with the same first letters, else enUS."""
    if not installed:
        return None
    if system in installed:
        return system
    if system:
        same_language = sorted(code for code in installed if code[:2] == system[:2])
        if same_language:
            return same_language[0]
    return "enUS" if "enUS" in installed else None


def installed_locales(game: Path) -> set[str]:
    """Language codes in the tags of the game's .build.info (next to the _retail_ folder)."""
    for folder in (game.parent.parent, game.parent):
        info = folder / ".build.info"
        if not info.is_file():
            continue
        lines = info.read_text(encoding="utf-8", errors="replace").splitlines()
        if len(lines) < 2:
            continue
        header = [column.split("!")[0] for column in lines[0].split("|")]
        if "Tags" in header:
            row = lines[1].split("|")
            index = header.index("Tags")
            return set(LOCALE_TAG.findall(row[index] if index < len(row) else ""))
    return set()


def system_locale() -> str | None:
    """The Windows user language as a game code, e.g. 'ru-RU' -> 'ruRU'."""
    try:
        buffer = ctypes.create_unicode_buffer(85)
        if ctypes.windll.kernel32.GetUserDefaultLocaleName(buffer, len(buffer)):
            return buffer.value.replace("-", "") or None
    except (AttributeError, OSError):
        pass
    return None


# --- running copies --------------------------------------------------------------------------------


def close_running_copy(game: Path, patience: float = 6.0) -> None:
    """Close a copy of this game left from an earlier run (for example at a 'Disconnected' screen).
    Any other Overwatch install, such as Overwatch 2, is left alone."""
    try:
        ours, others = running_copies(game)
    except (OSError, subprocess.SubprocessError) as error:
        raise LaunchError(f"Cannot check for a running Overwatch: {error}") from error
    if others:
        log.info("Another Overwatch is running (PID %s); leaving it alone.", ", ".join(map(str, others)))
    # A normal close first: the game writes its settings file (Documents\Overwatch\Settings) only
    # when it exits by itself. It is forced only if it is still running after that.
    for force in (False, True):
        if not ours:
            return
        for pid in ours:
            log.info(
                "%s the Overwatch left from the last run (PID %d)...", "Forcing" if force else "Closing", pid
            )
            subprocess.run(["taskkill", *(["/F"] if force else []), "/PID", str(pid)], capture_output=True)
        deadline = time.monotonic() + patience
        while running_copies(game)[0] and time.monotonic() < deadline:
            time.sleep(0.3)
        ours = running_copies(game)[0]


def running_copies(game: Path) -> tuple[list[int], list[int]]:
    """PIDs of running Overwatch.exe as (this copy of the game, any other copy)."""
    ours, others = [], []
    for pid in inject.find_pids(GAME_EXE):
        (ours if _same_file(inject.image_path(pid), game) else others).append(pid)
    return ours, others


def _same_file(path: str | None, other: Path) -> bool:
    if not path:
        return False
    try:
        return os.path.samefile(path, other)
    except OSError:
        return os.path.normcase(os.path.abspath(path)) == os.path.normcase(os.path.abspath(other))


# --- starting --------------------------------------------------------------------------------------


def start_game(game: Path, arguments: list[str], locale: str = "auto") -> subprocess.Popen:
    command = [str(game), *arguments]
    language = game_locale(game, locale)
    if language:
        command.append(f"--tank_Locale={language}")
        log.info("Game language: %s", language)
    GAME_LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    with GAME_LOG_FILE.open("ab") as output:
        return subprocess.Popen(command, cwd=game.parent, stdout=output, stderr=subprocess.STDOUT)


def inject_relay(process: subprocess.Popen, dll: Path, timeout: float) -> int:
    """Load the relay into the game as soon as its loader is ready; the module base address."""
    deadline = time.monotonic() + timeout
    last_error = "the loader modules are not initialized"
    while True:
        if process.poll() is not None:
            raise LaunchError(f"Overwatch exited with code {process.returncode} before the relay was loaded.")
        try:
            if inject.remote_load_library(process.pid):
                break
        except (OSError, RuntimeError) as error:
            last_error = str(error)
        if time.monotonic() >= deadline:
            raise LaunchError(f"Overwatch did not become ready for the relay: {last_error}")
        time.sleep(POLL_SECONDS)
    try:
        return inject.inject(str(dll), process.pid, int(timeout * 1000))
    except (OSError, RuntimeError, ValueError) as error:
        raise LaunchError(f"Could not load the relay into Overwatch: {error}") from error

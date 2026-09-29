"""The Python packages of the Battle.net emulator, pinned in requirements.txt and installed on demand."""

import logging
import subprocess
import sys
from importlib import metadata
from pathlib import Path

from ow174.launcher import LaunchError
from ow174.paths import REQUIREMENTS

log = logging.getLogger("ow174.launcher")


def missing_requirements(requirements: Path = REQUIREMENTS) -> list[str]:
    """Lines of requirements.txt that are not installed at exactly the pinned version."""
    missing = []
    for line in requirements.read_text(encoding="utf-8").splitlines():
        line = line.split("#")[0].strip()
        if not line:
            continue
        name, pinned, version = line.partition("==")
        try:
            if not pinned or metadata.version(name.strip()) != version.strip():
                missing.append(line)
        except metadata.PackageNotFoundError:
            missing.append(line)
    return missing


def ensure_requirements(requirements: Path = REQUIREMENTS) -> None:
    """Install what is missing with pip, so the network is only used on the first start."""
    missing = missing_requirements(requirements)
    if not missing:
        return
    log.info("Installing Python packages: %s", ", ".join(missing))
    command = [
        sys.executable,
        "-m",
        "pip",
        "install",
        "-q",
        "--disable-pip-version-check",
        "-r",
        str(requirements),
    ]
    if subprocess.run(command).returncode != 0:
        raise LaunchError(
            "Could not install the Python packages. Check the internet connection and start again."
        )

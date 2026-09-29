"""Everything START.bat needs before the game can connect: Python packages, the relay DLL, the game
itself and its launch."""


class LaunchError(RuntimeError):
    """A problem the player has to fix; the message says how."""

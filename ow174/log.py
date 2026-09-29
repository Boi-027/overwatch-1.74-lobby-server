"""Logging for the servers: one plain, timestamped line per event, on the console and in a file."""

import contextlib
import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

LOG_FILE_BYTES = 2_000_000
LOG_FILE_BACKUPS = 2


class _Formatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        marker = "! " if record.levelno >= logging.WARNING else ""
        line = f"{self.formatTime(record, '%H:%M:%S')} {marker}{record.getMessage()}"
        if record.exc_info:
            line += "\n" + self.formatException(record.exc_info)
        return line


def setup_logging(log_file: Path | None = None, level: int = logging.INFO) -> None:
    """Send the ow174 loggers to the console and, when given, to a rotating log file. Call once."""
    stream = sys.stdout
    # a player name the console cannot print must not crash the server
    with contextlib.suppress(AttributeError, ValueError):
        stream.reconfigure(line_buffering=True, errors="replace")
    handlers: list[logging.Handler] = [logging.StreamHandler(stream)]
    if log_file is not None:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        handlers.append(
            RotatingFileHandler(
                log_file, maxBytes=LOG_FILE_BYTES, backupCount=LOG_FILE_BACKUPS, encoding="utf-8"
            )
        )
    logger = logging.getLogger("ow174")
    for handler in handlers:
        handler.setFormatter(_Formatter())
    logger.handlers[:] = handlers
    logger.setLevel(level)
    logger.propagate = False

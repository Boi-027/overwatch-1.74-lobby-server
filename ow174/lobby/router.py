"""Maps (protocol group, message id) to the function that handles it."""

from collections.abc import Callable
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ow174.lobby.session import Session

Handler = Callable[["Session", dict], None]


class Router:
    def __init__(self) -> None:
        self._handlers: dict[tuple[int, int], Handler] = {}

    def add(self, crc: int, msg_id: int, handler: Handler) -> None:
        if (crc, msg_id) in self._handlers:
            raise ValueError(f"message {crc:08X}/{msg_id} already has a handler")
        self._handlers[(crc, msg_id)] = handler

    def on(self, crc: int, *msg_ids: int) -> Callable[[Handler], Handler]:
        """Decorator: register the function for one or more message ids of a group."""

        def register(handler: Handler) -> Handler:
            for msg_id in msg_ids:
                self.add(crc, msg_id, handler)
            return handler

        return register

    def include(self, other: "Router") -> None:
        for (crc, msg_id), handler in other._handlers.items():
            self.add(crc, msg_id, handler)

    def get(self, crc: int, msg_id: int) -> Handler | None:
        return self._handlers.get((crc, msg_id))

    def __len__(self) -> int:
        return len(self._handlers)

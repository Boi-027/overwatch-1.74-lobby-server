"""Message handlers, one module per topic. Each module exposes a `routes` Router."""

from ow174.lobby.handlers import chat, friends, gallery, login, lookups, matchmaking, party, settings
from ow174.lobby.router import Router


def build_router() -> Router:
    """A router with every handler registered."""
    router = Router()
    for module in (chat, friends, gallery, login, lookups, matchmaking, party, settings):
        router.include(module.routes)
    return router

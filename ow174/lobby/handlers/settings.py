"""Player settings the client saves and expects back at login in message 20802."""

from functools import partial

from ow174.jam.groups import SOCIAL_OUT
from ow174.lobby.router import Router
from ow174.lobby.session import Session

routes = Router()

# Messages 22200-22203 each save one block of 20802. The value is the 20802 field it fills.
BLOCKS = {22200: "+0x78", 22201: "+0x108", 22202: "+0x10D", 22203: "+0x120"}
# Message 22204 saves only the changed entries of the key/value settings list in this field.
VALUES_FIELD = "+0x130"


def save_block(session: Session, value: dict, field: str) -> None:
    session.profile.settings[field] = value.get("+0x78")
    session.save()
    session.log(f"[<<<] Settings {field} saved")


def _register_block_handlers() -> None:
    for msg_id, field in BLOCKS.items():
        routes.add(SOCIAL_OUT, msg_id, partial(save_block, field=field))


_register_block_handlers()


@routes.on(SOCIAL_OUT, 22204)
def save_values(session: Session, value: dict) -> None:
    """Merge the changed entries into the saved list, matching them by setting id."""
    settings = session.profile.settings
    by_setting_id = {}
    for entry in settings.get(VALUES_FIELD, []):
        by_setting_id[entry["+0x8"]] = entry
    changed = value.get("+0x78") or []
    for entry in changed:
        by_setting_id[entry.get("+0x8")] = entry
    settings[VALUES_FIELD] = list(by_setting_id.values())
    session.save()
    session.log(f"[<<<] {len(changed)} setting values saved")

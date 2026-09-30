"""What the client saves on the server and expects back at login: its settings (22204, sent back in
20802), interface states such as dialogs already seen (22207, sent back in 20812) and the competitive
season intros it showed (36200)."""

from functools import partial

from ow174.jam.groups import RANKED_OUT, SOCIAL_OUT
from ow174.lobby.router import Router
from ow174.lobby.session import Session
from ow174.services.social import SOCIAL_SETTINGS

routes = Router()

# Messages 22200-22203 each save one block of 20802. The value is the 20802 field it fills.
BLOCKS = {22200: "+0x78", 22201: "+0x108", 22202: "+0x10D", 22203: "+0x120"}
# Message 22204 saves only the changed entries of the key/value settings list in this field.
VALUES_FIELD = "+0x130"


def save_block(session: Session, value: dict, field: str) -> None:
    session.profile.settings[field] = value.get("+0x78")
    session.save()
    session.log(f"[<<<] Settings {field} saved")
    if field == SOCIAL_SETTINGS:
        # "Members can invite" lives in this block; the members' party state shows it (+0x94).
        party = session.server.social.parties.get(session.account.account_lo)
        if party is not None and party.leader is session.account and len(party.members) > 1:
            session.server.notify_party(party)


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


@routes.on(SOCIAL_OUT, 22207)
def save_ux_state(session: Session, value: dict) -> None:
    """One interface state (a dialog seen, "don't show again", ...), sent back in 20812 at login."""
    index, state = value.get("+0x78", 0), value.get("+0x7C", 0)
    states = session.profile.ux_states
    if states.get(str(index)) == state:
        return
    states[str(index)] = state
    session.save()
    session.log(f"[<<<] Interface state {index} = {state} saved")


@routes.on(RANKED_OUT, 36200)
def season_intro_seen(session: Session, value: dict) -> None:
    """The player closed a competitive season's intro; the ranked state says so from now on
    (card +0x2C, set by the client itself when it sends this, 0x7FF789728230)."""
    card = value.get("+0x78", 0)
    seen = session.profile.seasons_seen
    if card in seen:
        return
    seen.append(card)
    session.save()
    session.log(f"[<<<] Season intro 0x{card:016X} seen")

"""Login: the first message of a session, and the follow-ups that need the main menu to exist."""

import threading
import time

from ow174.accounts.registry import Account
from ow174.content import Identity
from ow174.jam.groups import CHAT_IN, FRIENDS, LOBBY, OUT_CONNECT, PERMISSIONS
from ow174.lobby.router import Router
from ow174.lobby.session import Session, without_party_state

routes = Router()

# Some client systems only exist once the main menu is up. The client drops 55500 (account
# features, which gate chat) on the first login after the game starts, and it writes 20802 (player
# settings) into the settings system without checking that it exists. So both are sent a few
# seconds after login.
MENU_READY_DELAYS = ((3, True), (7, False))  # (seconds after login, also send the settings)


@routes.on(OUT_CONNECT, 21800)
def login(session: Session, value: dict) -> None:
    server = session.server
    typed_name = (value.get("+0x78") or "").strip()
    # The retail frontend has no name screen and sends no name, so it gets the dashboard's account.
    account = server.accounts.get(typed_name) if typed_name else server.dashboard_account()
    _take_over_account(session, account)
    session.log(f"[<<<] Login as '{account.name}' (account 0x{account.account_lo:X})")

    earned = server.content.celebrations.claim_rewards(session.profile)
    if earned:
        session.save()
    messages = _login_messages(session, earned)
    sent = session.send_all(messages)
    session.logged_in = True
    for guid in earned:
        session.log(f"[>>>] Challenge reward: {server.items.describe(guid)}")
    _log_login_summary(session, sent, len(messages))

    for other in list(server.social.sessions.values()):
        if other is not session:
            other.send_social()
    threading.Thread(target=_after_menu_ready, args=(session,), daemon=True).start()


def _take_over_account(session: Session, account: Account) -> None:
    """Bind the account to this session and disconnect any older session that had it."""
    server = session.server
    session.account = account
    session.ident = Identity.create(account.account_lo, session.channel.seq)
    server.selected = account
    previous = server.social.sessions.get(account.account_lo)
    if previous is not None and previous is not session:
        previous.log("[>>>] Replaced by a new login")
        previous.disconnect()
    server.social.sessions[account.account_lo] = session


def _login_messages(session: Session, earned: list[int]) -> list[tuple]:
    server = session.server
    content = server.content
    messages = without_party_state(content.login_messages(session.profile, session.ident))
    messages += session.party_messages()
    messages.append((FRIENDS, 27100, server.social.friends_state(session.account)))
    messages.append((CHAT_IN, 20402, {"+0x78": server.social.general}))
    for guid in earned:
        messages.append(content.collection.unlock_granted(guid))
    return messages


def _log_login_summary(session: Session, sent: int, total: int) -> None:
    profile = session.profile
    online = []
    for account in session.server.social.online():
        online.append(account.name)
    session.log(
        f"[>>>] Sent {sent}/{total} login messages (Level {profile.level}, "
        f"Credits {profile.credits}, Boxes {len(profile.loot_boxes)}, "
        f"Extra unlocks {len(profile.unlocked_items)}, "
        f"online: {', '.join(online)})"
    )


def _after_menu_ready(session: Session) -> None:
    for delay, with_settings in MENU_READY_DELAYS:
        time.sleep(delay)
        if not session.logged_in:
            return
        try:
            player = session.server.content.player
            session.send(PERMISSIONS, 55500, player.features())
            if with_settings:
                session.send(LOBBY, 20802, player.settings(session.profile))
        except OSError:
            return

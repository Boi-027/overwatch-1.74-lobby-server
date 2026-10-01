"""Login: the first message of a session, and the follow-ups that need the main menu to exist."""

import threading
import time

from ow174.accounts.registry import Account
from ow174.bnet.session_key import name_in_key
from ow174.content import Identity
from ow174.content.presence import STATUS_ONLINE
from ow174.jam.groups import CHAT_IN, FRIENDS, LOBBY, OUT_CONNECT, PERMISSIONS
from ow174.lobby.router import Router
from ow174.lobby.session import FRIEND_CARDS, Session, without_party_state

# "Connection interrupted: the Battle.net account signed in on another device." (13C84.07C)
SIGNED_IN_ELSEWHERE = 0x0DE0000000013C84
# "Load error: incompatible client version." (103BF.07C)
INCOMPATIBLE_CLIENT = 0x0DE00000000103BF

routes = Router()

# Some messages go again a few seconds after login, once the main menu is up. The client drops
# 55500 (account features, which gate chat) on the first login after the game starts, and offline
# friends shown during login get an empty "offline ()" time until their presence comes again. Only
# their presence goes again: a whole friends list made the client announce online friends again.
MENU_READY_DELAYS = (3, 7)  # seconds to wait before each resend
# The client writes "offline (4 minutes)" once, when a friend's presence arrives (0x7FF7894EBF30
# counts from its own clock), so the text only moves when the presence is sent again.
OFFLINE_REFRESH_SECONDS = 60


@routes.on(OUT_CONNECT, 21800)
def login(session: Session, value: dict) -> None:
    server = session.server
    name = login_name(value)
    # The tournament frontend sends the name it asked for; the retail one sends none. They run the
    # game at different command frame lengths, which the game server has to know.
    session.tournament = bool((value.get("+0x78") or "").strip())
    if name:
        account = server.accounts.get(name)
    elif session.local:
        account = server.dashboard_account()
    else:
        # A game on another PC whose Battle.net emulator put no name in the key, from an older
        # version of this server: it would play the dashboard's account.
        session.log("[<<<] Login without a name from another PC, refused")
        session.kick(INCOMPATIBLE_CLIENT)
        return
    _take_over_account(session, account)
    session.log(f"[<<<] Login as '{account.name}' (account 0x{account.account_lo:X})")

    earned = server.content.celebrations.claim_rewards(session.profile)
    greetings, gifts, gift_boxes = server.content.celebrations.greet(session.profile)
    paired = server.shop.add_missing_pairs(session.profile)
    if earned or greetings or paired:
        session.save()
    messages = _login_messages(session, earned + gifts + paired)
    if gift_boxes:  # retail's order at the first login of an event: box (24302), item (24301), 38901
        messages.append(server.content.collection.boxes_update(gift_boxes))
    messages += greetings
    sent = session.send_all(messages)
    session.logged_in = True
    for guid in earned:
        session.log(f"[>>>] Challenge reward: {server.items.describe(guid)}")
    if greetings:
        session.log(f"[>>>] Event greeting for {len(greetings)} new event(s)")
    for guid in gifts:
        session.log(f"[>>>] Event login reward: {server.items.describe(guid)}")
    for guid in paired:
        session.log(f"[>>>] Partner of a bought team skin: {server.items.describe(guid)}")
    _log_login_summary(session, sent, len(messages))

    server.notify_friends(account)
    threading.Thread(target=_after_menu_ready, args=(session,), daemon=True).start()


def login_name(value: dict) -> str:
    """The account name of a login (21800), or "" when it has none. The tournament frontend sends the
    name typed on its name screen. The retail frontend has no name screen: it passes on what
    Battle.net gave it at logon (+0xB8), whose session key our emulator fills with the name."""
    typed = (value.get("+0x78") or "").strip()
    if typed:
        return typed
    battle_net = value.get("+0xB8") or {}
    return name_in_key(bytes(battle_net.get("+0x20") or [])) or ""


def _take_over_account(session: Session, account: Account) -> None:
    """Bind the account to this session and disconnect any older session that had it."""
    server = session.server
    session.account = account
    account.status = STATUS_ONLINE  # the client's dropdown starts at Online on a fresh login
    session.ident = Identity.create(account.account_lo, session.channel.seq)
    account.profile.last_online = int(time.time())
    account.save()
    second_game = server.games is not None and account.name in server.games.second_accounts()
    if session.local and not second_game:
        server.selected = account  # the dashboard follows the game on this PC, not a second one
    previous = server.social.sessions.get(account.account_lo)
    if previous is not None and previous is not session:
        previous.log("[>>>] Replaced by a new login")
        previous.kick(SIGNED_IN_ELSEWHERE)
    server.social.sessions[account.account_lo] = session


def _login_messages(session: Session, granted: list[int]) -> list[tuple]:
    server = session.server
    content = server.content
    messages = without_party_state(content.login_messages(session.profile, session.ident))
    messages += session.party_messages()
    messages.append((LOBBY, 20802, content.player.settings(session.profile)))
    messages.append((FRIENDS, 27100, server.social.friends_state(session.account)))
    messages.append((LOBBY, FRIEND_CARDS, {"+0x78": server.social.friend_cards(session.account)}))
    messages.append((CHAT_IN, 20402, {"+0x78": server.social.general}))
    for guid in granted:
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
    for delay in MENU_READY_DELAYS:
        time.sleep(delay)
        if not session.logged_in:
            return
        try:
            session.send(PERMISSIONS, 55500, session.server.content.player.features())
            _send_offline_presence(session)
        except OSError:
            return
    while True:
        time.sleep(OFFLINE_REFRESH_SECONDS)
        if not session.logged_in:
            return
        try:
            _send_offline_presence(session)
        except OSError:
            return


def _send_offline_presence(session: Session) -> None:
    offline = session.server.social.offline_presence(session.account)
    if offline:
        session.send(FRIENDS, 27113, {"+0x78": offline})

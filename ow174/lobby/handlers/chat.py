"""Chat channels and whispers."""

from ow174.accounts.registry import Account
from ow174.jam.groups import CHAT_IN, CHAT_OUT, FRIENDS, FRIENDS_OUT
from ow174.lobby.router import Router
from ow174.lobby.session import Session

routes = Router()

BOT_REPLY = "{name}, I hear you: {text}"


@routes.on(CHAT_OUT, 21700)
def chat(session: Session, value: dict) -> None:
    social = session.server.social
    channel = value.get("+0x78") or {}
    text = value.get("+0x90") or ""
    message = social.chat_message(channel, session.account, text, value.get("+0xB8", 0))
    members = social.channel_members(channel)
    _deliver(session.server, members, message)
    session.log(f"[chat #{channel.get('+0x10')}] {session.account.name}: {text}")
    bot = social.accounts.bot
    if bot in members and session.profile.bot_chat:
        reply_text = BOT_REPLY.format(name=session.account.name, text=text)
        _deliver(session.server, members, social.chat_message(channel, bot, reply_text))


def _deliver(server, members: list[Account], message: dict) -> None:
    for account in members:
        recipient = server.session_of(account.account_lo)
        if recipient:
            recipient.send(CHAT_IN, 20400, message)


@routes.on(CHAT_OUT, 21701)
def channel_members(session: Session, value: dict) -> None:
    session.send(CHAT_IN, 20401, session.server.social.who(value.get("+0x78") or {}))


@routes.on(FRIENDS_OUT, 27004)
def whisper(session: Session, value: dict) -> None:
    """A Battle.net whisper. 27116 completes the request, and the target gets 27117 {sender, text}.

    The sender's client shows its own line itself, so it gets no copy.
    """
    text = value.get("+0xA0") or ""
    session.send(FRIENDS, 27116, {"+0x78": value.get("+0x78", 0), "+0x80": 0})
    target = _whisper_target(session, value)
    if target is None:
        session.log(f"[<<<] Whisper to unknown player {value}")
        return
    session.log(f"[whisper] {session.account.name} -> {target.name}: {text}")
    if target.virtual:
        if session.profile.bot_chat:
            reply = BOT_REPLY.format(name=session.account.name, text=text)
            session.send(FRIENDS, 27117, {"+0x78": target.account, "+0x88": reply})
        return
    recipient = session.server.session_of(target.account_lo)
    if recipient:
        recipient.send(FRIENDS, 27117, {"+0x78": session.account.account, "+0x88": text})


def _whisper_target(session: Session, value: dict) -> Account | None:
    """The first known player in the whisper who is not the sender. Either field may hold the sender."""
    accounts = session.server.social.accounts
    for key in ("+0x80", "+0x90"):
        player = value.get(key) or {}
        account = accounts.by_id(player.get("+0x0", 0))
        if account and account.account_lo != session.account.account_lo:
            return account
    return None

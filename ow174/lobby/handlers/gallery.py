"""Loot boxes, equipping items and buying them."""

from copy import deepcopy

from ow174.jam.groups import (
    GALLERY_OUT,
    HERO_CATALOG,
    IN_CONNECT,
    PROGRESSION_IN,
    PROGRESSION_OUT,
)
from ow174.lobby.router import Router
from ow174.lobby.session import Session
from ow174.services.lootbox import box_id_from, box_result_message
from ow174.services.shop import ShopError

routes = Router()


@routes.on(PROGRESSION_OUT, 24201)
def open_box(session: Session, value: dict) -> None:
    server = session.server
    box_id = box_id_from(value)
    opening = server.loot.open(box_id, session.profile)
    session.save()
    session.send(PROGRESSION_IN, 24305, box_result_message((box_id, 0), opening.drops))
    session.send(PROGRESSION_IN, 24307, {"+0x78": session.profile.credits})
    descriptions = []
    for drop in opening.drops:
        if "amount" in drop:
            descriptions.append(f"{drop['amount']} credits")
            continue
        description = server.items.describe(drop["unlock"])
        if drop["duplicate"]:
            description += f" (dup +{drop['credits']})"
        descriptions.append(description)
    session.log(f"[>>>] Opened {opening.box_name} box #{box_id}: {'; '.join(descriptions)}")


@routes.on(GALLERY_OUT, 24500)
def equip(session: Session, value: dict) -> None:
    server = session.server
    hero = value.get("+0x78", 0)
    guid = value.get("+0x80", 0)
    slot = value.get("+0x88", 0)
    if not server.content.collection.equip(session.profile, hero, guid, slot):
        session.log(f"[<<<] Equip {server.items.describe(guid)} on 0x{hero:X} slot {slot}: unknown slot")
        return
    session.save()
    session.send(HERO_CATALOG, 24902, {"+0x78": hero, "+0x80": guid, "+0x88": slot})
    if hero == 0 or _is_icon(server, guid):
        # The player icon is shown on the player card, which the party and friends see too.
        record = server.content.player.record(session.profile, session.ident)
        session.send(IN_CONNECT, 20502, {"+0x78": record})
        server.notify_party(server.social.party_of(session.account))
        server.broadcast_presence()
    target = server.items.hero_name(hero) if hero else "account"
    session.log(f"[>>>] Equipped {server.items.describe(guid)} ({target}, slot {slot})")


def _is_icon(server, guid: int) -> bool:
    item = server.items.get(guid)
    return item is not None and item.type == "Icon"


@routes.on(GALLERY_OUT, 24501)
def item_seen(session: Session, value: dict) -> None:
    """The player looked at an item marked new. The client clears the mark itself and needs no
    answer; it sends these in bursts of hundreds when a gallery page opens."""


@routes.on(PROGRESSION_OUT, 24203)
def progression_purchase(session: Session, value: dict) -> None:
    purchase(session, value.get("+0x78", 0))


def purchase(session: Session, guid: int) -> None:
    """Buy an item: charge the profile, save it, and tell the client the new state."""
    server = session.server
    try:
        with server.state_lock:
            profile = deepcopy(session.profile)  # a refused purchase must not touch the live profile
            receipt = server.shop.purchase(profile, guid)
            session.account.profile = profile
            session.save()
    except ShopError as error:
        session.log(f"[<<<] Purchase {server.items.describe(guid)} refused: {error}")
        return
    also = [int(pair, 16) for pair in receipt["also"]]  # the other skin of a team skin pair
    session.send(*server.content.collection.unlock_bought(guid))
    for pair in also:
        session.send(*server.content.collection.unlock_granted(pair))
    # 24300 carries all three balances. A credits-only update would leave the league token and
    # competitive point counters stale.
    session.send(PROGRESSION_IN, 24300, server.content.collection.progression(profile))
    currency = receipt["currency"]
    session.log(
        f"[>>>] Purchased {server.items.describe(guid)} for {receipt['price']} {currency}, "
        f"balance {getattr(profile, currency)}"
    )
    for pair in also:
        session.log(f"[>>>] Came with it: {server.items.describe(pair)}")

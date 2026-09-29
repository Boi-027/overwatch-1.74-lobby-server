"""Choosing roles for a role queue (Quick Play or Competitive with roles).

What the client does (1.74, from its code and statescript graphs):

- Picking a role queue opens the role screen (graph 01B/1FD3) and sends 44100. The whole party is
  then in the queue (44201), and the party state (20700) carries the queue entry (+0x18) and each
  member's choice (+0xC8). Without the entry the role cards are grey (0x7FF789AB7350).
- The entry's state (+0x54) drives the screens (table 0x7FF78B5668E0, type 3D.114): 1 = "wait until
  group members pick roles", 2 = "match search starts in N..." (+0x50 seconds), 3 = searching: the
  role screen closes, the "while you wait" screen opens (01B/2240) and the menu shows the search
  timer (01B/02DA). 0 means no role check, like no entry at all.
- A member whose choice has +0x68 off gets the group banner "pick a role now?" (01B/20AE): Accept
  sends 44104, Decline 44105. The member who started the queue is already on the role screen.
- Ready on the role screen sends 44103 with the member's whole role list, then 44106 with the role
  to spend a priority pass on. Once +0x69 is on, the button reads Change Role and sends 44107; in a
  group it follows with an empty 44103.
- A role is a number: 1 damage, 2 tank, 3 support (0x7FF78B5668D0, 0x7FF78934DCB0).
- The entry's roles with +0x39 on are the ones a priority pass can be spent on (0x7FF789349580).
  A role card offers the pass only for them, and a member can spend one when every chosen role is
  among them (0x7FF78934A540).
- The role cards show estimated waits from 56200 (protocol A1498A6A), which the client keeps in
  its system 0x7C (0x7FF789617DC0) and redraws the cards on. A card reads the record of its role
  (0x7FF7899CCA20): +0x18 the wait and +0x20 the wait with a pass, in seconds; 0 hides the time.
  The flex card reads the record of role 0. The card rounds up to whole minutes, "< N min" up to
  20 minutes (table 0x7FF78BD4FBE0).
"""

from ow174.content.ranked import DAMAGE, SUPPORT, TANK

ROLE_NUMBERS = {1: DAMAGE, 2: TANK, 3: SUPPORT}

# Queue entry states (+0x54).
PICKING, STARTING, SEARCHING = 1, 2, 3

# Retail picked the roles for priority passes, and the waits, from the queues of the moment. With no
# real queues these are fixed: damage has the long wait and takes passes.
PASS_ROLES = (DAMAGE,)
FLEX = 0  # the role of the flex card's record
WAIT_SECONDS = {FLEX: 45, TANK: 90, SUPPORT: 120, DAMAGE: 420}
PASS_WAIT_SECONDS = {DAMAGE: 90}


def _role(role: int, open_for_passes: bool, wait: int = 0, pass_wait: int = 0) -> dict:
    return {
        "+0x0": role,
        "+0x8": 0,
        "+0x10": 0,
        "+0x18": wait,
        "+0x20": pass_wait,
        "+0x28": 0,
        "+0x30": 0,
        "+0x38": False,
        "+0x39": open_for_passes,
    }


def queue_entry(key: dict, state: int) -> dict:
    """The party's entry for the queue with this key (the key of the 44100 request)."""
    roles = [_role(role, role in PASS_ROLES) for role in ROLE_NUMBERS.values()]
    return {"+0x0": {"+0x0": {"+0x0": roles}}, "+0x18": key, "+0x48": 0, "+0x50": 0.0, "+0x54": state}


def wait_times() -> dict:
    """56200: the estimated waits the role cards show."""
    roles = []
    for role, wait in WAIT_SECONDS.items():
        roles.append(_role(role, role in PASS_ROLES, wait, PASS_WAIT_SECONDS.get(role, 0)))
    return {"+0x78": {"+0x0": {"+0x0": roles}}}


def group_slot(slot_types: list[int]) -> dict:
    """A listed group's member (20700 member +0xA0): the slot types the member takes (52203) and the
    one they fill (+0x8). Slot types are the group finder's own: 1 any role, 2 tank, 3 support,
    4 damage (table 0x7FF78B566690). Until a member has one, the client keeps its "choose role(s)"
    screen open (0x7FF789AA46B0); the member's slot is read by 0x7FF78934B270."""
    return {"+0x0": 0, "+0x8": slot_types[0], "+0x9": 0, "+0x10": list(slot_types)}


def role_choice(roles: list[int], accepted: bool, ready: bool, passes: int = 0, pass_role: int = 0) -> dict:
    """A party member's choice (20700 member +0xC8).

    +0x0 is how many priority passes the member has for this queue, +0x8 the chosen roles. The role
    a pass is used for is in +0x38; +0x20 and +0x50 take roles out of +0x8 and +0x38 (0x7FF78934A540),
    and a member with a pass role left makes "your team has priority search" (0x7FF789349978).
    +0x68 is on once the member is on the role screen (no banner), +0x69 once they pressed Ready.
    """
    return {
        "+0x0": passes,
        "+0x8": list(roles),
        "+0x20": [],
        "+0x38": [pass_role] if pass_role else [],
        "+0x50": [],
        "+0x68": accepted,
        "+0x69": ready,
    }

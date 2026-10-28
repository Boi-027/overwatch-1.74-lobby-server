"""The controller's statescript on a player's entity: the HUD and the hero select screen.

Each team of a game mode names its controller graph. The Practice Range's is 13C1: its SubScript
states start the HUD (state 42 -> graph 20E4) and hero select (state 43 -> 288A, whose state 0 ->
288B). The PvP modes use 0C90, whose state 119 starts 288A; its HUD graph is client-only, so the
client makes that one itself. 288B presents the screen 008C.05A ("CHOOSE YOUR HERO") to the local player
while two entity variables are on: v2438 (hero select is open, the server's) and v31435 (what 288B's
client-only nodes read). The client sends the pick as ClientOutInstance 21616 {entity, parameters,
game message 1361.025, instance}, with the hero among the parameters; 1360.025 asks to open or close
the screen. Retail OW2 traffic works the same way.

More of the screen (read in the graphs and in IDA):
- The skin selector shows while 288B's v25377 ("assembling heroes") is on. In retail the PvP mode
  script sets it for the assemble phase, and we do the same; in the Practice Range we set it whenever
  the screen is open, and hide the assemble title that comes with it (v8535). With it on, the client
  leaves the screen open after a pick, so the server closes it. A chosen skin goes to the lobby
  (24500), and the body wears it (world.create).
- The team list: 288A's state 1 starts 288D, whose client-only nodes post their entity (game message
  134F.025) to the local player's 288B unless it is an enemy. On our own entity that puts us in the list.
"""

from dataclasses import dataclass

from ow174.game.content import PRACTICE_RANGE_MODE, heroes
from ow174.game.statescript import (
    CONTROLLER,
    HERO_SELECT,
    HERO_SELECT_HOST,
    HUD,
    PVP_CONTROLLER,
    TEAM_ENTRY,
    Bool,
    Graph,
    Instance,
    chunk,
    graph_create_frame,
    owner_full_frame,
    subscript,
)

OPEN_SELECT = 2438
SELECT_SHOWN = 31435
ASSEMBLING = 0  # 288B's presence bit of v25377
# 288B's v8535 feeds the screen's IsAssemblingHeroesHidden: on, "ASSEMBLE YOUR TEAM" and its timer go. Only
# a client-only Stack writes it (on 06A7.025, which some mode graphs post), so a value from the server
# stays.
HIDE_ASSEMBLE = 8535
PICK_HERO = 0x0240000000001361  # 1361.025 {hero}
OPEN_OR_CLOSE = 0x0240000000001360  # 1360.025 {open}
HERO_TYPE = 0x02E0  # the type bits of a 075 hero GUID


@dataclass(frozen=True)
class Controller:
    graph: Graph
    allow_bit: int  # the presence bit of v8419, m_allowHeroSelect
    hero_select_bit: int  # the HeroSelect state
    select_bit: int  # the SubScript state that starts 288A: its state bit, and its state index
    select_state: int
    hud_bit: int | None = None  # the SubScript state that starts 20E4, if the server has to
    hud_state: int | None = None


PRACTICE = Controller(CONTROLLER, 5, 25, 37, 43, 36, 42)
PVP = Controller(PVP_CONTROLLER, 19, 1, 103, 119)


def controller_of(mode_guid: int) -> Controller:
    return PRACTICE if mode_guid == PRACTICE_RANGE_MODE else PVP


# 20E4's one owner state and the two entity vars its subgraph begins on. A live client proved the
# shape: mount the HUD and set these together with the body's scripts; mounting it without the
# state on leaves the subgraph empty, and the hero select grid loses its heroes.
HUD_GATE = 0
HUD_BEGIN_VARS = (17906, 18405)
# The UI side's own variables, as entity variables of the same snapshot: the HUD layers exist only
# after UI graph 041A's state 20 turns its mode machine on. That state's condition is an OR of
# five terms, one of which is the global var 0x09BA (ui-graphs-casc report B3/B4), and the
# dispatcher graph 046B keeps the preload-sync var 0x401F. The retail server replicates them over
# the statescript component's global sync-var section - the frame's entity-variable list is that
# section (id-delta entries terminated by id 0, bools one bit), so they ride the UI entity's
# snapshot next to the begin vars. 09FC (preload, the 041A pool) comes along: an entity variable
# the client has no slot for is stored and unused, not an error (the hero select screen has
# ignored unknown vars in its own snapshots since the first live runs).
UI_HUD_VARS = (0x09BA, 0x401F, 0x09FC)


def controller_frame(
    controller: Controller,
    cmfd: int,
    select_open: bool,
    skins: bool = False,
    hide_assemble: bool = False,
    hud: bool = False,
):
    """The owner full frame for the player's entity: the controller (1) with hero select 288A (2) ->
    288B (3) under it, 13C1's HUD 20E4 (4), and 288A's team list entry 288D (the next index).
    `skins` turns on v25377, which shows the skin selector; `hide_assemble` hides the assemble title
    and timer that come with it. `hud` mounts the HUD subgraph on (the gate state and its begin
    variables) rather than just creating the instance: pass it when the hero's body scripts ride the
    body, the configuration the HUD widgets read their data from."""
    active = {controller.hero_select_bit: None, controller.select_bit: subscript(2)}
    host = Instance(2, HERO_SELECT_HOST, parent=(1, controller.select_state), active={0: subscript(3)})
    screen = Instance(3, HERO_SELECT, parent=(2, 0))
    instances = [
        Instance(1, controller.graph, presence={controller.allow_bit: Bool(True)}, active=active),
        host,
        screen,
    ]
    variables = {OPEN_SELECT: Bool(select_open), SELECT_SHOWN: Bool(select_open)}
    if controller.hud_bit is not None:
        active[controller.hud_bit] = subscript(4)
        states = {HUD_GATE: None} if hud else {}
        if hud:
            variables.update(dict.fromkeys(HUD_BEGIN_VARS + UI_HUD_VARS, Bool(True)))
        instances.append(Instance(4, HUD, parent=(1, controller.hud_state), active=states))
    entry = len(instances) + 1
    host.active[1] = subscript(entry)
    instances.append(Instance(entry, TEAM_ENTRY, parent=(2, 1)))
    if skins:
        screen.presence[ASSEMBLING] = Bool(True)
    if hide_assemble:
        screen.extra[HIDE_ASSEMBLE] = Bool(True)
    return owner_full_frame(cmfd, instances, variables)


def world_ui_graph_chunk():
    """The UI entity's first record: the empty graph-info chunk at base 1, mirroring the bodies'
    two-step sequence (§ the create must not list the instances the snapshot creates)."""
    return chunk(graph_create_frame([Instance(1, HUD)]), 1, 1)


def world_ui_chunk(cmfd: int):
    """The HUD's own mount on a server-created UI entity: the widget's visibility predicate
    resolves through the UI instance's owner and accepts only an entity whose GetWorld is a game
    world. GetWorld is the entity's registration world (entity+0x120), and every entity the
    create path makes on the game channel is registered in the game world - so a create record
    for the UI definition (0x04000000000001F1) is world-owned without any attach. The client's
    own UI entity is owned by the client's global root, which is why the menu-shape mount above
    (20E4 on the controller) never draws the widgets in a match. Same gate and begin variables
    as the controller mount, wrapped at base 0 like the body's activating snapshot."""
    frame = owner_full_frame(
        cmfd,
        [Instance(1, HUD, active={HUD_GATE: None})],
        dict.fromkeys(HUD_BEGIN_VARS + UI_HUD_VARS, Bool(True)),
    )
    return chunk(frame, 0, 3)


def picked_hero(parameters: bytes) -> int | None:
    """The hero in a pick's parameters: an 8-byte little-endian GUID with the hero type bits (the pick
    in OW2's traffic carries it that way; its exact parameter layout is not certain)."""
    for offset in range(len(parameters) - 7):
        value = int.from_bytes(parameters[offset : offset + 8], "little")
        if value >> 48 == HERO_TYPE and value in heroes():
            return value
    return None


def wanted_open(parameters: bytes) -> bool | None:
    """1360.025's one boolean parameter, or None when it is not found. The parameters end with two u32
    entities (0x7FF78AAB7D30); the boolean before them is its type byte 1 and a value byte."""
    body = parameters[:-8]
    if len(body) >= 2 and body[-2] == 1 and body[-1] in (0, 1):
        return bool(body[-1])
    return None

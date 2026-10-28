"""Hero bodies get their statescript graphs: why the HUD is not empty.

The hero body (003) carries a statescript component whose initial graphs (the bodies table in
data/game_graphs_174.json.gz) are what the client's HUD graph 0x20E4 reads to draw abilities,
weapon and ammo: an instance created by the server does not run its Entry nodes (statescript.py),
so the server sends the schema values (m_1EB5A024) in the owner frame and lets the client-side
nodes do the rest. Without the chunk the body has no graphs and the HUD renders its defaults: no
ability bar, no weapon, the red "can't fire" glyph.

The frames ride the body's create in two steps, as the client's graph manager applies them:
`body_graph_chunk` is the empty graph-info frame at base 1 (it only lifts the manager over the
create tick), `body_full_chunk` is the activating full snapshot at base 0. Listing the instances
in both creates them twice and the client crashes when the activation wait expires; a full frame
wrapped at a numbered base is ignored. A re-pick spawns a new body, so the pair goes again for
the new entity, and the chunk carries the second record's CmFD as any owner frame (a stale one is
dropped, statescript.Stream.next_cmfd).

Two bisection knobs let a deployment narrow the picture without code changes: DROP_GRAPHS leaves
whole graphs out (by 16-bit index), and the `allow` argument restricts which state classes are
activated by name suffix (the SubScript launchers and the HUD gate states are structure and stay
on regardless). With `allow` unset every class is activated except HAS_DATA_STATES below: those
three need the
second-bit has-data form owner_full_frame does not write yet, and a bare active bit for them
shifts every later field in the frame.
"""

from ow174.game import statescript
from ow174.game.script import graph

DROP_GRAPHS: set[int] = set()  # 16-bit graph indexes to leave out of every picture
# The state classes the static picture activates, a set of name suffixes or None for "all the
# one-bit classes the encoder can write". Every body_* function takes it explicitly; SHIP_STATES
# below is what a live server sends: the health and HUD structure, the weapon cosmetics (the gun
# model) and the logical buttons the firing conditions run through. Anim and UXPresenter states
# are deliberately left out: an active one plays its presentation the moment the body activates,
# and heroes carry ability-anim and placement-presenter states right into the spawn - the
# soldier spawns mid-visor and the symmetra's placement screen swallows her view model. The
# animations must be started as transitions (with the state already on), like the volley; the
# presenter states return with the HUD readouts. WeaponVolley moves through `entries` instead -
# the client must see it begin and stop with its data - and the expiry-tick class (0x691BFA55)
# stays out: a live run showed its begin action dies without the type-3 preloads. An entry
# without a mask bit consumes nothing, so a class left out cannot shift the frame.
SHIP_STATES = frozenset({"HealthPool", "BooleanSwitch", "Stack", "Wait",
                         "CosmeticEntity", "LogicalButton"})
STRUCTURAL = {"STU_37D754C8", "STU_9D7BF987"}  # SubScript launchers and the HUD widget gates

VAR_ID_MASK = 0xFFFFFFFFFFFF  # the .01C identifier's index, without the type bits

# The native predicate at 0xf5e960 divides the 486 state classes three ways: the many one-bit
# kinds (a bare active bit), a handful whose one-bit entry carries its own state-data bits
# (BooleanSwitch, Stack, the sender class and the launcher; statescript.DATA_STATES writes them),
# and four classes whose entry is has-data + active + state data (statescript.HAS_DATA_STATES has
# their builders, statescript.HasData is the entry form). The predicate's fourth true class,
# 0x2F07046F, has no state in any graph of our data, so its reader can stay unread.
HAS_DATA_STATES = {
    "STUStatescriptStateLogicalButton",
    "STUStatescriptStateWeaponVolley",
    "STU_691BFA55",
}


def _allowed(cls: str, allow: set[str] | None) -> bool:
    return allow is None or cls in STRUCTURAL or any(cls == a or cls.endswith(a) for a in allow)


def _value(entry):
    """A schema entry's m_value, the statescript.Value it encodes, None for kinds we skip."""
    kind = entry.get("$")
    value = entry.get("m_value")
    if kind == "STUConfigVarInt" and value is not None:
        return statescript.Int(value)
    if kind == "STUConfigVarBool" and value is not None:
        return statescript.Bool(bool(value))
    if kind == "STUConfigVarFloat" and value is not None:
        return statescript.Float(float(value))
    return None  # null, Texture, ContactSet, ...: the client keeps the graph's default


def _subscript_target(node) -> int | None:
    """The child graph a SubScript (37D754C8) state starts: a 01B GUID in its m_36F7B64C chain, or
    None when the node launches no graph."""
    found: set[int] = set()
    graph._collect_graphs([node.fields], found)
    return next(iter(found), None) if found else None


def body_instances(
    body_guid: int,
    allow: set[str] | None = None,
    entries: dict[tuple[int, int], statescript.HasData] | None = None,
) -> tuple[list, dict]:
    """The body's initial graphs as (instances, entity vars) for an owner full frame.

    Every graph of the body's statescript component becomes a root instance carrying its schema
    values (m_1EB5A024) and every networked state turned on: a server-created instance does not
    run its Entry nodes, so the settled picture a running Entry would reach is sent directly
    (statescript.py). A SubScript launcher is turned on only when the graph it starts is one of
    this body's instances, so the child id it carries is always valid.

    Two entity variables deserve naming here and nowhere else. The first is the hero asset every
    StateHero in the graphs reads (0x03ac): unset, no StateHero ever starts; a null guid there and
    the client exits on activation. The body graphs of the extracted data carry no HUD (0x20E4):
    that graph rides the controller's own frames, and its begin-condition variables belong there."""
    record = graph.bodies().get(body_guid)
    if record is None:
        return [], {}
    by_index = {entry["graph"]: i for i, entry in enumerate(record["graphs"], start=1)
                if entry["graph"] is not None and entry["graph"] not in DROP_GRAPHS}
    instances, entity_vars = [], {}
    from ow174.game.content import heroes  # here: content pulls in the catalogue, keep it lazy

    hero = next((h for h in heroes().values() if h.body == body_guid), None)
    if hero is not None:
        entity_vars.setdefault(0x03AC, statescript.Asset(hero.guid))
    for index, entry in enumerate(record["graphs"], start=1):
        if entry["graph"] is None:
            continue  # the component's array has an empty slot; the client has no instance there
        if entry["graph"] in DROP_GRAPHS:
            continue
        g = graph.graph(entry["graph"])
        if g is None:
            continue  # the body references a graph the extracted data does not carry
        presence = {}
        for schema in entry.get("m_1EB5A024") or []:
            value = _value(schema.get("m_value") or {})
            if value is None:
                continue
            var = int(schema["m_0D09D2D9"], 16) & VAR_ID_MASK
            bit = g.presence_bit(var)
            if bit is not None:
                presence[bit] = value
            else:
                entity_vars[var] = value  # an entity-scope variable: the frame's entity block
        active = {}
        for slot, node in enumerate(g.owner_states()):
            if node.cls == "STU_37D754C8":  # a SubScript launcher: on only for a graph that is an instance
                child = by_index.get(_subscript_target(node))
                if child:
                    active[slot] = statescript.subscript(child)
            elif node.cls == "STUStatescriptStateLogicalButton" and _allowed(node.cls, allow):
                # The firing and HUD conditions run through active buttons, and an active button
                # must carry its press counter: the two-bit entry with zero presses so far.
                active[slot] = statescript.has_data_entry(node.cls)
            elif node.cls == "STU_691BFA55" and allow is not None and _allowed(node.cls, allow):
                # The unnamed expiry-tick class of the has-data family: the entry carries the
                # delay as a sign-magnitude int. A live run activated it and the client died on
                # spawn (the state's begin action reads preloads the server does not send), so
                # only an explicitly named allow-list turns it on; `None` (every one-bit class)
                # leaves it out. The encoder is ready for when the preloads land.
                active[slot] = statescript.has_data_entry(node.cls, ticks=0)
            elif node.cls in HAS_DATA_STATES:
                # A volley lit at spawn fires and never ends - callers move those through
                # `entries` - and an entry without a mask bit consumes nothing, so leaving
                # them out cannot shift the frame.
                continue
            elif not _allowed(node.cls, allow):
                continue  # outside the allow-list: not activated, no entry in the mask
            elif node.cls in statescript.DATA_STATES:
                active[slot] = statescript.active_data(node.cls)()  # the class's own data bits
            else:
                active[slot] = None  # the one-bit form; the Entry chain a server instance skips
                # would have run these states up (statescript.py)
        for (want, want_slot), value in (entries or {}).items():
            if want == index and want_slot < len(g.owner_states()):
                active[want_slot] = value
        instances.append(
            statescript.Instance(
                index,
                statescript.Graph(entry["graph"], len(g.owner_states()), len(g.presence_vars())),
                presence=presence,
                active=active,
            )
        )
    instances += gun_instances(body_guid, allow, entries)
    return instances, entity_vars


# The manager graph's states a live run proved enough to let the weapon cosmetics build (the
# census manager run: manager states 39 and 43 with a cosmetic state turn the gloves into the
# gun). They sit at owner bits 29 and 33 in 1.74's 35-state owner list.
MANAGER_STATES = (29, 33)
# The ammo counter is this sync var of the weapon script (index 53 in its sync-var list, bit 8
# of the presence list); the widget re-reads it whenever the snapshot writes it, so a fresh
# magazine rides the body snapshot itself. AMMO_VAR is the var, AMMO_MAGAZINE the count.
AMMO_VAR = 0x0035
AMMO_MAGAZINE = 30


def gun_instances(
    body_guid: int, allow: set[str] | None, entries: dict[tuple[int, int], statescript.HasData]
) -> list:
    """The manager (0x15) and the weapon scripts' instances for a body: the scripts a gun needs.

    A hero's statescript component names the body graphs (body_instances) plus a manager and up
    to three weapon scripts; the manager starts the cosmetic and animation machinery and a weapon
    script's CosmeticEntity state builds the view model. With only the body graphs the client
    shows bare gloves. The cosmetic state needs no data beyond its active bit (the wire class is
    one bit; the definition it builds must come from the preload list - see content), and the
    weapon script also carries the ammo var, written here so the counter has a number to show.
    """
    record = graph.bodies().get(body_guid)
    if record is None or not _allowed("STUStatescriptStateCosmeticEntity", allow):
        return []
    instances = []
    for index, script in _gun_plan(body_guid)[1]:
        g = graph.graph(script)
        if script == record["manager"]:
            manager_active = {
                slot: None
                for slot in MANAGER_STATES
                if slot < len(g.owner_states())
            }
            for (want, want_slot), value in (entries or {}).items():
                if want == index and want_slot < len(g.owner_states()):
                    manager_active[want_slot] = value
            instances.append(
                statescript.Instance(
                    index,
                    statescript.Graph(
                        script, len(g.owner_states()), len(g.presence_vars())
                    ),
                    active=manager_active,
                )
            )
            continue
        active = {
            slot: None
            for slot, node in enumerate(g.owner_states())
            if node.cls == "STUStatescriptStateCosmeticEntity"
        }
        presence = {}
        bit = g.presence_bit(AMMO_VAR)
        if bit is not None:
            presence[bit] = statescript.Int(AMMO_MAGAZINE)
        for (want, want_slot), value in (entries or {}).items():
            if want == index and want_slot < len(g.owner_states()):
                active[want_slot] = value
        instances.append(
            statescript.Instance(
                index,
                statescript.Graph(script, len(g.owner_states()), len(g.presence_vars())),
                presence=presence,
                active=active,
            )
        )
    return instances


def _gun_plan(body_guid: int) -> tuple[int, list[tuple[int, int]]]:
    """The gun instances as (number of body graphs, [(instance index, weapon graph) ...] including
    the manager first): the indices body_instances hands out, recomputed for callers that need them
    before building the instances."""
    record = graph.bodies().get(body_guid)
    if record is None:
        return 0, []
    plan, index = [], len(record["graphs"]) + 1
    if record["manager"] is not None and graph.graph(record["manager"]) is not None:
        plan.append((index, record["manager"]))
        index += 1
    for weapon in record["weapons"] or []:
        if weapon is not None and graph.graph(weapon) is not None:
            plan.append((index, weapon))
            index += 1
    return len(record["graphs"]), plan


def gun_defs(body_guid: int) -> list[int]:
    """The .003 definition guids the body's weapon cosmetics build - the gun models. The client
    builds a cosmetic only when its definition is resident; the 20505 preload list is where a
    server says so, and these guids are missing from the captured 1.68 list."""
    record = graph.bodies().get(body_guid)
    if record is None:
        return []
    defs = []
    for weapon in record["weapons"] or []:
        g = graph.graph(weapon) if weapon is not None else None
        if g is None:
            continue
        for node in g.owner_states():
            if node.cls != "STUStatescriptStateCosmeticEntity":
                continue
            holder = node.fields.get("m_entityDef") or {}
            guid = holder.get("m_entityDef") if isinstance(holder, dict) else None
            if isinstance(guid, str) and guid.lower().startswith("0x"):
                defs.append(int(guid, 16))
    return defs


def volley_target(body_guid: int) -> tuple[int, int] | None:
    """The gun's WeaponVolley state as (instance index, owner state slot): the first volley in the
    body's weapon scripts - the scripts that also build the view model, and whose volleys drive the
    firing picture - falling back to the body graphs (the order body_instances walks them). The
    firing frames light exactly this entry."""
    record = graph.bodies().get(body_guid)
    if record is None:
        return None
    _, plan = _gun_plan(body_guid)
    for index, weapon in plan:
        if weapon == record["manager"]:
            continue
        g = graph.graph(weapon)
        for slot, node in enumerate(g.owner_states()):
            if node.cls == "STUStatescriptStateWeaponVolley":
                return index, slot
    for index, entry in enumerate(record["graphs"], start=1):
        if entry["graph"] is None or entry["graph"] in DROP_GRAPHS:
            continue
        g = graph.graph(entry["graph"])
        if g is None:
            continue
        for slot, node in enumerate(g.owner_states()):
            if node.cls == "STUStatescriptStateWeaponVolley":
                return index, slot
    return None


def body_frame(
    body_guid: int,
    cmfd: int,
    lbss: int | None = None,
    allow: set[str] | None = None,
    entries: dict[tuple[int, int], statescript.HasData] | None = None,
):
    """The owner full frame of the body's scripts, None for a body the data does not describe.
    `cmfd` must exceed the last CmFD sent to this entity, as any owner frame does. `entries`
    lights or stops two-bit states on top of the static picture, keyed (instance, state slot)."""
    instances, entity_vars = body_instances(body_guid, allow, entries)
    if not instances:
        return None
    return statescript.owner_full_frame(cmfd, instances, entity_vars, lbss)


def body_graph_chunk(body_guid: int, allow: set[str] | None = None):
    """The first record on a fresh body: an empty graph-info frame at base 1. The fresh entity's
    manager holds no instances until the first full snapshot creates them; listing them here too
    makes the snapshot create them a second time."""
    instances, _ = body_instances(body_guid, allow)
    if not instances:
        return None
    return statescript.chunk(statescript.graph_create_frame([]), 1, 1)


def body_full_chunk(
    body_guid: int,
    cmfd: int,
    allow: set[str] | None = None,
    entries: dict[tuple[int, int], statescript.HasData] | None = None,
):
    """The second record: the activating full snapshot, wrapped at base 0 (last frame 2, so it
    stays above the create's graph-info frame and cannot queue-stall). The same blob wrapped at a
    numbered base is ignored: a full frame at base 0 is what resets and creates the instances."""
    frame = body_frame(body_guid, cmfd, allow=allow, entries=entries)
    return None if frame is None else statescript.chunk(frame, 0, 3)

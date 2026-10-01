"""A match: its players, their entities, and what each player's client is told.

How a player gets into the world, as ProCore got it working in the Practice Range (their live tests;
the create path, 20302 and the loading gate re-read in IDA):
- When the map is loaded the client sends 21601 (and later 21602). 0.3 s later the server sends
  20302 {the player entity}: "this is your entity" (0x7FF7896E6C10 writes world +0x298). It has to
  come before the player's create, or the client does not add its command components (59, 63).
- 0.6 s after loading, one frame creates the map's server-owned placeables (the loading screen only
  ends once they all exist), the player entity (000E.003, flags 0x3C) and the game mode entity
  (16B2.003).
- Then the player's entity gets its controller's statescript (heroselect.py): the HUD and the hero
  select screen. That frame puts the entity's statescript in owner mode, so small owner frames keep
  telling the client which of its commands the server has (CmFD), else it replays old key presses.
- The hero's body comes with the pick, as in retail (OW2 traffic): the body with its movement state
  and an update of the player entity whose component 44 possesses it. A switch replaces the body
  under a new id in the same frame (the client ignores a destroyed id for 30 s).
- The client then moves the body itself; it never tells the server where it is. The server moves
  a copy from the commands, and that copy is what the other players see.
- In the PvP modes the first spawn starts "assemble your team": 30 s shown on the hero select
  screen, which stays open after a pick and closes when the time is up.
"""

import logging
import math
import secrets
import time

from ow174.game import heroselect, world
from ow174.game.commands import Command, CommandQueue
from ow174.game.content import SOLDIER, GameMap, Hero, heroes, spawn_point
from ow174.game.mover import FlatMover
from ow174.game.statescript import Float, Stream, owner_ack, variables_frame
from ow174.game.world import (
    OP_CREATE,
    OP_DESTROY,
    OP_UPDATE,
    PLAYER_PATH,
    WITH_PARTICIPANT,
    WITH_SCALE,
    WITH_TRANSFORM,
    EntityUpdate,
    Movement,
)

log = logging.getLogger("ow174.game")

PLAYER_DEFINITION = 0x040000000000000E  # 000E.003, the player (controller) entity
GAME_MODE_DEFINITION = 0x04000000000016B2
GAME_MODE_ENTITY = 0xD0000010
PLAYER_FLAGS = WITH_PARTICIPANT | PLAYER_PATH | WITH_TRANSFORM | WITH_SCALE
BODY_FLAGS = WITH_TRANSFORM | WITH_SCALE

# Component 26, the filter bits: 0x800000 << team picks the team's physics proxy (team 0 or 1). The
# client's own session builder gives its player 0x10040001 | that bit (0x7FF78973950E), but
# 0x10000000 is the spectator bit (GetEntityIsSpectator, 0x7FF789B2F000): ProCore's player with it
# got the spectator's HUD panel, and their bodies with only it had no collision and no enemy
# outline. So the player gets 0x00040001 | the team bit, a body only the team bit.
TEAM_BIT = 0x800000
PLAYER_FILTER = 0x00040001
HEALTH = 200.0  # every hero, until the heroes' own pools are read from their data

RETAIL_QUANTA = 16000  # microseconds per command frame; 20300 must name the client's own value
TOURNAMENT_QUANTA = 7000  # tank_TournamentModeTickRate, which tournament mode turns on

# Seconds after the client reports its map loaded.
SEND_OWN_ENTITY = 0.3
SEND_GAME = 0.4
SEND_WORLD = 0.6
SEND_CONTROLLER = 0.7  # a frame after the entity exists
OWNER_ACK_EVERY = 0.1  # seconds between owner frames that only move CmFD on

# "Assemble your team" in the PvP modes: their mode graphs (0F4A, 0EC2) set the game mode entity's
# v1303 to 30 and count it down; 288B shows it on the hero select screen as long as it is open.
ASSEMBLE_SECONDS = 30
COUNTDOWN = 1303
COUNTDOWN_EVERY = 1.0  # the screen shows whole seconds


def _yaw_units(degrees: float) -> int:
    return round(degrees * 65536 / 360)


def _rotation(yaw_units: int) -> tuple[float, float, float, float]:
    half = yaw_units * math.pi / 65536
    return (0.0, math.sin(half), 0.0, math.cos(half))


class Player:
    def __init__(
        self, match: "Match", slot: int, account_lo: int, name: str, hero: Hero, team: int, quanta: int
    ):
        self.match = match
        self.slot = slot
        self.account_lo = account_lo
        self.name = name
        self.hero = hero
        self.team = team
        self.quanta = quanta
        self.entity = 0xA0000000 | (slot + 1) << 8
        self.body = self.entity | 1
        self.has_body = False  # not before the first pick
        self.skin = (0, False)  # the body's (skin theme, golden weapon)
        self.client = None  # the connected client (server.Client), None before it joins or after it left
        self.loaded_at: float | None = None
        self.steps_done = 0
        self.spawned = False
        teammates = sum(1 for other in match.players if other.team == team)
        self.spawn = spawn_point(match.game_map, team, teammates)  # (position, yaw in degrees)
        self.mover = FlatMover(self.spawn[0], _yaw_units(self.spawn[1]))
        self.commands = CommandQueue()
        # Other players' bodies this player's client has: body -> False in the frame of its create.
        self.seen: dict[int, bool] = {}
        self.script = Stream(self.entity)  # the player entity's statescript: the controller
        self.mode_script = Stream(GAME_MODE_ENTITY)  # the game mode entity's variables: the countdown
        self.next_countdown = 0.0
        self.select_open = True

    @property
    def team_bit(self) -> int:
        return TEAM_BIT << self.team

    def movement(self) -> Movement:
        mover = self.mover
        flags = 0x2 if mover.crouched else 0
        return Movement(mover.position, mover.yaw, mover.pitch, mover.velocity, flags)

    def take_commands(self, commands: list[Command]) -> None:
        """Move the server's copy of the body. Without a body the client moves nothing either."""
        seconds = self.quanta / 1e6
        for command in self.commands.new(commands):
            if self.has_body:
                self.mover.step(command, seconds)

    def new_body(self) -> None:
        """The next body id, at the spawn point: the server's flat mover is not exact enough to put a
        new body where the old one stood. Ids cycle through the player's 255."""
        self.body = self.entity | ((self.body & 0xFF) % 255 + 1)
        self.mover = FlatMover(self.spawn[0], _yaw_units(self.spawn[1]))

    def describe(self) -> str:
        return f"{self.name} ({self.hero.name}, team {self.team + 1})"


def no_skin(account_lo: int, hero: int) -> tuple[int, bool]:
    return 0, False


class Match:
    def __init__(self, game_map: GameMap, on_leave=None, skin_of=no_skin) -> None:
        self.id = (secrets.randbits(63), secrets.randbits(63))
        self.game_map = game_map
        # (account, hero GUID) -> (skin theme, golden weapon) the player has equipped, from the lobby.
        # The client's hero select equips a skin through the lobby (24500), even in a match.
        self.skin_of = skin_of
        self.controller = heroselect.controller_of(game_map.mode_guid)
        self.tick = 0  # the server's frame number, set every tick
        self.assemble_ends: float | None = None  # PvP: when "assemble your team" ends
        self.assembled = False
        self.players: list[Player] = []
        self.started = time.time()
        self.on_leave = on_leave  # called with each player who leaves
        self.ended = False

    def add_player(self, account_lo: int, name: str, hero_guid: int, team: int, tournament: bool) -> Player:
        hero = heroes().get(hero_guid) or heroes()[SOLDIER]
        quanta = TOURNAMENT_QUANTA if tournament else RETAIL_QUANTA
        player = Player(self, len(self.players), account_lo, name, hero, team, quanta)
        self.players.append(player)
        return player

    def label(self) -> str:
        return f"match {self.id[0] & 0xFFFF:04X}"

    # --- messages ------------------------------------------------------------------------------

    def game_struct(self, viewer: Player) -> dict:
        """The 1064-byte game struct of 20300 and 20301: the mode, the players and their teams. Only
        the mode is known to matter; ProCore saw no change from the roster."""
        records = [{"+0x50": player.name} for player in self.players]
        teams = []
        for team in (0, 1):
            slots = [{"+0x0": player.entity} for player in self.players if player.team == team]
            teams.append({"+0x18": slots, "+0x60": team, "+0x61": team})
        return {
            "+0x0": {
                "+0x0": {
                    "+0x0": {"+0x0": records},
                    "+0xB0": self.game_map.mode_guid,
                    "+0xC0": teams,
                    "+0xD8": [{"+0x0": viewer.entity}],
                },
                "+0x274": 16,
            }
        }

    def instance_message(self, viewer: Player) -> dict:
        """20300: load this map with this mode. +0x500 must equal the client's command frame length
        (0x7FF7896E94B0), else error 262445; +0x519 must match a byte that is 0 (error 262455)."""
        return {
            "+0x78": 1,
            "+0x80": self.game_struct(viewer),
            "+0x4A8": self.game_map.map_guid,
            "+0x500": viewer.quanta,
            "+0x508": {"+0x0": 1, "+0x8": 1},
        }

    # --- entities ------------------------------------------------------------------------------

    def placeable_ids(self) -> list[int]:
        return [0x80000000 | index for index in self.game_map.placeables]

    def spawn_updates(self, viewer: Player) -> list[EntityUpdate]:
        """The frame that ends the loading screen: the placeables, the viewer's player (and body, if it
        has one), the game mode entity, and the bodies of the players already in the world."""
        updates = [
            EntityUpdate(entity, OP_CREATE, lambda origin, index=index: world.create_placeable(origin, index))
            for entity, index in zip(self.placeable_ids(), self.game_map.placeables, strict=True)
        ]
        if viewer.has_body:
            updates += self.own_body_updates(viewer)
        updates.append(
            EntityUpdate(viewer.entity, OP_CREATE, lambda origin: self._player_create(origin, viewer))
        )
        updates.append(EntityUpdate(GAME_MODE_ENTITY, OP_CREATE, self._game_mode_create))
        for other in self.players:
            if other is not viewer and self._shown(other):
                updates.append(self.remote_body_create(viewer, other))
        return updates

    @staticmethod
    def _shown(player: Player) -> bool:
        """Whether other players' clients get this player's body."""
        return player.spawned and player.has_body and player.client is not None

    def own_body_updates(self, viewer: Player) -> list[EntityUpdate]:
        """The viewer's own body: its movement state rides in the create (at frame 0xFFFFFFFE) and in a
        movement record at the packet frame, as in ProCore's working spawn."""
        state = viewer.movement()
        return [
            EntityUpdate(
                viewer.body, OP_CREATE, lambda origin: self._body_create(origin, viewer, state), state
            )
        ]

    def _body_create(self, origin: int, owner: Player, state: Movement | None) -> world.RecordWriter:
        components = world.health(HEALTH, HEALTH)
        components[26] = [owner.team_bit]
        components[123] = [owner.hero.guid]
        return world.create(
            origin,
            owner.hero.body,
            components,
            flags=BODY_FLAGS,
            position=owner.mover.position,
            rotation=_rotation(owner.mover.yaw),
            movement=state,
            skin=owner.skin,
        )

    def _player_create(self, origin: int, player: Player) -> world.RecordWriter:
        components = {
            26: [PLAYER_FILTER | player.team_bit],
            74: [None, None, player.slot, None, None, None, 1, 1, None],
        }
        if player.has_body:
            components.update(self._possession(player))
        return world.create(origin, PLAYER_DEFINITION, components, flags=PLAYER_FLAGS)

    @staticmethod
    def _possession(player: Player) -> dict:
        """Components 44 (the body the player possesses, views and controls) and 75 (its hero, skin
        theme and the last valid hero)."""
        theme = player.skin[0] or None
        return {44: [player.body] * 3, 75: [player.hero.guid, theme, None, player.hero.guid]}

    def _game_mode_create(self, origin: int) -> world.RecordWriter:
        return world.create(origin, GAME_MODE_DEFINITION, {114: [None, self.game_map.mode_guid]})

    def remote_body_create(self, viewer: Player, other: Player) -> EntityUpdate:
        """Another player's body: placed by its transform, then moved by movement records from the next
        frame on (a movement state in the create would sit at frame 0xFFFFFFFE, and the client refuses
        every later record that is not newer)."""
        viewer.seen[other.body] = False
        return EntityUpdate(other.body, OP_CREATE, lambda origin: self._body_create(origin, other, None))

    def remote_movements(self, viewer: Player) -> list[EntityUpdate]:
        """A movement record for every other player's body in every frame, standing or not, as the
        retail server sent them (OW2's live traffic); the first in the frame after the body's create."""
        updates = []
        for other in self.players:
            if other is viewer or other.body not in viewer.seen:
                continue
            if not viewer.seen[other.body]:
                viewer.seen[other.body] = True
                continue
            updates.append(EntityUpdate(other.body, movement=other.movement()))
        return updates

    def destroy_for_others(self, player: Player, body: int) -> None:
        for other in self.players:
            if other is not player and body in other.seen:
                del other.seen[body]
                if other.client is not None:
                    other.client.queue_entities([EntityUpdate(body, OP_DESTROY)])

    # --- lifetime ------------------------------------------------------------------------------

    def update(self, now: float, tick: int) -> None:
        """Run the spawn steps of players who loaded, keep their statescript acked, and show new bodies
        to the others."""
        self.tick = tick
        if self.assemble_ends is not None and not self.assembled and now >= self.assemble_ends:
            self._end_assemble(now)
        for player in self.players:
            client = player.client
            if client is None:
                continue
            if player.loaded_at is not None:
                self._spawn_steps(player, client, now)
            if player.script.data_last and now >= player.script.next_ack:
                self._owner_ack(player, player.script, now)
            if self.assembling() and player.steps_done >= 4 and now >= player.next_countdown:
                self._send_countdown(player, now)
        for viewer in self.players:
            if not viewer.spawned or viewer.client is None:
                continue
            for other in self.players:
                if other is not viewer and self._shown(other) and other.body not in viewer.seen:
                    viewer.client.queue_entities([self.remote_body_create(viewer, other)])

    def _spawn_steps(self, player: Player, client, now: float) -> None:
        waited = now - player.loaded_at
        if player.steps_done == 0 and waited >= SEND_OWN_ENTITY:
            client.queue_reliable(20302, {"+0x78": {"+0x0": player.entity}, "+0x7C": False, "+0x80": 0})
            player.steps_done = 1
        if player.steps_done == 1 and waited >= SEND_GAME:
            client.queue_reliable(20301, {"+0x78": self.game_struct(player)})
            player.steps_done = 2
        if player.steps_done == 2 and waited >= SEND_WORLD:
            client.queue_entities(self.spawn_updates(player))
            player.steps_done = 3
            player.spawned = True
            log.info("[game] %s: %s spawned", self.label(), player.describe())
            if self.controller is heroselect.PVP and self.assemble_ends is None:
                self.assemble_ends = now + ASSEMBLE_SECONDS
                log.info("[game] %s: assemble your team, %d s", self.label(), ASSEMBLE_SECONDS)
        if player.steps_done == 3 and waited >= SEND_CONTROLLER:
            player.steps_done = 4
            self.send_controller(player)

    # --- assembling heroes ---------------------------------------------------------------------

    def assembling(self) -> bool:
        return self.assemble_ends is not None and not self.assembled

    def _send_countdown(self, player: Player, now: float) -> None:
        """The seconds left, as the game mode entity's v1303. The client shows them rounded up."""
        player.next_countdown = now + COUNTDOWN_EVERY
        seconds = max(0.0, self.assemble_ends - now)
        frame = variables_frame({COUNTDOWN: Float(seconds)})
        player.client.queue_entities([player.mode_script.full_frame(frame)])

    def _end_assemble(self, now: float) -> None:
        """The countdown is over: the screens of the players who have a hero close, and the skin
        selector goes."""
        self.assembled = True
        log.info("[game] %s: heroes assembled", self.label())
        for player in self.players:
            if player.client is None or player.steps_done < 4:
                continue
            self._send_countdown(player, now)
            if player.has_body:
                player.select_open = False
            self.send_controller(player)

    # --- the player's statescript --------------------------------------------------------------

    def _newest_frame(self, player: Player) -> int:
        """The newest command frame we have from the client, or our tick before the first."""
        return player.commands.last_frame or self.tick

    def send_controller(self, player: Player) -> None:
        """A full frame of the controller's statescript, with hero select open or closed. The skin
        selector shows in the PvP modes while heroes are assembled, in the Practice Range whenever
        the screen is open."""
        if player.client is None:
            return
        cmfd = player.script.next_cmfd(self._newest_frame(player))
        practice = self.controller is heroselect.PRACTICE
        skins = player.select_open and (practice or self.assembling())
        frame = heroselect.controller_frame(self.controller, cmfd, player.select_open, skins, practice)
        player.client.queue_entities([player.script.full_frame(frame)])
        state = "open" if player.select_open else "closed"
        log.info("[game] %s: %s's controller sent (hero select %s)", self.label(), player.name, state)

    def _owner_ack(self, player: Player, stream: Stream, now: float) -> None:
        """A delta owner frame that only moves CmFD on, so the client replays fewer of its own commands.
        Only once the frames with data have arrived: a newer chunk whose range covers a queued one makes
        the client drop that one."""
        stream.next_ack = now + OWNER_ACK_EVERY
        if stream.data_in_flight or (player.commands.last_frame or 0) <= stream.cmfd:
            return
        cmfd = stream.next_cmfd(self._newest_frame(player))
        player.client.queue_entities([stream.ack_delta(owner_ack(cmfd))])

    def game_message(self, player: Player, value: dict, now: float) -> None:
        """21616: a game message a statescript action sends to the server, such as a hero pick."""
        message = value.get("+0xA8", 0)
        parameters = bytes(value.get("+0x80") or b"")
        log.info(
            "[game] %s: %s sent game message %X (instance %s) %s",
            self.label(),
            player.name,
            message & 0xFFFFFFFF,
            value.get("+0xB0"),
            parameters.hex(" "),
        )
        if message == heroselect.PICK_HERO:
            hero = heroes().get(heroselect.picked_hero(parameters) or 0)
            if hero is None:
                log.warning("[game] %s: no hero found in the pick", self.label())
                return
            skin = self.skin_of(player.account_lo, hero.guid)
            if hero is not player.hero or skin != player.skin or not player.has_body:
                self.switch_hero(player, hero)
            if self.assembling():
                return  # the screen stays open until the heroes are assembled, as in retail
            player.select_open = False
            self.send_controller(player)
        elif message == heroselect.OPEN_OR_CLOSE:
            wanted = heroselect.wanted_open(parameters)
            player.select_open = not player.select_open if wanted is None else wanted
            if not player.has_body:
                player.select_open = True  # no hero yet: nothing to go back to
            self.send_controller(player)

    def map_loaded(self, player: Player, now: float) -> None:
        if player.loaded_at is None:
            player.loaded_at = now
            log.info("[game] %s: %s loaded the map", self.label(), player.name)

    def switch_hero(self, player: Player, hero: Hero) -> None:
        """The body of a picked hero. The first one just comes. A switch creates the new body under a
        new id at the spawn point, moves the player's components 44 and 75 to it and destroys the old
        body in the same frame: retail drops the old one at once too (OW2 traffic), while ProCore's
        .sethero kept it 0.5 s, and it showed when the hero select screen closed."""
        old = player.body if player.has_body else None
        if old is not None:
            player.new_body()
        player.hero = hero
        player.skin = self.skin_of(player.account_lo, hero.guid)
        player.has_body = True
        if player.client is not None and player.spawned:
            components = self._possession(player)
            updates = self.own_body_updates(player)
            updates.append(
                EntityUpdate(player.entity, OP_UPDATE, lambda origin: world.update(origin, components))
            )
            if old is not None:
                updates.append(EntityUpdate(old, OP_DESTROY))
            player.client.queue_entities(updates)
        if old is not None:
            self.destroy_for_others(player, old)
        theme, golden = player.skin
        look = f", skin theme {theme:X}{' + golden weapon' if golden else ''}" if theme else ""
        log.info("[game] %s: %s plays %s%s", self.label(), player.name, hero.name, look)

    def leave(self, player: Player) -> None:
        if player.client is None and not player.spawned:
            return
        player.client = None
        player.spawned = False
        self.destroy_for_others(player, player.body)
        log.info("[game] %s: %s left", self.label(), player.name)
        if self.on_leave is not None:
            self.on_leave(player)
        if all(other.client is None for other in self.players):
            self.ended = True

    def snapshot(self) -> dict:
        return {
            "id": self.label(),
            "map": self.game_map.name,
            "started": self.started,
            "players": [
                {
                    "name": player.name,
                    "hero": player.hero.name,
                    "team": player.team + 1,
                    "state": "playing" if player.spawned else ("loading" if player.client else "waiting"),
                }
                for player in self.players
            ],
        }


def pong(ping: dict, received_ns: int) -> dict:
    """20306 for a 21610 ping: its index and stamps, plus when we got it and when we answer."""
    stamps = [*ping.get("+0x80", []), received_ns, time.time_ns()]
    return {"+0x78": ping.get("+0x78", 0), "+0x80": stamps}

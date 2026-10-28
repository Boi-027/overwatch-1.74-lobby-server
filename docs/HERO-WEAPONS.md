# Hero guns and the in-game HUD (1.74)

What it takes for a scripted hero body (see [STATESCRIPT.md](STATESCRIPT.md)) to show its weapon
and its HUD. Every gate below was read out of the client and confirmed by a live render; the
encoders are in `ow174/game/loadout.py`, `ow174/game/heroselect.py` and
`ow174/lobby/matchmaker.py`, behind `--hero-scripts`.

## A body picture alone shows gloves

The statescript component lists the graphs an entity runs, and a body's picture normally names
only the body graphs. The view model lives in the hero's weapon script graphs (Soldier: 76's
0x254 builds its gun through a `CosmeticEntity` state) and those run in a manager graph (0x15):
a snapshot that shows no gun is missing instances, not cosmetics. The working picture adds:

- the manager instance, with its two weapon-management states on (the states named by the graph's
  `m_states` for classes `CD46AF93` and `BD02E168`, at bit positions 29 and 33 of the instance
  list). These are bare-bit states; the pair was verified against a live 1.23 server first, and
  the same two states build the cosmetics there;
- every weapon script instance of the hero, with all its `CosmeticEntity` states on (one bit each).

The gun definition the cosmetic state instantiates is looked up in the 20505 preload list: a
weapon graph that is on but whose definition was never preloaded builds nothing, silently. The
captured 1.68 skin list this server ships does not carry gun definitions, so `loadout.gun_defs()`
appends the hero's own definitions to the skins list of the same 20505 record (list entry at
`+0x90`).

## The ammo number

The script's ammo counter is an entity sync var: for 0x254 it is var 53 (id 0x0035), presence bit
8 of its instance entry, value a full magazine (30). The widget re-reads the var whenever a
snapshot writes it - the write-up hook runs from the frame reader's var loop, at
0x7FF78A1B2DC0 (1.74) - so the counter has its number before the first shot, and a BeginAction
on the script is not needed to light it.

Firing is the script's own `WeaponVolley` state (for 0x254, state 10 of the weapon instance,
instance 11 of the picture): the two-bit form documented in STATESCRIPT.md, driven from button
32 of the player's command record. A press lights the volley, a release stops it.

## The HUD needs a world-owned entity, and the mode machine needs mode 5

Two independent gates decide whether the in-game HUD draws, and both are native:

1. **Owner.** The widget visibility predicate resolves through the UI instance's owner entity and
   accepts only an entity registered in a game world - registration is what makes one: the create
   path stores the world pointer into entity `+0x120`, which is what `GetWorld` answers. The UI
   entity the client creates at boot is owned by the client's global root, so the menu-shape HUD
   mount never draws in a match. The server must create its own UI entity (definition
   `0x04000000000001F1`) in the match snapshot and mount the HUD graph on it, with the begin
   variables the HUD screens wait on as entity variables: `0x09BA` (the layer machine's switch
   term), `0x401F` (the dispatcher's preload var) and `0x09FC` (the mode screen's).
2. **Mode.** `HUD_Root` itself is created by the mode machine of UI graph `120B`, and its gate is
   a UI-entity variable the UI manager sets only while the client sits in **mode 5** - the queue
   "match found" state. Mode 5 is entered by the group banner (message 44203) and leaves with it,
   which is why a direct match join never shows a HUD: the client is not in the mode. The test
   join (`--test-map`) therefore rides the queue: the match first joins the queue (44201 plus the
   party-state entry), the banner answers the client's Accept (44104) when it comes and otherwise
   waits out its window, and the join happens at the commit.

One trap on that path: a party state still carrying the queue entry makes the client offer role
selection again while loading into the world, so the entry must be removed from the party state
(a 20700 without it) at the commit - the banner itself dies by its own deadline or by the accept,
not by the party state.


## Which state is what

The extracted 1.74 data answers this per hero without guessing. Every row: the hero's weapon
script (the 01B index), the `CosmeticEntity` slots with the entity definition each builds (the
view model), the `WeaponVolley` slots (the firing entry points; the first is the one
`loadout.volley_target` rides), the `Anim`/`UXPresenter`/`Ability` slot lists. The manager
(0x15) is identical for all of them: states 29 and 33 are what builds the cosmetics, slot 19 is
one shared `Anim`. The shared body graphs are 0x33 (the hero's health pool, a schema entry with
the number), 0x4b (one expiry-tick state) and 0x43 (`Anim` slots 1 and 11, `Ability` slot 5 and
the hero's contact set); each hero adds its own ability graphs, whose slots the extract lists.

Animations and presenters are in the tables but NOT activated by the server today: a live run
showed an active one plays its presentation the moment the body activates - the soldier spawns
into his visor animation, and the symmetra's teleporter placement screen takes her whole view.
They must start as transitions (the state on, then the event), as the volley does. The ult
presentation is the same mechanism: the hero-specific ability graph, its `Anim`/`UXPresenter`
slots listed here.

| hero | weapon script | cosmetic (def) | volley | anim | presenter | ability |
|---|---|---|---|---|---|---|
| Ana | 0xb94 | 59=0x040000000000159C | 5, 43 | 8 | 65, 66 | - |
| Ashe | 0x14fc | 0=0x0400000000002523 | 7, 113 | 69, 70, 74, 78, 79 | 103, 104 | - |
| Baptiste | 0x1932 | 0=0x0400000000002CEE | 6, 26 | 17 | - | - |
| Bastion | 0x12e | - | 14 | 8 | 30, 31 | - |
| Brigitte | 0x1248 | 24, 50=0x0400000000002283 | - | 25, 26, 27, 28, 31, 32, 33, 38 | 23 | - |
| Cassidy | 0x1d2 | 27=0x04000000000002A6 | 11, 15 | 9, 33, 39 | 49, 50 | - |
| D.Va | 0x2b8 | 27=0x0400000000000982 | 5 | 15 | 24, 25 | - |
| Doomfist | 0xd0a | - | 2 | 15, 20, 31, 34 | 39, 40 | - |
| Echo | 0x19e5 | - | 3 | 23, 24, 27, 30 | 11, 12 | - |
| Genji | 0x304 | 30=0x04000000000007A3 | 4, 11 | 7, 9, 10, 16, 17, 18, 25, 26, 39, 40, 61 | 74, 75 | 44, 45 |
| Hanzo | 0xb9 | 0=0x0400000000000171 | 4, 31 | 30, 43, 44 | - | - |
| Junkrat | 0x22b | 1=0x0400000000000452 | 12 | 5, 22 | 26, 27 | - |
| Lúcio | 0x776 | 0=0x0400000000000401 | 12 | 9, 23, 24, 25 | 46, 47, 48 | 18 |
| Mei | 0x5e6 | 0=0x040000000000091E | 13, 16 | 6 | - | 30 |
| Mercy | 0x6b | 0=0x04000000000000EB | - | - | 45 | - |
| Mercy | 0x6d | 0=0x040000000000025E | 6 | 16 | 29, 30 | - |
| Moira | 0xf76 | - | 6 | 34, 36, 37, 38, 62, 64 | - | 109 |
| Orisa | 0xbd4 | 30=0x040000000000149A | 24 | 5, 26, 27 | 44, 45 | - |
| Pharah | 0x23 | 0=0x040000000000003B | 6 | 9 | 24, 25 | - |
| Reaper | 0x19 | 0=0x0400000000000029, 1=0x0400000000000029 | 6 | 13 | 30, 31 | - |
| Reinhardt | 0x48 | 0=0x0400000000000061 | - | 19, 20, 21, 22, 23, 24, 41 | 40 | - |
| Roadhog | 0x1c6 | 16=0x040000000000037C | 9, 12, 14 | 6, 22, 23 | 40, 41 | - |
| Sigma | 0x187f | 1=0x04000000000031A7 | 20, 28 | 5, 18, 29, 30, 31 | 19 | - |
| Soldier: 76 | 0x254 | 11=0x04000000000004BF | 10, 16 | 7, 39 | 64, 65, 66 | 31 |
| Sombra | 0xb1b | 0=0x0400000000001221, 22=0x04000000000015CB | 6 | 17, 33, 55, 56, 57 | - | 45 |
| Symmetra | 0x157c | 0=0x0400000000000215 | 34 | 9 | 61, 62, 63 | 40 |
| Torbjörn | 0x1683 | 26=0x0400000000000147 | 10, 13 | 8 | 31, 32 | - |
| Torbjörn | 0x168c | 0=0x04000000000001BC, 9=0x04000000000001BC | - | 13 | 27 | - |
| Tracer | 0x16 | 0=0x0400000000000017, 1=0x0400000000000017 | 8 | 9 | 30, 31 | - |
| Widowmaker | 0x1b | 0=0x040000000000002A | 6, 9 | 33, 66, 67 | 82, 83 | - |
| Winston | 0xfe | 0=0x04000000000001F2 | - | 8, 20, 21, 22, 23, 36, 37, 38 | 65, 66 | - |
| Wrecking Ball | 0x1514 | - | 9 | 8, 34 | 25, 26 | - |
| Zarya | 0x241 | 0=0x04000000000003CD | 16 | 6 | 52, 53 | - |
| Zenyatta | 0x164 | 72=0x04000000000002DA, 73=0x040000000000028D, 76=0x04000000000002D9, 77=0x040000000000028D, 78=0x040000000000028D, 79=0x040000000000028D, 80=0x040000000000028D, 81=0x040000000000028D, 82=0x040000000000028D, 83=0x040000000000028D, 84=0x040000000000028D | 14, 18 | 8, 48, 49, 50, 51, 52, 53, 66, 67 | 89, 90 | - |

## Open threads

What is known but not implemented, to continue from:

- **Round phase.** Fire-while-reloading and the round countdown read a game-mode statescript
  graph (0x13C0 in the Practice Range, 0x0F4A in quick play). The incoming dispatcher of channel
  1 has no 21600-base handler: round state travels in statescript, and no mode-graph snapshot is
  sent yet, so the client sits in a permanent pre-round.
- **Counter widgets.** The ammo and health readouts bind through widget 0x20E7 with a
  `.025:0x1005` refresh term; the sync-var write hook is documented above, but the readout layer
  of the HUD screens is not fully mounted yet, so the numbers exist (visible on the gun's own
  display) and do not reach the HUD counters.
- **Multi-weapon heroes.** Heroes with swappable weapons (Torbjorn) own two weapon scripts - the
  rivet gun 0x1683 and the hammer 0x168c - and the picture lights both scripts' cosmetic states,
  so the client renders the fused pair. The split is mechanical: light the first script's
  cosmetics and leave the alternates to the manager's swap states (the table above lists each
  script and its definitions).
- **Muzzle flash and tracers** ride state transitions of the weapon graphs: the `Anim` slots
  listed above, entered as transitions (with the state already on) and with their type-3
  preloads mounted.

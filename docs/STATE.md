# Project state

## Works

- Login, the main menu, the lobby hero and lobby scenes (events).
- Loot boxes: all 11 types can be opened.
- The shop: prices come from the catalog, and each item takes the right currency (credits, league tokens or competitive points).
- The three Anniversary Remix scenes.
- The dashboard: profile, events, loot boxes, shop and skins.
- Hero challenges with a Play menu banner (Tracer, Symmetra, Hanzo's Kanezaka) and their rewards.
- Competitive seasons, ratings and the Top 500 board in the menus.

## Partly works

- Practice Range and matchmaking: the game sends its requests (24000 for the Practice Range, 44100 to
  search, 44102 to cancel), and the server starts a match on one: 20600 hands over a game server, the
  game loads the map and spawns the player. What a match is and is not, item by item, is in [WIP.md](../WIP.md).
- `tools/probe_practice.py` can put the game into the "searching" state (message 53000 with state 4,
  answered by 52903). It resets the game back to idle afterwards.

## Does not work yet

- Match flow after "Assemble your team": no round phase, no objectives, no damage. The hero's own
  weapons, HUD readouts and abilities are scripted server-side already (see `--hero-scripts`,
  [docs/HERO-WEAPONS.md](docs/HERO-WEAPONS.md)), but they cover the body and the gun, not the match.

A match runs up to hero select and a live hero body in the world; do not describe it as a playable game yet.

## Data

`data/` holds message layouts and catalogs taken from a 1.68 reference capture and the 1.74 client. Player names in it are anonymized. The raw captures are not included. `ow174/bnet/descriptors.pb` is Battle.net protocol data the server needs; it is not a program.

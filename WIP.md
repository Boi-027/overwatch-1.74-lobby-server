# Work in progress: game server and matchmaking

This branch adds our own game server and real matchmaking. It works in the game, but a lot is missing.

## What works (checked in the game)

- Matchmaking: a search that fills the teams starts a match. Parties are never split, role queue gives
  every player one of the roles they picked. The maps and modes come from the queue cards.
- For tests, the dashboard's "Players to start" starts a match with fewer players (1 = alone).
- The game connects to our UDP game server (port 3730), loads the map and spawns the player.
- Hero select in the Practice Range and in the PvP modes, with skins: the body wears the chosen skin.
  The H key opens hero select again, and a pick mid-match replaces the body under a new entity id.
- PvP modes: "Assemble your team" with a 30 s countdown, then hero select closes.
- Hero statescript behind `--hero-scripts`: the server runs the hero's graphs, and the client shows
  the in-game HUD layers and the hero's gun; the fire button drives the weapon script's volley and
  the ammo counter's variable carries a full magazine. `--test-map MAP:MODE` joins one map through
  the group finder's match-found banner, which is the state the HUD layers need. What every state
  slot of every hero is, and what is still open, is written down in
  [docs/HERO-WEAPONS.md](docs/HERO-WEAPONS.md).
- The team list on the hero select screen shows yourself.
- Walking. Other players see your body move and change heroes.
- Leaving a match from the menu.
- Dashboard: players to start, the default account for the game.

Written but not checked in the game yet: the golden weapon, match chat with the `.hero <name>` and
`.leave` commands, "End all matches" in the dashboard.

## Not done yet

- The HUD's ammo and health readouts: the numbers live in the weapon script's sync vars (and on the
  gun's own display), but the readout layer of the HUD screens is not mounted yet, and the round
  phase travels in a game-mode graph that is not snapshotted, so the client stays in a permanent
  pre-round. The animations and presenters must enter as state transitions, not as active bits:
  an active one plays the moment the body activates (see docs/HERO-WEAPONS.md for the tables and
  the open threads).
- Heroes with swappable weapons (Torbjorn) render both scripts' cosmetics at once: the cosmetic
  states must follow the weapon the manager swaps to, not all light up together.
- Other players' bodies play a run animation while standing.
- Teammates in the hero select team list.
- "Leave the match as a group" does nothing.
- Match phases after "Assemble your team": setup, objectives, the end of the match, damage and health.
- Event modes whose loading waits for a mode script (for example the Halloween ones).
- The custom game browser.

## Data

`data/game_*.json` come from the scripts in `tools/` (`extract_game_*.py`, `dump_schemas.py`), which read a
DataTool dump of the 1.74 client. The game server needs `cryptography` (in `requirements.txt`).

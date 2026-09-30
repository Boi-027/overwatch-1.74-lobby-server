# Work in progress: game server and matchmaking

This branch adds our own game server and real matchmaking. It works in the game, but a lot is missing.

## What works (checked in the game)

- Matchmaking: a search that fills the teams starts a match. Parties are never split, role queue gives
  every player one of the roles they picked. The maps and modes come from the queue cards.
- For tests, the dashboard's "Players to start" starts a match with fewer players (1 = alone).
- The game connects to our UDP game server (port 3730), loads the map and spawns the player.
- Hero select in the Practice Range and in the PvP modes, with skins: the body wears the chosen skin.
  The H key opens hero select again.
- PvP modes: "Assemble your team" with a 30 s countdown, then hero select closes.
- The team list on the hero select screen shows yourself.
- Walking. Other players see your body move and change heroes.
- Leaving a match from the menu.
- Dashboard: players to start, the default account for the game.

Written but not checked in the game yet: the golden weapon, match chat with the `.hero <name>` and
`.leave` commands, "End all matches" in the dashboard.

## Not done yet

- Weapons, abilities and the in-game HUD. The client predicts them but rolls them back without the
  server's answers, so the server has to run the heroes' statescript graphs itself. Started: the graph
  data (`data/game_graphs_174.json.gz`, `ow174/game/script/graph.py`).
- Other players' bodies play a run animation while standing.
- Teammates in the hero select team list.
- "Leave the match as a group" does nothing.
- Match phases after "Assemble your team": setup, objectives, the end of the match, damage and health.
- Event modes whose loading waits for a mode script (for example the Halloween ones).
- The custom game browser.

## Data

`data/game_*.json` come from the scripts in `tools/` (`extract_game_*.py`, `dump_schemas.py`), which read a
DataTool dump of the 1.74 client. The game server needs `cryptography` (in `requirements.txt`).

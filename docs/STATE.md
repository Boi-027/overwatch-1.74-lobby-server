# Project state

## Works

- Login, the main menu, the lobby hero and lobby scenes (events).
- Loot boxes: all 11 types can be opened.
- The shop: prices come from the catalog, and each item takes the right currency (credits, league tokens or competitive points).
- The three Anniversary Remix scenes.
- The dashboard: profile, events, loot boxes, shop and skins.

## Partly works

- Weekly challenges (Tracer, Symmetra): the data is in place, but the game does not pick them up on its own, and their banner and popup do not show.
- Practice Range and matchmaking: the game sends its requests (24000 for the Practice Range, 44100 to search, 44102 to cancel), and the server starts or stops a worker for each. No match actually starts.
- `tools/probe_practice.py` can put the game into the "searching" state (message 53000 with state 4, answered by 52903). It resets the game back to idle afterwards.

## Does not work yet

- Joining a match. Sending a server address (message 20600) gave no network traffic from the game.
- Competitive seasons.

Do not describe this server as able to play matches.

## Data

`data/` holds message layouts and catalogs taken from a 1.68 reference capture and the 1.74 client. Player names in it are anonymized. The raw captures are not included. `ow174/bnet/descriptors.pb` is Battle.net protocol data the server needs; it is not a program.

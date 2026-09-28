# Overwatch 1.74 — Lobby Research Server

[![Windows checks](https://github.com/squeeeezy/overwatch-1.74-lobby-server/actions/workflows/ci.yml/badge.svg)](https://github.com/squeeeezy/overwatch-1.74-lobby-server/actions/workflows/ci.yml)

A compact source distribution for local protocol research, targeting Windows x64 and client **1.74.0.0 / 104319**.

Built on [Boi-027's Overwatch 1.74 Lobby Research](https://github.com/Boi-027/Overwatch-1-v1.74-Lobby-Research), with shared findings and tools from the wider preservation community. See [Credits](CREDITS.md).

## Run

Clone this repository with `git clone https://github.com/squeeeezy/overwatch-1.74-lobby-server.git` and open its directory.

1. Install Python 3.14 x64 and dependencies: `py -3 -m pip install -r requirements.txt`.
2. In an **x64 Native Tools Command Prompt for Visual Studio 2022**, run `relay\build.bat`.
3. Launch your own client with `run_retail.bat --game-exe "X:\path\Overwatch.exe"`.
4. Open the dashboard at **http://127.0.0.1:3725/**.

The game client is not included. A successful Windows checks run also provides a relay-DLL artifact; place `owwfd_relay.dll` in `relay/` if using that build instead of compiling locally.

The launcher starts the local lobby and Battle.net helpers, waits for readiness, and verifies relay injection. Close an existing game before launching again. Existing helpers are reused; restart them after changing Python server code. `--check-only` checks prerequisites without starting the game.

Alternatively run `py -3 server/lobbyserv.py`, then launch the client with `--tank_TournamentMode --lobbyServer=127.0.0.1:3724 --console` (no Battle.net helper or relay required for that route).

## Contents

- `server/`: lobby, profiles, events, shop, dashboard API, local Battle.net, and per-session UDP workers.
- `server/web/`: standalone dashboard; no npm/build step.
- `relay/`: source and injector for the local retail connection. Build the DLL locally.
- `tools/`: launcher, synthetic smoke client, schema extractor, and explicit practice-state probe.
- `data/`: required protocol/catalog fixtures and compact scene metadata; see [data/README.md](data/README.md). Packet captures, memory dumps, private profiles and logs are not included.
- `tests/`: automated regressions.

First launch creates a fresh `Researcher` profile. Keep generated profiles/logs out of shared copies.

## Verify

`py -3 -B -m unittest discover -s tests -v`

Optional JavaScript client tests (Node.js): `node --test tests/test_dashboard_client.mjs`.

With a disposable lobby server running: `py -3 tools/fake_client.py --port 3724 --name SmokeTest`. The smoke client changes its test profile; use a throwaway copy.

## Status

Main menu, cosmetics, boxes, purchases and dashboard are implemented. Anniversary scene fixes were tested with the real client. **Actual match connection/gameplay, competitive seasons and complete native event announcements remain unfinished.** UDP workers are protocol-capture endpoints, not playable game servers. See `docs/STATE.md`.

## Credits

Big thanks to **Boi-027**, **AyakaPS**, **Logo2K**, **Zagrion**, **Sidiusz**, **Blizless**, **overtools**, **Plasmawatch**, **Prometheus**, the community relay authors, and everyone else who contributed findings, tools or testing — including those not named in the surviving notes.

See [CREDITS.md](CREDITS.md) for project links, Logo2K's research notes and attribution details.

MIT-licensed source; see [LICENSE](LICENSE). Original copyright: Arlecchino (2026). Original notices and source attributions are retained.

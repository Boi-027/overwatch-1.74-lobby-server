# Overwatch 1.74 — Lobby Research Server

[![Windows checks](https://github.com/squeeeezy/overwatch-1.74-lobby-server/actions/workflows/ci.yml/badge.svg)](https://github.com/squeeeezy/overwatch-1.74-lobby-server/actions/workflows/ci.yml)

A compact source distribution for local protocol research, targeting Windows x64 and client **1.74.0.0 / 104319**.

Built on [Boi-027's Overwatch 1.74 Lobby Research](https://github.com/Boi-027/Overwatch-1-v1.74-Lobby-Research), with shared findings and tools from the wider preservation community. See [Credits](CREDITS.md).

## Quick start

Clone and enter the repo, then install Python 3.14 x64 and the dependencies:

```
git clone https://github.com/squeeeezy/overwatch-1.74-lobby-server.git
cd overwatch-1.74-lobby-server
py -3 -m pip install -r requirements.txt
```

The game client is **not** included — bring your own `1.74.0.0 / 104319` `Overwatch.exe`. There are two ways to connect it, depending on how much of the menu you need.

### Route A — Tournament mode (simplest, no build)

One command each, no Battle.net emulator and no relay DLL:

```
py -3 server/lobbyserv.py
Overwatch.exe --tank_TournamentMode --lobbyServer=127.0.0.1:3724 --console
```

The client reaches the main menu and the dashboard at **http://127.0.0.1:3725/** drives it live.

**Limitations of tournament mode** — the client runs its reduced *tournament* frontend, not the full retail one, so:

- **No cosmetic lobby hero** in the menu scene (the frontend branches on `IsTournamentMode || IsShowMode` and skips it — this is by design in the client, not a server gap).
- A stripped-down main menu presentation (no retail shop/promo panels; menu options are limited).
- Best for protocol/lobby research, not for showing off the full retail menu.
- On the plus side, `--lobbyMap=<guid>` **is** honoured here, so any of the lobby maps can be forced directly.

### Route B — Retail route (full main menu with the lobby hero)

This runs the normal retail frontend (hero in the scene, shop/promo panels) by logging in through a local Battle.net emulator. It needs the TLS-strip relay DLL (see [About the relay](#about-the-relay) below):

1. Build the relay **once** in an **x64 Native Tools Command Prompt for Visual Studio 2022**: `relay\build.bat`. (Or take the `owwfd_relay.dll` artifact from a successful Windows checks CI run and drop it in `relay/`.)
2. Launch everything with `run_retail.bat --game-exe "X:\path\Overwatch.exe"`.
3. Open the dashboard at **http://127.0.0.1:3725/**.

That is the whole flow — **`run_retail.bat` does everything automatically**: it starts the lobby and Battle.net helpers, launches the client with `--BNetServer=127.0.0.1:1119`, waits for the client to be ready, **injects the relay DLL into it, and verifies the injection**. You never run the injector by hand; `relay/inject.py` exists only for advanced/manual setups where you start the pieces yourself.

Close any running game before launching again; existing helpers are reused, so restart them after changing Python server code. `--check-only` checks prerequisites without starting the game. Note: on the retail route `--lobbyMap` is ignored — only celebration-backed scenes switch (via the dashboard).

### About the relay

On client **1.74** the Battle.net connection is TLS with **public-key pinning**, and the client's code pages are protected (Arxan/ACG), so you cannot simply point it at a local server with a swapped certificate or patch the check out. The **relay** (`relay/owwfd_relay.cpp` → `owwfd_relay.dll`) works around this without defeating any protection:

- It is a small DLL that `run_retail.bat` **injects into the game client at launch** (LoadLibrary via a remote thread).
- Inside the client it finds the live TLS stream object and swaps its send/receive to a **plaintext pipe** aimed at the local Battle.net emulator (`server/bnet`, on port 21119). This is a heap **data** write — it does not modify protected code, so it stays Arxan-compatible.
- The client then speaks the normal (now plaintext) Battle.net WebSocket to our emulator, which answers the login handshake and hands back a referral to the local lobby on `127.0.0.1:3724`.

So the relay is only about getting *past the pinned TLS* on the login socket; all the actual lobby behaviour is plain server code. You build the DLL once and never touch it again — the launcher handles loading it every run. Tournament mode (Route A) skips all of this, which is why it needs no relay or Battle.net emulator.

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

Big thanks to **Boi-027**, the researchers in the **AyakaPS Discord community**, **Logo2K**, **Zagrion**, **Sidiusz**, **Blizless**, **overtools**, **Plasmawatch**, **Prometheus**, the community relay authors, and everyone else who contributed findings, tools or testing — including those not named in the surviving notes.

See [CREDITS.md](CREDITS.md) for project links, Logo2K's research notes and attribution details.

MIT-licensed source; see [LICENSE](LICENSE). Original copyright: Arlecchino (2026). Original notices and source attributions are retained.

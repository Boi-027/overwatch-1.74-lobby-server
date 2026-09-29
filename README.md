# Overwatch 1.74 Lobby Server

An offline lobby server for Overwatch 1.74 (build 104319) on Windows.

## How to start

1. Install [Python](https://www.python.org/downloads/windows/) 3.10 or newer.
2. Double-click `START.bat`.
3. Choose a mode. Press Enter for the normal one (retail).
4. The first time, pick your `Overwatch.exe`.

The server and the game start. Keep the black window open while you play.

To switch modes, close the game and the black window, then start `START.bat` again and choose another mode.

- **Retail**: the full main menu with a hero in the lobby.
- **Tournament**: a simpler menu without the hero.
- **Server only**: only the server. You start the game yourself.

To manage your profile, events and loot boxes, open http://127.0.0.1:3725 in your browser.

The game is not included. You need your own copy of build 1.74.0.0.104319.

## Without START.bat

Open a terminal in this folder and run:

```
py -m ow174
```

It asks for the mode too. To skip the question, give it: `py -m ow174 --mode tournament`. `py -m ow174 --help` lists all options.

## If something goes wrong

- Start the game only with `START.bat` or `py -m ow174`, not from a shortcut.
- If your antivirus blocks it, add this folder and the game folder to its exceptions.
- If the game asks for an email and password, type anything. The server does not check them.
- Logs are in the `logs` folder. Send `logs/ow174.log` when you ask for help.

## For developers

```
py -m pip install -r requirements.txt ruff
py -m ruff check .
py -B -m unittest discover -s tests
```

The code is in `ow174/`. The relay DLL source is in `relay/`.

## Credits

Based on [Boi-027's research](https://github.com/Boi-027/Overwatch-1-v1.74-Lobby-Research). Thanks to everyone listed in [CREDITS.md](CREDITS.md). MIT license.

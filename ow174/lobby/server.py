"""The lobby server: state shared by all sessions, and the loop that accepts connections."""

import logging
import socket
import threading

from ow174.accounts.profile import Profile, battle_tag, load_or_create_profile
from ow174.accounts.registry import Account, Accounts, account_id_for
from ow174.catalog.items import ItemDB
from ow174.catalog.templates import RetailTemplates
from ow174.content import Content, Identity
from ow174.content.leaderboard import Player
from ow174.jam.codec import Schemas
from ow174.jam.groups import FRIENDS, GROUPS, LOBBY
from ow174.jam.handshake import server_handshake
from ow174.jam.values import id16
from ow174.launcher.retail import RetailGames
from ow174.lobby.battle_tag_query import answer_query
from ow174.lobby.handlers import build_router
from ow174.lobby.matchmaker import Matchmaker
from ow174.lobby.research import ClientRecorder
from ow174.lobby.session import FRIEND_CARDS, Session
from ow174.lobby.settings import Settings
from ow174.services.lootbox import LootBoxEngine
from ow174.services.shop import ShopService
from ow174.services.social import Party, Social

log = logging.getLogger("ow174.lobby")

BACKLOG = 16
GOLDEN_WEAPON = "GOLDEN"  # the name of every hero's golden weapon skin; the other one is "DEFAULT"


class LobbyServer:
    """Everything the sessions share: data, accounts, services and the connected sessions."""

    def __init__(self, settings: Settings) -> None:
        paths = settings.paths
        self.settings = settings
        self.schemas = Schemas()
        self.items = ItemDB()
        self.templates = RetailTemplates()
        self.content = Content(self.schemas, self.templates, self.items)
        self.loot = LootBoxEngine(self.content.collection, self.items)
        self.shop = ShopService(self.content.collection, self.items)
        self.game = None  # the game server (ow174.game.server), once start_game_server() ran
        self.matchmaker = Matchmaker(self, settings.test_players)
        self.accounts = Accounts(paths.profiles, paths.template)
        self.content.ranked.places = self._top500_places
        self.social = Social(self.accounts, self.content)
        self.recorder = ClientRecorder(paths.client_log)
        self.router = build_router()
        # Dashboard writes and message handlers change the same profiles, so they take turns.
        self.state_lock = threading.RLock()
        self.sessions: set[Session] = set()
        self.selected: Account | None = None  # the account the dashboard edits
        self.games: RetailGames | None = None  # retail mode: starts a second game for an account
        self._connections = 0
        self._connections_lock = threading.Lock()

    # --- shared operations ---------------------------------------------------------------------

    def dashboard_account(self) -> Account:
        """The account the dashboard edits: the last one that logged in, else the default one (set in
        the dashboard), else the first saved one."""
        if self.selected is None:
            self.selected = (
                self._last_online_account()
                or self._saved_account(self.default_account_name())
                or self.accounts.get(self._first_saved_name())
            )
        return self.selected

    def default_account_name(self) -> str:
        """The account a freshly started game logs in as; "" when none was set."""
        try:
            return self.settings.paths.default_account.read_text(encoding="utf-8").strip()
        except OSError:
            return ""

    def set_default_account(self, name: str) -> None:
        self.settings.paths.default_account.write_text(name + "\n", encoding="utf-8")

    def _saved_account(self, name: str) -> Account | None:
        for saved in self.accounts.all_saved():
            if name and saved.lower() == name.lower():
                return self.accounts.get(saved)
        return None

    def _last_online_account(self) -> Account | None:
        last = None
        for session in list(self.sessions):
            if session.account:
                last = session.account
        return last

    def _first_saved_name(self) -> str:
        saved = self.accounts.all_saved()
        if saved:
            return saved[0]
        return load_or_create_profile(self.settings.paths.template).player_name

    def select_account(self, name: str) -> None:
        self.selected = self.accounts.get(name)

    def session_of(self, account_lo: int) -> Session | None:
        return self.social.sessions.get(account_lo)

    def battle_tag_of(self, name: str) -> str:
        """The BattleTag a name plays with here, without making an account for it."""
        account_lo = account_id_for(name)
        account = self.accounts.by_id(account_lo)
        return account.battle_tag if account else battle_tag(name, account_lo)

    def game_account(self, peer_port: int, server_port: int) -> Account:
        """The account a retail game plays, found by its connection: a second game plays the account
        it was started for, the first game the dashboard's, so "Play as" can switch it."""
        name = self.games.account_of(peer_port, server_port) if self.games else None
        return self.accounts.get(name) if name else self.dashboard_account()

    def leaderboard_players(self) -> list[Player]:
        """Every saved account, as the leaderboard ranks them."""
        players = []
        for name in self.accounts.all_saved():
            account = self.accounts.get(name)
            players.append(Player(account.name, account.profile, Identity.for_account(account.account_lo)))
        return players

    def _top500_places(self, profile: Profile) -> dict[str, int]:
        return self.content.leaderboard.places(profile, self.leaderboard_players())

    def push_profile(self, account: Account | None = None, granted: list[int] | None = None) -> None:
        """Send an edited account's state to its client. The dashboard calls this after an edit;
        `granted` are the items it gave."""
        if account is None:
            account = self.dashboard_account()
        for session in list(self.sessions):
            if session.account is account:
                try:
                    session.push_state(granted)
                except OSError as error:
                    session.log(f"[!] Live update failed: {error}", logging.WARNING)

    def push_settings(self, account: Account) -> None:
        """Send an account's saved settings (20802) to its client again."""
        settings = self.content.player.settings(account.profile)
        for session in list(self.sessions):
            if session.account is account:
                try:
                    session.send(LOBBY, 20802, settings)
                except OSError as error:
                    session.log(f"[!] Settings update failed: {error}", logging.WARNING)

    def reconnect_own_game(self) -> None:
        """Drop the game played on this PC so it logs in again, as the dashboard's account now (the
        menu scene only changes at login). Second games and other players' games stay."""
        second = self.games.second_accounts() if self.games else set()
        for session in list(self.sessions):
            if session.local and (session.account is None or session.account.name not in second):
                session.log("[>>>] Disconnecting for reconnect (dashboard)")
                session.disconnect()

    def notify_friends(self, account: Account) -> None:
        """Show an account's online friends its presence (27113) and card (20809) again, after it logs
        in or out or changes its icon. Not a whole friends list (27100): the client takes that as every
        online friend coming online again and says "X is entering the game" for each (0x7FF789613930)."""
        records = self.social.presence(account)
        for friend in self.social.friends_of(account):
            session = self.session_of(friend.account_lo)
            if session is None:
                continue
            try:
                session.send(FRIENDS, 27113, {"+0x78": records})
                session.send(LOBBY, FRIEND_CARDS, {"+0x78": self.social.friend_cards(friend)})
            except OSError as error:
                session.log(f"[!] Friends update failed: {error}", logging.WARNING)

    def notify_presence(self, account: Account) -> None:
        """Send an account's presence again (27113) after a status change: its online friends see the
        new colour, and its own client the new status (the dropdown and the name card read it from
        there, not from what was picked)."""
        seen = self.social.presence(account)
        updates = [(self.session_of(friend.account_lo), seen) for friend in self.social.friends_of(account)]
        updates.append((self.session_of(account.account_lo), self.social.own_presence(account)))
        for session, records in updates:
            if session is None:
                continue
            try:
                session.send(FRIENDS, 27113, {"+0x78": records})
            except OSError as error:
                session.log(f"[!] Presence update failed: {error}", logging.WARNING)

    def notify_party(self, party: Party) -> None:
        """Send the current party state to every member that is online, and the group to players who
        watch it in the group finder."""
        for member in list(party.members):
            session = self.session_of(member.account_lo)
            if session:
                session.send_all(session.party_messages())
        self.notify_watchers(party)

    def notify_watchers(self, party: Party) -> None:
        """52301 refreshes a watched group while it looks for players; 52302 drops a group that
        stopped, from the client's watched list."""
        open_group = party.listing is not None and party.searching
        for session in list(self.social.sessions.values()):
            if party.party_id not in session.watched_groups:
                continue
            if open_group:
                session.send(GROUPS, 52301, {"+0x78": self.social.group(party)})
                session.log(f"[group] {party.leader.name}'s group changed (52301)")
            else:
                session.watched_groups.discard(party.party_id)
                session.send(GROUPS, 52302, {"+0x78": id16(*party.party_id)})
                session.log(f"[group] {party.leader.name}'s group closed (52302)")

    # --- matches -------------------------------------------------------------------------------

    def start_game_server(self) -> None:
        """Bind the game server's UDP port and start it. Raises OSError when the port is taken. It
        needs the packages of requirements.txt, so it is imported here."""
        from ow174.game.server import GameServer

        game = GameServer(
            self.settings.host, self.settings.game_port, on_leave=self.game_left, skin_of=self.skin_of
        )
        game.start()
        self.game = game

    def skin_of(self, account_lo: int, hero: int) -> tuple[int, bool]:
        """The skin theme and golden weapon a player has equipped on a hero, for the bodies the game
        server creates. No state lock: the game server calls it under its own lock."""
        account = self.accounts.by_id(account_lo)
        if account is None:
            return 0, False
        loadout = self.content.collection.loadout(account.profile, hero)
        skin = self.items.unlocks.get(loadout.get("+0x38") or 0)
        weapon = self.items.unlocks.get(loadout.get("+0x50") or 0)
        return (skin.skin_theme if skin else 0), bool(weapon and weapon.name == GOLDEN_WEAPON)

    def game_left(self, player) -> None:
        """A player's game left its match: back in the menu, it gets its party state again."""
        session = self.session_of(player.account_lo)
        if session is None:
            return
        with self.state_lock:
            self.social.leave_match_chat(session.account)
            self.notify_party(self.social.party_of(session.account))

    # --- connections ---------------------------------------------------------------------------

    def listen(self) -> socket.socket:
        """Bind the lobby port. Raises OSError when it is taken."""
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            listener.bind((self.settings.host, self.settings.port))
        except OSError:
            listener.close()
            raise
        listener.listen(BACKLOG)
        return listener

    def serve_forever(self, listener: socket.socket) -> None:
        """Accept clients until interrupted; every client gets its own thread."""
        try:
            while True:
                sock, address = listener.accept()
                threading.Thread(target=self._serve_client, args=(sock, address), daemon=True).start()
        except KeyboardInterrupt:
            log.info("Server shutdown.")
        finally:
            listener.close()

    def _serve_client(self, sock: socket.socket, address: tuple) -> None:
        try:
            self._handle_connection(sock, address)
        except (ConnectionError, OSError) as error:
            # A client that dials the lobby and drops before or during the handshake is normal, for
            # example while it is still finishing the Battle.net login. One line, no traceback.
            log.info("[lobby] Client disconnected early: %s", error)
        except Exception:
            log.exception("[lobby] Connection error")
        finally:
            sock.close()

    def _handle_connection(self, sock: socket.socket, address: tuple) -> None:
        with self._connections_lock:
            self._connections += 1
            conn_id = self._connections
        log.info("[lobby #%d] Incoming connection from %s:%d", conn_id, address[0], address[1])
        sock.settimeout(30)
        if answer_query(sock, self.battle_tag_of):
            log.info("[lobby #%d] Answered a join's BattleTag question", conn_id)
            return
        channel = server_handshake(sock, conn_id)
        log.info("[lobby #%d] [+] Handshake complete, waiting for the protocol announcement", conn_id)

        session = Session(self, sock, channel, conn_id, address)
        self.sessions.add(session)
        try:
            session.run()
        finally:
            self.sessions.discard(session)

"""The lobby server: state shared by all sessions, and the loop that accepts connections."""

import logging
import socket
import threading

from ow174.accounts.profile import load_or_create_profile
from ow174.accounts.registry import Account, Accounts
from ow174.catalog.items import ItemDB
from ow174.catalog.templates import RetailTemplates
from ow174.content import Content
from ow174.jam.codec import Schemas
from ow174.jam.handshake import server_handshake
from ow174.lobby.handlers import build_router
from ow174.lobby.research import ClientRecorder
from ow174.lobby.session import Session
from ow174.lobby.settings import Settings
from ow174.matches.runtime import MatchManager
from ow174.services.lootbox import LootBoxEngine
from ow174.services.shop import ShopService
from ow174.services.social import Party, Social

log = logging.getLogger("ow174.lobby")

BACKLOG = 16


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
        self.matches: MatchManager | None = None
        if settings.game_port > 0:
            self.matches = MatchManager(paths.matches, base_port=settings.game_port)
        self.accounts = Accounts(paths.profiles, paths.template)
        self.social = Social(self.accounts, self.content)
        self.recorder = ClientRecorder(paths.client_log)
        self.router = build_router()
        # Dashboard writes and message handlers change the same profiles, so they take turns.
        self.state_lock = threading.RLock()
        self.sessions: set[Session] = set()
        self.selected: Account | None = None  # the account the dashboard edits
        self._connections = 0
        self._connections_lock = threading.Lock()

    # --- shared operations ---------------------------------------------------------------------

    def dashboard_account(self) -> Account:
        """The account the dashboard edits: the last one that logged in, else the first saved one."""
        if self.selected is None:
            self.selected = self._last_online_account() or self.accounts.get(self._first_saved_name())
        return self.selected

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

    def push_profile(self, account: Account | None = None) -> None:
        """Send an edited account's state to its client. The dashboard calls this after an edit."""
        if account is None:
            account = self.dashboard_account()
        for session in list(self.sessions):
            if session.account is account:
                try:
                    session.push_state()
                except OSError as error:
                    session.log(f"[!] Live update failed: {error}", logging.WARNING)

    def reconnect_all(self) -> None:
        """Drop every client so it logs in again (the menu scene only changes at login)."""
        for session in list(self.sessions):
            session.log("[>>>] Disconnecting for reconnect (dashboard)")
            session.disconnect()

    def broadcast_presence(self) -> None:
        """Send everyone online a fresh friends list."""
        for session in list(self.social.sessions.values()):
            session.send_social()

    def notify_party(self, party: Party) -> None:
        """Send the current party state to every member that is online."""
        for member in list(party.members):
            session = self.session_of(member.account_lo)
            if session:
                session.send_all(session.party_messages())

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
            if self.matches is not None:
                self.matches.close()

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
        channel = server_handshake(sock, self.settings.host, self.settings.port, conn_id)
        log.info("[lobby #%d] [+] Handshake complete, waiting for the protocol announcement", conn_id)

        session = Session(self, sock, channel, conn_id)
        self.sessions.add(session)
        try:
            session.run()
        finally:
            self.sessions.discard(session)

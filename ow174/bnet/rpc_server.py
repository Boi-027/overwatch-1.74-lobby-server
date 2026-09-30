"""Battle.net RPC server: WebSocket on 127.0.0.1:21119, without TLS (the relay DLL removes it).

The login, step by step:
  1. ConnectionService.Connect. The client lists the listener services it offers; we answer with
     server time and ids.
  2. AuthenticationServer.Logon. We answer with AuthenticationClient.OnLogonComplete at once, as
     the player picked in the dashboard. Tested in game: the client needs no login form and no
     ticket, with or without a cached one, so switching accounts needs no typing.
  3. GameUtilities.ProcessTask. We answer with a ReferralInfo that sends the client to our lobby
     server. The client then takes the normal retail path and shows the full main menu.

A response goes to service id 254 and repeats the token of the request. A notification names the
listener by its hash and by the id the client gave it, and uses a token from our own counter.
"""

import asyncio
import logging
import time
from collections.abc import Callable
from typing import NamedTuple

import websockets

from ow174.bnet import protocol as P

log = logging.getLogger("ow174.bnet")

SUBPROTOCOL = "v1.rpc.battle.net"
RESPONSE_SERVICE_ID = 254

# Where the ReferralInfo sends the client: our lobby server.
LOBBY_HOSTV4 = "127.0.0.1:3724"
REFERRAL_CID = 379775058
# The referral's session keys. With all zeros the lobby does not validate the state blob, as in
# tournament mode.
ZERO_KEY = bytes(64)

# The high words (type tags) of the Battle.net account and Overwatch game account ids; the low words
# come from the Player. Evidence that the client accepts these tags: the plasmawatch server.
ACCOUNT_HIGH = 0x0100000000000000
GAME_ACCOUNT_HIGH = 0x020000010050726F  # the tag ends in "Pro", the Overwatch program id
SESSION_KEY = bytes(range(1, 65))  # the client only needs a 64-byte key to be present


class Session:
    """One client connection."""

    def __init__(self, ws, server):
        self.ws = ws
        self.server = server
        self.peer = ws.remote_address
        self._token = 0x40000000  # far above the client's own tokens, so they never clash
        self.listener_ids: dict[int, int] = {}  # listener service hash -> id the client gave it

    def next_token(self) -> int:
        self._token = (self._token + 1) & 0xFFFFFFFF
        return self._token

    def log(self, msg: str):
        self.server.log(f"[{self.peer[0]}:{self.peer[1]}] {msg}")

    async def send_response(self, request_header, body: bytes):
        header = P.Header()
        header.service_id = RESPONSE_SERVICE_ID
        header.method_id = 0
        header.token = request_header.token
        header.status = 0
        await self.ws.send(P.encode_frame(header, body))

    async def send_notification(self, service_hash: int, method_id: int, body: bytes):
        header = P.Header()
        header.service_id = self.listener_ids.get(service_hash, 0)
        header.service_hash = service_hash
        header.method_id = method_id
        header.token = self.next_token()
        await self.ws.send(P.encode_frame(header, body))


def _service_name(service_hash: int) -> str:
    return P.SERVICE_NAMES.get(service_hash, hex(service_hash))


class Player(NamedTuple):
    """Who the client logs in as: the Battle.net account, its Overwatch game account and BattleTag."""

    account: int
    game_account: int
    battle_tag: str


def _logon_result(player: Player):
    result = P.LogonResult()
    result.error_code = 0
    result.account_id.high = ACCOUNT_HIGH
    result.account_id.low = player.account
    game_account = result.game_account_id.add()
    game_account.high = GAME_ACCOUNT_HIGH
    game_account.low = player.game_account
    result.email = "player@localhost"
    result.available_region.append(1)
    result.connected_region = 1
    result.battle_tag = player.battle_tag
    result.geoip_country = "US"
    result.session_key = SESSION_KEY
    result.restricted_mode = False
    return result


def _add_attribute(attributes, name: str, **value) -> None:
    attribute = attributes.add()
    attribute.name = name
    for kind, data in value.items():
        setattr(attribute.value, kind, data)


def _fill_referral(attributes) -> None:
    _add_attribute(attributes, "response_type", string_value="ReferralInfo")
    for key_name in ("k0", "k1", "k2", "k3"):
        _add_attribute(attributes, key_name, blob_value=ZERO_KEY)
    _add_attribute(attributes, "cid", uint_value=REFERRAL_CID)
    _add_attribute(attributes, "hostv4", string_value=LOBBY_HOSTV4)


class BNetRpcServer:
    def __init__(
        self,
        host="127.0.0.1",
        port=21119,
        *,
        player: Callable[[tuple], Player],
    ):
        """`player` gives the Player a client logs in as, from its (host, port)."""
        self.host = host
        self.port = port
        self.player = player

    def log(self, msg: str):
        log.info("[bnet] %s", msg)

    async def start(self):
        """Bind the RPC port and serve in the background of the running event loop."""
        server = await websockets.serve(
            self._handle, self.host, self.port, subprotocols=[SUBPROTOCOL], max_size=8 << 20
        )
        self.log(f"RPC (WebSocket {SUBPROTOCOL}) listening on {self.host}:{self.port}")
        return server

    async def _handle(self, ws):
        session = Session(ws, self)
        session.log("connected")
        try:
            async for message in ws:
                if isinstance(message, str):
                    session.log(f"unexpected text frame: {message[:80]!r}")
                    continue
                await self._dispatch(session, message)
        except websockets.ConnectionClosed:
            pass
        finally:
            session.log("disconnected")

    async def _dispatch(self, session: Session, data: bytes):
        try:
            header, body = P.decode_frame(data)
        except Exception as e:  # noqa: BLE001 - a malformed frame must not drop the connection
            session.log(f"frame decode error: {e} ({data[:32].hex()})")
            return
        service = header.service_hash
        name = P.SERVICE_NAMES.get(service, f"0x{service:08x}")
        session.log(f"<- {name}.method{header.method_id} token={header.token} size={len(body)}")

        if service == P.CONNECTION_HASH:
            await self._connection(session, header, body)
        elif service == P.AUTH_SERVER_HASH:
            await self._auth(session, header, body)
        elif service == P.GAME_UTILITIES_HASH:
            await self._game_utilities(session, header, body)
        elif service == P.ACCOUNT_HASH:
            # Empty answers are enough for the client to go on.
            session.log(f"   AccountService.method{header.method_id} (empty ack)")
            await session.send_response(header, b"")
        else:
            session.log(f"   (no handler for {name}.method{header.method_id})")

    async def _connection(self, session, header, body):
        if header.method_id == P.CONNECT:
            await self._connect(session, header, body)
        elif header.method_id in (P.KEEP_ALIVE, P.ECHO):
            await session.send_response(header, b"")
        else:
            session.log(f"   ConnectionService.method{header.method_id} unhandled")

    async def _connect(self, session, header, body):
        request = P.ConnectRequest.FromString(body)
        bind = request.bind_request
        # Services the client uses get ids 1, 2, 3... in the order it lists them.
        used = [f"{_service_name(s.hash)}=id{i + 1}" for i, s in enumerate(bind.imported_service)]
        if used:
            session.log("   imports: " + ", ".join(used))
        offered = []
        for service in bind.exported_service:
            session.listener_ids[service.hash] = service.id
            offered.append(f"{_service_name(service.hash)}=id{service.id}")
        if offered:
            session.log("   exports: " + ", ".join(offered))

        response = P.ConnectResponse()
        response.server_id.label = 0
        response.server_id.epoch = int(asyncio.get_event_loop().time())
        if request.HasField("client_id"):
            response.client_id.CopyFrom(request.client_id)
        for i in range(len(bind.imported_service)):
            response.bind_response.imported_service_id.append(i + 1)
        response.server_time = int(time.time() * 1000)
        response.use_bindless_rpc = request.use_bindless_rpc
        await session.send_response(header, response.SerializeToString())

    async def _auth(self, session, header, body):
        if header.method_id == P.LOGON:
            await self._logon(session, header, body)
        else:
            session.log(f"   AuthenticationServer.method{header.method_id} unhandled")
            await session.send_response(header, b"")

    async def _logon(self, session, header, body):
        request = P.LogonRequest.FromString(body)
        session.log(
            f"   Logon program={request.program!r} locale={request.locale!r} version={request.version!r}"
        )
        await session.send_response(header, b"")
        player = self.player(session.peer)
        await session.send_notification(
            P.AUTH_CLIENT_HASH, P.ON_LOGON_COMPLETE, _logon_result(player).SerializeToString()
        )
        session.log(f"   -> OnLogonComplete as {player.battle_tag} (account 0x{player.account:X})")

    async def _game_utilities(self, session, header, body):
        if header.method_id != P.PROCESS_TASK:
            session.log(f"   GameUtilities.method{header.method_id} unhandled")
            await session.send_response(header, b"")
            return
        request = P.ProcessTaskRequest.FromString(body)
        session.log(f"   ProcessTask attrs={[attribute.name for attribute in request.attribute]}")
        response = P.ProcessTaskResponse()
        _fill_referral(response.result)
        await session.send_response(header, response.SerializeToString())
        session.log(f"   -> ProcessTaskResponse ReferralInfo hostv4={LOBBY_HOSTV4}")

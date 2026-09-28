"""Battle.net RPC server over WebSocket on 127.0.0.1:1119 (plaintext; the relay DLL strips TLS).

Answers the retail login handshake and hands the client a ReferralInfo pointing at our lobby server,
so the client takes the retail path and shows the full main menu.

Addressing follows plasmawatch (a working reference against this client era):
  - client -> server request: header.service_hash selects the service, header.token is echoed back.
  - server -> client response: service_id = 254, token echoed, body = the response message.
  - server -> client notification (listener call): header.service_hash = listener hash,
    header.method_id = listener method, header.token = a fresh per-connection token.
"""
import asyncio
import struct

import websockets

from . import protocol as P

SUBPROTOCOL = "v1.rpc.battle.net"
RESPONSE_SERVICE_ID = 254

# The referral we hand back. hostv4 is where the client then dials the lobby (our lobbyserv on 3724).
LOBBY_HOSTV4 = "127.0.0.1:3724"
REFERRAL_CID = 379775058
# Zero session keys: the lobby then needs no state-blob validation (same as tournament mode).
ZERO_KEY = bytes(64)

# A stand-in Battle.net account/game-account id (values proven working by plasmawatch).
# high carries the account/program type tag; low is the id.
ACCOUNT_HIGH = 72057594037927936        # 0x0100000000000000
ACCOUNT_LOW = 379775058
GAME_ACCOUNT_HIGH = 144115192376095343  # Overwatch ("Pro") game-account tag
GAME_ACCOUNT_LOW = 559865145
SESSION_KEY = bytes(range(1, 65))       # 64-byte session key; the client just needs one present


class Session:
    def __init__(self, ws, server):
        self.ws = ws
        self.server = server
        self.peer = ws.remote_address
        self._token = 0x40000000  # server-initiated tokens live in a high range to avoid clashes
        self.imported_services: dict[int, int] = {}   # hash -> id we assigned (client imports)
        self.exported_services: dict[int, int] = {}    # hash -> id the client assigned (listeners)

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
        header.service_id = self.exported_services.get(service_hash, 0)
        header.service_hash = service_hash
        header.method_id = method_id
        header.token = self.next_token()
        await self.ws.send(P.encode_frame(header, body))


class BNetRpcServer:
    def __init__(self, host="127.0.0.1", port=1119, web_url=None, logger=None):
        self.host = host
        self.port = port
        self.web_url = web_url or "http://127.0.0.1:6969/battlenet/login?externalChallenge=login&app=pro"
        self._logger = logger

    def log(self, msg: str):
        if self._logger:
            self._logger(f"[bnet] {msg}")
        else:
            print(f"[bnet] {msg}", flush=True)

    async def serve(self):
        self.log(f"RPC (WebSocket {SUBPROTOCOL}) listening on {self.host}:{self.port}")
        async with websockets.serve(
            self._handle, self.host, self.port,
            subprotocols=[SUBPROTOCOL], max_size=8 << 20,
        ):
            await asyncio.Future()

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
        except Exception as e:
            session.log(f"frame decode error: {e} ({data[:32].hex()})")
            return
        svc = header.service_hash
        name = P.SERVICE_NAMES.get(svc, f"0x{svc:08x}")
        session.log(f"<- {name}.method{header.method_id} token={header.token} size={len(body)}")

        if svc == P.CONNECTION_HASH:
            await self._connection(session, header, body)
        elif svc == P.AUTH_SERVER_HASH:
            await self._auth(session, header, body)
        elif svc == P.GAME_UTILITIES_HASH:
            await self._game_utilities(session, header, body)
        elif svc == P.ACCOUNT_HASH:
            await self._account(session, header, body)
        else:
            session.log(f"   (no handler for {name}.method{header.method_id})")

    # ---- ConnectionService ----
    async def _connection(self, session, header, body):
        if header.method_id == P.CONNECT:
            req = P.ConnectRequest.FromString(body)
            bind = req.bind_request
            for i, svc in enumerate(bind.imported_service):
                session.imported_services[svc.hash] = i + 1
            for svc in bind.exported_service:
                session.exported_services[svc.hash] = svc.id
            if bind.imported_service:
                session.log("   imports: " + ", ".join(
                    f"{P.SERVICE_NAMES.get(s.hash, hex(s.hash))}=id{i + 1}"
                    for i, s in enumerate(bind.imported_service)))
            if bind.exported_service:
                session.log("   exports: " + ", ".join(
                    f"{P.SERVICE_NAMES.get(s.hash, hex(s.hash))}=id{s.id}"
                    for s in bind.exported_service))

            resp = P.ConnectResponse()
            resp.server_id.label = 0
            resp.server_id.epoch = int(asyncio.get_event_loop().time())
            if req.HasField("client_id"):
                resp.client_id.CopyFrom(req.client_id)
            for i in range(len(bind.imported_service)):
                resp.bind_response.imported_service_id.append(i + 1)
            resp.server_time = self._now_ms()
            resp.use_bindless_rpc = req.use_bindless_rpc
            await session.send_response(header, resp.SerializeToString())
        elif header.method_id in (P.KEEP_ALIVE, P.ECHO):
            await session.send_response(header, b"")
        else:
            session.log(f"   ConnectionService.method{header.method_id} unhandled")

    # ---- AuthenticationServer ----
    async def _auth(self, session, header, body):
        if header.method_id == P.LOGON:
            req = P.LogonRequest.FromString(body)
            session.log(f"   Logon program={req.program!r} locale={req.locale!r} version={req.version!r}")
            await session.send_response(header, b"")  # NoData ack
            challenge = P.ChallengeExternalRequest()
            challenge.payload_type = "web_auth_url"
            challenge.payload = self.web_url.encode()
            await session.send_notification(
                P.CHALLENGE_NOTIFY_HASH, P.ON_EXTERNAL_CHALLENGE, challenge.SerializeToString())
            session.log(f"   -> OnExternalChallenge {self.web_url}")
        elif header.method_id == P.VERIFY_WEB_CREDENTIALS:
            req = P.VerifyWebCredentialsRequest.FromString(body)
            session.log(f"   VerifyWebCredentials ticket={req.web_credentials[:32]!r}")
            await session.send_response(header, b"")  # NoData ack
            result = P.LogonResult()
            result.error_code = 0
            result.account_id.high = ACCOUNT_HIGH
            result.account_id.low = ACCOUNT_LOW
            game = result.game_account_id.add()
            game.high = GAME_ACCOUNT_HIGH
            game.low = GAME_ACCOUNT_LOW
            result.email = "player@localhost"
            result.available_region.append(1)
            result.connected_region = 1
            result.battle_tag = "Player#11111"
            result.geoip_country = "US"
            result.session_key = SESSION_KEY
            result.restricted_mode = False
            await session.send_notification(
                P.AUTH_CLIENT_HASH, P.ON_LOGON_COMPLETE, result.SerializeToString())
            session.log("   -> OnLogonComplete (error_code=0)")
        else:
            session.log(f"   AuthenticationServer.method{header.method_id} unhandled")
            await session.send_response(header, b"")

    # ---- GameUtilities v2 ----
    async def _game_utilities(self, session, header, body):
        if header.method_id == P.PROCESS_TASK:
            req = P.ProcessTaskRequest.FromString(body)
            attrs = [a.name for a in req.attribute]
            session.log(f"   ProcessTask attrs={attrs}")
            resp = P.ProcessTaskResponse()
            self._fill_referral(resp.result)
            await session.send_response(header, resp.SerializeToString())
            session.log(f"   -> ProcessTaskResponse ReferralInfo hostv4={LOBBY_HOSTV4}")
        else:
            session.log(f"   GameUtilities.method{header.method_id} unhandled")
            await session.send_response(header, b"")

    def _fill_referral(self, result):
        def add(name, **kw):
            attr = result.add()
            attr.name = name
            for key, value in kw.items():
                setattr(attr.value, key, value)

        add("response_type", string_value="ReferralInfo")
        add("k0", blob_value=ZERO_KEY)
        add("k1", blob_value=ZERO_KEY)
        add("k2", blob_value=ZERO_KEY)
        add("k3", blob_value=ZERO_KEY)
        add("cid", uint_value=REFERRAL_CID)
        add("hostv4", string_value=LOBBY_HOSTV4)

    # ---- AccountService (minimal acks so the client keeps going) ----
    async def _account(self, session, header, body):
        session.log(f"   AccountService.method{header.method_id} (empty ack)")
        await session.send_response(header, b"")

    @staticmethod
    def _now_ms() -> int:
        import time
        return int(time.time() * 1000)


def main():
    server = BNetRpcServer()
    asyncio.run(server.serve())


if __name__ == "__main__":
    main()

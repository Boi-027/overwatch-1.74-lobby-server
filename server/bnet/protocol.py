"""BGS wire protocol: message classes from the embedded descriptors, service/method tables, framing.

Everything here is derived from descriptors.pb, the FileDescriptorSet extracted from the 1.74 client
image. No .proto compilation is needed; the descriptor pool builds working message classes at import.
"""
import struct
from pathlib import Path

from google.protobuf import descriptor_pb2, descriptor_pool, message_factory

DESCRIPTORS = Path(__file__).with_name("descriptors.pb")


def _build_pool() -> descriptor_pool.DescriptorPool:
    fds = descriptor_pb2.FileDescriptorSet.FromString(DESCRIPTORS.read_bytes())
    by_name = {f.name: f for f in fds.file}
    pool = descriptor_pool.DescriptorPool()
    added: set[str] = set()

    def add(name: str):
        if name in added or name not in by_name:
            return
        for dep in by_name[name].dependency:
            add(dep)
        pool.Add(by_name[name])
        added.add(name)

    for name in list(by_name):
        add(name)
    return pool


POOL = _build_pool()


def message_class(full_name: str):
    descriptor = POOL.FindMessageTypeByName(full_name)
    try:
        return message_factory.GetMessageClass(descriptor)  # protobuf >= 4.22
    except AttributeError:  # pragma: no cover - older protobuf
        return message_factory.MessageFactory(POOL).GetPrototype(descriptor)


# ---- message classes used by the login flow ----
Header = message_class("bgs.protocol.Header")
ProcessId = message_class("bgs.protocol.ProcessId")
EntityId = message_class("bgs.protocol.EntityId")
NoData = message_class("bgs.protocol.NoData")

ConnectRequest = message_class("bgs.protocol.connection.v1.ConnectRequest")
ConnectResponse = message_class("bgs.protocol.connection.v1.ConnectResponse")
BindRequest = message_class("bgs.protocol.connection.v1.BindRequest")
BindResponse = message_class("bgs.protocol.connection.v1.BindResponse")

LogonRequest = message_class("bgs.protocol.authentication.v1.LogonRequest")
LogonResult = message_class("bgs.protocol.authentication.v1.LogonResult")
VerifyWebCredentialsRequest = message_class("bgs.protocol.authentication.v1.VerifyWebCredentialsRequest")
ChallengeExternalRequest = message_class("bgs.protocol.challenge.v1.ChallengeExternalRequest")

ProcessTaskRequest = message_class("bgs.protocol.game_utilities.v2.client.ProcessTaskRequest")
ProcessTaskResponse = message_class("bgs.protocol.game_utilities.v2.client.ProcessTaskResponse")
Attribute = message_class("bgs.protocol.v2.Attribute")
Variant = message_class("bgs.protocol.v2.Variant")


def fnv1a32(text: str) -> int:
    h = 0x811C9DC5
    for byte in text.encode():
        h = ((h ^ byte) * 0x01000193) & 0xFFFFFFFF
    return h


# Exported services the client calls on us, keyed by the fixed32 service_hash it puts in the header.
# The hash is FNV1a-32 of the original bnet.protocol.* descriptor name (see service options).
CONNECTION_HASH = fnv1a32("bnet.protocol.connection.ConnectionService")          # 0x65446991
AUTH_SERVER_HASH = fnv1a32("bnet.protocol.authentication.AuthenticationServer")  # 0x0decfc01
ACCOUNT_HASH = fnv1a32("bnet.protocol.account.AccountService")                   # 0x62da0891
GAME_UTILITIES_HASH = fnv1a32("bnet.protocol.game_utilities.v2.client.GameUtilities")  # 0x5dbb51c2

# Imported (listener) services we call on the client. The client binds these in ConnectRequest and we
# reply with an assigned service_id per hash; notifications then go out on that service_id.
AUTH_CLIENT_HASH = fnv1a32("bnet.protocol.authentication.AuthenticationClient")  # 0x71240e35
CHALLENGE_NOTIFY_HASH = fnv1a32("bnet.protocol.challenge.ChallengeNotify")       # 0xbbda171f
ACCOUNT_NOTIFY_HASH = fnv1a32("bnet.protocol.account.AccountNotify")             # 0x54dfda17

SERVICE_NAMES = {
    CONNECTION_HASH: "ConnectionService",
    AUTH_SERVER_HASH: "AuthenticationServer",
    ACCOUNT_HASH: "AccountService",
    GAME_UTILITIES_HASH: "GameUtilities.v2",
    AUTH_CLIENT_HASH: "AuthenticationClient",
    CHALLENGE_NOTIFY_HASH: "ChallengeNotify",
    ACCOUNT_NOTIFY_HASH: "AccountNotify",
}

# method ids on the wire (from the method options in the descriptors)
CONNECT = 1
BIND = 2
ECHO = 3
FORCE_DISCONNECT = 4
KEEP_ALIVE = 5
ENCRYPT = 6
REQUEST_DISCONNECT = 7

LOGON = 1
VERIFY_WEB_CREDENTIALS = 7
GENERATE_WEB_CREDENTIALS = 8

PROCESS_TASK = 1
GET_ALL_VALUES_FOR_ATTRIBUTE = 2

ON_SERVER_STATE_CHANGE = 4
ON_LOGON_COMPLETE = 5
ON_EXTERNAL_CHALLENGE = 3


def encode_frame(header, body: bytes = b"") -> bytes:
    """[u16be header_len][header][body] — the payload of one WebSocket binary message."""
    if header.size != len(body):
        header.size = len(body)
    header_bytes = header.SerializeToString()
    return struct.pack(">H", len(header_bytes)) + header_bytes + body


def decode_frame(data: bytes):
    """Return (Header, body_bytes) from one WebSocket binary message."""
    header_len = struct.unpack_from(">H", data, 0)[0]
    header = Header.FromString(data[2:2 + header_len])
    body = data[2 + header_len:]
    return header, body

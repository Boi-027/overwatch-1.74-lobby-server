"""Battle.net (BGS) RPC protocol: message classes, service hashes, method ids and framing.

The message classes come from descriptors.pb, the protobuf descriptor set extracted from the 1.74
client, so no .proto files need to be compiled.

Each WebSocket binary message is one RPC frame: a u16 big-endian header length, a protobuf Header,
then the body. The header says which service and method a request is for, and carries a token that
the answer repeats.
"""

import struct
from pathlib import Path

from google.protobuf import descriptor_pb2, descriptor_pool, message_factory

DESCRIPTORS = Path(__file__).with_name("descriptors.pb")


def _build_pool() -> descriptor_pool.DescriptorPool:
    """Load descriptors.pb. A file can only be added after the files it imports."""
    descriptor_set = descriptor_pb2.FileDescriptorSet.FromString(DESCRIPTORS.read_bytes())
    files = {file.name: file for file in descriptor_set.file}
    pool = descriptor_pool.DescriptorPool()
    added: set[str] = set()

    def add(name: str) -> None:
        if name in added or name not in files:
            return
        for dependency in files[name].dependency:
            add(dependency)
        pool.Add(files[name])
        added.add(name)

    for name in files:
        add(name)
    return pool


POOL = _build_pool()


def message_class(full_name: str):
    return message_factory.GetMessageClass(POOL.FindMessageTypeByName(full_name))


Header = message_class("bgs.protocol.Header")

ConnectRequest = message_class("bgs.protocol.connection.v1.ConnectRequest")
ConnectResponse = message_class("bgs.protocol.connection.v1.ConnectResponse")

LogonRequest = message_class("bgs.protocol.authentication.v1.LogonRequest")
LogonResult = message_class("bgs.protocol.authentication.v1.LogonResult")

ProcessTaskRequest = message_class("bgs.protocol.game_utilities.v2.client.ProcessTaskRequest")
ProcessTaskResponse = message_class("bgs.protocol.game_utilities.v2.client.ProcessTaskResponse")


def fnv1a32(text: str) -> int:
    value = 0x811C9DC5
    for byte in text.encode():
        value = ((value ^ byte) * 0x01000193) & 0xFFFFFFFF
    return value


# A request names its service by a hash: FNV-1a 32 of the service's original "bnet.protocol.*" name.
# Services on our side, which the client calls:
CONNECTION_HASH = fnv1a32("bnet.protocol.connection.ConnectionService")  # 0x65446991
AUTH_SERVER_HASH = fnv1a32("bnet.protocol.authentication.AuthenticationServer")  # 0x0decfc01
ACCOUNT_HASH = fnv1a32("bnet.protocol.account.AccountService")  # 0x62da0891
GAME_UTILITIES_HASH = fnv1a32("bnet.protocol.game_utilities.v2.client.GameUtilities")  # 0x5dbb51c2

# Listener services on the client side, which we call to push notifications. The client lists them
# in its ConnectRequest together with the id it gave each one.
AUTH_CLIENT_HASH = fnv1a32("bnet.protocol.authentication.AuthenticationClient")  # 0x71240e35
CHALLENGE_NOTIFY_HASH = fnv1a32("bnet.protocol.challenge.ChallengeNotify")  # 0xbbda171f
ACCOUNT_NOTIFY_HASH = fnv1a32("bnet.protocol.account.AccountNotify")  # 0x54dfda17

SERVICE_NAMES = {
    CONNECTION_HASH: "ConnectionService",
    AUTH_SERVER_HASH: "AuthenticationServer",
    ACCOUNT_HASH: "AccountService",
    GAME_UTILITIES_HASH: "GameUtilities.v2",
    AUTH_CLIENT_HASH: "AuthenticationClient",
    CHALLENGE_NOTIFY_HASH: "ChallengeNotify",
    ACCOUNT_NOTIFY_HASH: "AccountNotify",
}

# Method ids, per service, from the method options in the descriptors.
# ConnectionService
CONNECT = 1
BIND = 2
ECHO = 3
FORCE_DISCONNECT = 4
KEEP_ALIVE = 5
ENCRYPT = 6
REQUEST_DISCONNECT = 7
# AuthenticationServer
LOGON = 1
VERIFY_WEB_CREDENTIALS = 7
GENERATE_WEB_CREDENTIALS = 8
# GameUtilities
PROCESS_TASK = 1
GET_ALL_VALUES_FOR_ATTRIBUTE = 2
# ChallengeNotify
ON_EXTERNAL_CHALLENGE = 3
# AuthenticationClient
ON_SERVER_STATE_CHANGE = 4
ON_LOGON_COMPLETE = 5


def encode_frame(header, body: bytes = b"") -> bytes:
    """Build one WebSocket binary message from a Header and a body."""
    # Setting size, even to 0, puts the field on the wire, so an empty body leaves it unset.
    if header.size != len(body):
        header.size = len(body)
    header_bytes = header.SerializeToString()
    return struct.pack(">H", len(header_bytes)) + header_bytes + body


def decode_frame(data: bytes):
    """Split one WebSocket binary message into (Header, body bytes)."""
    header_length = struct.unpack_from(">H", data, 0)[0]
    header = Header.FromString(data[2 : 2 + header_length])
    body = data[2 + header_length :]
    return header, body

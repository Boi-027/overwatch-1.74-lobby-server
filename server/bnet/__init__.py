"""Local Battle.net (BGS) emulator for the Overwatch 1.74 retail login route.

The client speaks the Battle.net RPC protocol over a WebSocket on port 1119 (TLS is stripped by the
injected relay DLL, so we receive plaintext). This package answers the login handshake and hands the
client a ReferralInfo that points at our lobby server, so the client takes the retail code path and
shows the full main menu instead of the tournament-mode variant.
"""

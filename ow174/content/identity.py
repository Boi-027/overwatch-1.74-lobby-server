"""The ids that identify one connected player to the client."""

import os
from dataclasses import dataclass

from ow174.jam.values import id16

PARTY_ENTITY_TYPE = 0x1D << 40
ID_HIGH_TAG = 1 << 56  # the high half every id in these messages carries, see id16()


def _random_u64() -> int:
    return int.from_bytes(os.urandom(8), "little")


@dataclass
class Identity:
    """The account, session and party ids of one player, as the lobby messages use them."""

    account_lo: int
    session: tuple
    party_id: tuple
    party_entity: tuple

    @classmethod
    def create(cls, account_lo: int, connection_seq: int) -> "Identity":
        """Fresh ids for a new connection."""
        connection = connection_seq & 0xFFFFFFFF
        party_entity_lo = connection | PARTY_ENTITY_TYPE | ID_HIGH_TAG
        return cls(
            account_lo,
            (connection, 1),
            (_random_u64(), _random_u64()),
            (party_entity_lo, _random_u64()),
        )

    @classmethod
    def for_account(cls, account_lo: int) -> "Identity":
        """An identity for a player who is not connected (friends, party members)."""
        return cls(account_lo, (account_lo, 1), (0, 0), (0, 0))

    @property
    def account(self) -> dict:
        """The account id in the two-part form the messages use."""
        return id16(self.account_lo)

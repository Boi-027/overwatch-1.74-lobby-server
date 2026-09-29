"""Priority passes for the role queues.

The game's own texts say how they work: players get them by queueing as All Roles,
more for a win than a loss (1266E.07C), and spend one to find a match faster with one role
(1266F.07C). A pass taken for a search comes back when the search ends without a match (128E5.07C,
12965.07C). Without real matches passes are only set on the dashboard.

The client keeps a count per pool, keyed by the queue card's pool identifier (0C7 m_DD50F6A1, read
at card +0x250 by 0x7FF7893497D0): one pool for Quick Play, one for Competitive. 58501 carries every
count and replaces the client's list (0x7FF789609E50). The client shows "N/40"; 40 is server config
value 17EB12A9.
"""

from ow174.accounts.profile import Profile

POOLS = {"quick_play": 0x0D8000000000843C, "competitive": 0x0D8000000000843D}
MAX_PASSES = 40


def pool_name(pool: int) -> str | None:
    for name, key in POOLS.items():
        if key == pool:
            return name
    return None


def count(profile: Profile, pool: int) -> int:
    name = pool_name(pool)
    return int((profile.priority_passes or {}).get(name, 0)) if name else 0


def change(profile: Profile, pool: int, amount: int) -> None:
    name = pool_name(pool)
    if name is None:
        return
    passes = dict(profile.priority_passes or {})
    passes[name] = max(0, min(MAX_PASSES, int(passes.get(name, 0)) + amount))
    profile.priority_passes = passes


def counts(profile: Profile) -> dict:
    """58501: the player's passes in every pool."""
    return {"+0x78": {"+0x0": [{"+0x0": key, "+0x8": count(profile, key)} for key in POOLS.values()]}}

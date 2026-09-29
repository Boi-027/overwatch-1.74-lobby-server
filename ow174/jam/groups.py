"""CRCs of the protocol groups the lobby speaks, with the message ids seen in each.

A group is a set of messages that the client announces by CRC. Names are ours; the ids come from the
client's schemas (data/schemas_174.json).
"""

# Sent by the server
IN_CONNECT = 0xF5FF548A  # 20500 hello, 20502 player record, 20504 content keys, 20505 preload
PARTY = 0x2411DE56  # 20700 party state, 20701 invite
LOBBY = 0x1A9879A4  # 20800-20825 lobby status, settings (20802), career profile (20807)
CUSTOM_GAMES = 0x21286B49  # 23301 custom game maps and training maps
PROGRESSION_IN = 0x779A8581  # 24300 record, 24305 box result, 24306 unlock granted, 24307 credits
HERO_CATALOG = 0x70E5A247  # 24900 catalog, 24901 unlock granted, 24902 item equipped
MODE_RULES = 0xB9DCA497  # 27202 game modes per hero and map
ENDORSEMENTS = 0x79BFDDBE  # 52002 player, 52005 own summary, 52006 pending
FRIENDS = 0xF470E2AB  # 27100 friends, 27104 news, 27113 presence, 27116/27117 whispers
PERMISSIONS = 0xBCD57A46  # 55500 account features (its frames carry a leading u32)
PROFILES = 0xD5550262  # 39001 profile status, 39002 profile summary
NAME_REPLY = 0x981D6A52  # 58301 names
ARCADE = 0x00A8654E
EVENTS = 0x8B77C823  # 38900 celebrations, 38901 new celebrations, 38902 challenge progress
CONFIG = 0x1732EBF2  # 36600 dynamic config, 36602 clock, 36603
REPLAYS = 0x5894D085
STORE = 0x4BAD7A7E  # 26400 products, 26404
CHAT_IN = 0x5F913F6B  # 20400 message, 20401 channel members, 20402 joined, 20404 left
HANDOFF = 0x074DAD18  # 20600 game-server handoff
MATCH_STATE = 0xA1498A6A  # 56200 match assignment, 56201 id list, 56202 stub

# Sent by the client
OUT_CONNECT = 0x3C7E3468  # 21800 login, 21809 locale
PROGRESSION_OUT = 0x7F4F46CB  # 24201 open box, 24203 purchase
GALLERY_OUT = 0xB68870B8  # 24500 equip {hero, unlock, slot}, 24501 purchase {hero, unlock}
SOCIAL_OUT = 0x75D32AE2  # 22200-22204 settings, 22206 career profile request
NAME_QUERY = 0x46DC9706  # 58202 account ids
STORE_QUERY = 0x5217E4CD  # 26500
CHAT_OUT = 0x28A2A1CD  # 21700 send {channel, text, flags}, 21701 who
PARTY_OUT = 0xB2FF5A5E  # 22102 invite, 22103 answer, 22105 kick, 22107 leave
FRIENDS_OUT = 0xA287DF29  # 27004 whisper {token, target, sender, text}
GAME_REQUEST = 0xA6E53896  # 24000 create game (kind 2 / flags 4 is the Practice Range)
MATCHMAKE = 0x1C6EC712  # 44100 enter queue, 44102 cancel
GROUP_FINDER = 0x9529F0ED  # 52201 group state, 52203 chosen roles (client to server only)

# Periodic client reports that carry nothing the lobby needs
TELEMETRY = frozenset({0xC64B397E, 0x692C511B})

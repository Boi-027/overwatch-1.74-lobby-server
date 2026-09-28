#!/usr/bin/env python3
"""
Overwatch 1.74 Lobby Server
---------------------------
  - JAM handshake up to the type-13 lobby channel (tournament route).
  - Every message is encoded/decoded with the client's own schemas
    (data/schemas_174.json, see jam_codec.py and tools/dump_schemas.py).
  - Several players at once: each typed name is an account with its own profile
    in profiles/, everyone is friends, parties and chat work between sessions,
    and a virtual friend ("Bot") is always online (social.py).
  - Login: hello, player record, content keys, preload, events, party state,
    endorsements, friends and presence, chat, progression, hero catalog and the
    retail capture's static lobby data (arcade, config, store...).
  - Loot boxes, equipping, purchases, name lookups, career profiles, store.
  - Dashboard edits are pushed live to the connected client.
  - Every client message is decoded into client_msgs.log (telemetry skipped).

Client Launch:
    Overwatch.exe --tank_TournamentMode --lobbyServer=127.0.0.1:3724
"""

import sys
import os
import json
import socket
import struct
import time
import hmac
import hashlib
import argparse
import threading
import traceback
import math
from copy import deepcopy
from pathlib import Path

try:
    sys.stdout.reconfigure(line_buffering=True, errors="replace")
except Exception:
    pass

CURRENT_DIR = Path(__file__).resolve().parent
REPO_ROOT = CURRENT_DIR.parent
if str(CURRENT_DIR) not in sys.path:
    sys.path.insert(0, str(CURRENT_DIR))

from crypto import Jam, build_state_blob_292, HELLO_CLIENT, HELLO_SERVER, ZERO_KEY, DIFFICULTY
from protocol import send_frame
from storage import load_or_create_profile, save_profile
from jam_codec import Schemas, DecodeError, to_jsonable
from retail import RetailCapture
from items import ItemDB
from content import (
    LobbyContent, Identity, OUT_CONNECT, PROGRESSION_IN, PROGRESSION_OUT, NAME_QUERY, NAME_REPLY,
    STORE, STORE_QUERY, SOCIAL_OUT, TELEMETRY, HERO_CATALOG, GALLERY_OUT, PARTY, IN_CONNECT, FRIENDS,
    PERMISSIONS, LOBBY,
)
from social import Accounts, Social
from lootbox import LootBoxEngine, box_id_from
from shop import ShopService, ShopError
from dashboard import start_dashboard
import matchmaker
from matchmaker import (
    MATCHMAKE, HANDOFF, ENTER_QUEUE, MATCH_HANDOFF,
    build_handoff,
)
from match_runtime import MatchManager

CLIENT_LOG = REPO_ROOT / "client_msgs.log"
INJECT_FILE = REPO_ROOT / "inject.jsonl"  # research hook, see inject_watcher
CHAT_IN = 0x5F913F6B          # 20400 message, 20401 joined channel
CHAT_OUT = 0x28A2A1CD         # 21700 send {channel, text, flags}
PARTY_OUT = 0xB2FF5A5E        # 22102 invite, 22103 answer, 22105 kick, 22107 leave
FRIENDS_OUT = 0xA287DF29      # 27004 whisper {token, target, sender, text}
GAME_REQUEST = 0xA6E53896    # 24000 create game; kind2/flags4 observed for Practice Range
CANCEL_QUEUE = 44102         # Confirmed by manual Arcade search/cancel trace 2026-09-28


def recvn(c: socket.socket, n: int, timeout=30) -> bytes:
    c.settimeout(timeout)
    buf = b""
    while len(buf) < n:
        chunk = c.recv(n - len(buf))
        if not chunk:
            raise ConnectionError("client closed")
        buf += chunk
    return buf


class Server:
    """Shared state: schemas, content, accounts, sessions, parties and chat."""

    def __init__(self, template_path: Path, cfg):
        self.cfg = cfg
        self.schemas = Schemas()
        self.items = ItemDB()
        self.retail = RetailCapture(self.schemas)
        self.content = LobbyContent(self.schemas, self.retail, self.items)
        self.loot = LootBoxEngine(self.content, self.items)
        self.shop = ShopService(self.content, self.items)
        self.state_lock = threading.RLock()
        self.matches = MatchManager(REPO_ROOT / 'logs' / 'matches', base_port=cfg.game_port) if cfg.game_port > 0 else None
        self.accounts = Accounts(REPO_ROOT / "profiles", template_path)
        self.social = Social(self.accounts, self.content)
        self.connections = 0
        self.connections_lock = threading.Lock()
        self.sessions = set()
        self.selected = None  # account the dashboard edits

    def dashboard_account(self):
        if self.selected is None:
            online = [s.account for s in list(self.sessions) if s.account]
            saved = self.accounts.all_saved()
            self.selected = online[-1] if online else self.accounts.get(
                saved[0] if saved else load_or_create_profile(self.accounts.template).player_name)
        return self.selected

    def select_account(self, name: str):
        self.selected = self.accounts.get(name)

    def push_profile(self, account=None):
        """Send the edited account's state to its client (dashboard edits)."""
        acc = account if account is not None else self.dashboard_account()
        for session in list(self.sessions):
            if session.account is acc:
                try:
                    session.push_state()
                except OSError as e:
                    session.log(f"[!] Live update failed: {e}")

    def reconnect_all(self):
        """Drop every client so it logs in again (the menu scene only changes at login)."""
        for session in list(self.sessions):
            session.log("[>>>] Disconnecting for reconnect (dashboard)")
            try:
                session.sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass

    def session_of(self, account_lo: int):
        return self.social.sessions.get(account_lo)

    def broadcast_presence(self):
        """Everyone online gets a fresh friends list and General channel roster."""
        for session in list(self.social.sessions.values()):
            session.send_social()


class LobbySession:
    def __init__(self, server: Server, sock: socket.socket, txjam: Jam, cid: int, seq: int):
        self.srv, self.sock, self.txjam, self.cid, self.seq = server, sock, txjam, cid, seq
        self.account = None
        self.ident = None
        self.send_lock = threading.Lock()
        self.logged_in = False
        self.party_channel = None
        self.crc_at = {}
        self.wire_of = {}
        self.handlers = {
            (OUT_CONNECT, 21800): self.on_login,
            (PROGRESSION_OUT, 24201): self.on_open_box,
            (PROGRESSION_OUT, 24203): self.on_purchase,
            (NAME_QUERY, 58202): self.on_name_query,
            (STORE_QUERY, 26500): self.on_store_query,
            (SOCIAL_OUT, 22206): self.on_profile_query,
            (GALLERY_OUT, 24500): self.on_equip,
            (GALLERY_OUT, 24501): self.on_gallery_purchase,
            (CHAT_OUT, 21700): self.on_chat,
            (CHAT_OUT, 21701): self.on_chat_who,
            (PARTY_OUT, 22102): self.on_party_invite,
            (PARTY_OUT, 22103): self.on_party_answer,
            (PARTY_OUT, 22105): self.on_party_kick,
            (PARTY_OUT, 22107): self.on_party_leave,
            (FRIENDS_OUT, 27004): self.on_whisper,
            (SOCIAL_OUT, 22200): lambda v: self.on_settings(v, "+0x78"),
            (SOCIAL_OUT, 22201): lambda v: self.on_settings(v, "+0x108"),
            (SOCIAL_OUT, 22202): lambda v: self.on_settings(v, "+0x10D"),
            (SOCIAL_OUT, 22203): lambda v: self.on_settings(v, "+0x120"),
            (SOCIAL_OUT, 22204): self.on_setting_values,
            (MATCHMAKE, ENTER_QUEUE): self.on_enter_queue,
            (MATCHMAKE, CANCEL_QUEUE): self.on_cancel_queue,
            (GAME_REQUEST, 24000): self.on_game_request,
        }

    @property
    def profile(self):
        return self.account.profile

    def log(self, msg):
        who = f" {self.account.name}" if self.account else ""
        print(f"[lobby #{self.cid}{who}] {msg}")

    # ------------------------------------------------------------ wire

    def announce(self, crcs: list):
        self.crc_at = {i + 1: crc for i, crc in enumerate(crcs)}
        self.wire_of = {crc: i for i, crc in self.crc_at.items()}
        missing = [f"{c:08X}" for c in crcs if c not in self.srv.schemas.groups]
        self.log(f"[+] Client announced {len(crcs)} protocol groups" + (f" (no schema: {missing})" if missing else ""))

    def send(self, crc: int, msg_id: int, value: dict) -> bool:
        wire = self.wire_of.get(crc)
        if wire is None:
            self.log(f"[!] Skipped {msg_id}: protocol {crc:08X} not announced")
            return False
        body = self.srv.schemas.encode(crc, msg_id, value)
        self.send_raw(bytes([wire, msg_id - self.srv.schemas.base(crc)]) + body)
        return True

    def send_raw(self, payload: bytes):
        with self.send_lock:
            send_frame(self.sock, self.txjam, payload)

    def send_all(self, messages: list):
        return sum(1 for crc, msg_id, value in messages if self.send(crc, msg_id, value))

    def dispatch(self, wire: int, offset: int, body: bytes):
        crc = self.crc_at.get(wire)
        if crc is None or crc not in self.srv.schemas.groups:
            self.log(f"[<<<] wire={wire} off={offset} len={len(body)} (unknown protocol)")
            return
        msg_id = self.srv.schemas.base(crc) + offset
        try:
            value = self.srv.schemas.decode(crc, msg_id, body)
        except (DecodeError, KeyError, struct.error) as e:
            self.log(f"[<<<] {crc:08X}/{msg_id} len={len(body)} undecodable: {e}")
            value = None
        if crc in TELEMETRY:
            return
        self._record(crc, msg_id, body, value)
        handler = self.handlers.get((crc, msg_id))
        if handler and value is not None and (self.logged_in or msg_id == 21800):
            try:
                # Dashboard writes and game handlers share the same profile
                # transaction boundary, including equips, boxes and settings.
                with self.srv.state_lock:
                    handler(value)
            except Exception:
                self.log(f"[!] Handler for {crc:08X}/{msg_id} failed:")
                traceback.print_exc()
        else:
            self.log(f"[<<<] {crc:08X}/{msg_id} (wire {wire}) {json.dumps(to_jsonable(value), ensure_ascii=False)[:200]}")

    def _record(self, crc, msg_id, body, value):
        entry = {"t": time.strftime("%H:%M:%S"), "conn": self.cid, "player": self.account.name if self.account else None,
                 "crc": f"{crc:08X}", "msg": msg_id, "value": to_jsonable(value) if value is not None else None,
                 "raw": body.hex()}
        with open(CLIENT_LOG, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    # ------------------------------------------------------------ state

    def save(self):
        self.account.save()

    def party_messages(self) -> list:
        social = self.srv.social
        party = social.party_of(self.account)
        msgs = [(PARTY, 20700, social.party_state(party))]
        if len(party.members) > 1 and self.party_channel != party.chat_channel:
            msgs.append((CHAT_IN, 20402, {"+0x78": party.chat_channel}))
            self.party_channel = party.chat_channel
        elif len(party.members) <= 1 and self.party_channel is not None:
            msgs.append((CHAT_IN, 20404, {"+0x78": self.party_channel}))
            self.party_channel = None
        return msgs

    def send_social(self):
        social = self.srv.social
        self.send_all([(FRIENDS, 27100, social.friends_state(self.account))])

    def push_state(self):
        """Live refresh after a dashboard edit: card, party, currencies, collection, events."""
        if not self.logged_in:
            return
        srv, profile = self.srv, self.profile
        earned = srv.content.claim_challenge_rewards(profile)
        if earned:
            self.save()
        messages = [m for m in srv.content.live_messages(profile, self.ident) if m[:2] != (PARTY, 20700)]
        messages += self.party_messages() + [srv.content.unlock_granted(g) for g in earned]
        sent = self.send_all(messages)
        self.log(f"[>>>] Live update: {sent} messages" + (f", {len(earned)} challenge rewards" if earned else ""))

    # ------------------------------------------------------------ matchmaker

    def on_enter_queue(self, value):
        """Allocate a real endpoint. Wire assignment is a separate research stage."""
        self.log(f"[MM] ENTER QUEUE (44100) {json.dumps(to_jsonable(value), ensure_ascii=False)[:300]}")
        mode = value['+0x78']['+0x0']['+0x0']
        self._allocate_game(mode, 'queue')

    def on_cancel_queue(self, value):
        if self.srv.matches is not None:
            self.srv.matches.cancel(self.cid, mode=value['+0x78']['+0x0']['+0x0'])
        self.log('[MM] CANCEL QUEUE (44102): stopped this search instance')

    def on_game_request(self, value):
        if value.get('+0x78') == 2 and value.get('+0xA8') == 4:
            # Captured Practice Range request contains a creation kind, not a mode GUID.
            self._allocate_game(0, 'practice')
        else:
            self.log(f'[MM] Unmapped create-game request: {to_jsonable(value)}')

    def _allocate_game(self, mode, activity):
        if self.srv.matches is None:
            self.log('[MM] Instance allocation disabled (--game-port 0)')
            return
        instance = self.srv.matches.request(self.cid, self.account.name, mode, activity)
        self.log(f'[MM] {activity}: instance {instance.directory.name}, PID {instance.process.pid}, '
                 f'UDP 127.0.0.1:{instance.port}; awaiting verified game protocol')

    def send_match_handoff(self):
        """Tell the client to connect to our UDP game server (074DAD18/20600)."""
        try:
            ok = self.send(HANDOFF, MATCH_HANDOFF, build_handoff())
        except Exception:
            self.log("[MM] handoff build/send failed:")
            traceback.print_exc()
            return
        self.log(f"[MM] -> handoff (074DAD18/20600) {matchmaker.GAME_HOST}:{matchmaker.GAME_PORT}: "
                 + ("SENT" if ok else "SKIPPED (client did not announce 074DAD18)"))

    # ------------------------------------------------------------ login / logout

    def on_login(self, value: dict):
        srv = self.srv
        typed = (value.get("+0x78") or "").strip()
        # The retail frontend sends no name (it has no name screen): log in as the dashboard's account.
        self.account = srv.accounts.get(typed) if typed else srv.dashboard_account()
        self.ident = Identity.create(self.account.account_lo, self.seq)
        srv.selected = self.account
        old = srv.social.sessions.get(self.account.account_lo)
        if old is not None and old is not self:
            old.log("[>>>] Replaced by a new login")
            try:
                old.sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
        srv.social.sessions[self.account.account_lo] = self
        self.log(f"[<<<] Login as '{self.account.name}' (account 0x{self.account.account_lo:X})")

        profile = self.profile
        earned = srv.content.claim_challenge_rewards(profile)
        if earned:
            self.save()
        messages = [m for m in srv.content.login_messages(profile, self.ident) if m[:2] != (PARTY, 20700)]
        messages += self.party_messages()
        messages += [(FRIENDS, 27100, srv.social.friends_state(self.account)),
                     (CHAT_IN, 20402, {"+0x78": srv.social.general})]
        messages += [srv.content.unlock_granted(g) for g in earned]
        sent = self.send_all(messages)
        self.logged_in = True
        for g in earned:
            self.log(f"[>>>] Challenge reward: {srv.items.describe(g)}")
        self.log(f"[>>>] Sent {sent}/{len(messages)} login messages (Level {profile.level}, Credits {profile.credits}, "
                 f"Boxes {len(profile.loot_boxes)}, Extra unlocks {len(profile.unlocked_items)}, "
                 f"online: {', '.join(a.name for a in srv.social.online())})")
        for other in list(srv.social.sessions.values()):
            if other is not self:
                other.send_social()
        threading.Thread(target=self.after_menu_ready, daemon=True).start()

    def after_menu_ready(self):
        """Messages whose client systems only exist once the main menu is up: 55500 (account
        features, which gate chat) is dropped on the first login after the game starts, and
        20802 (player settings) writes into the settings system without a null check."""
        for delay, with_settings in ((3, True), (7, False)):
            time.sleep(delay)
            if not self.logged_in:
                return
            try:
                self.send(PERMISSIONS, 55500, self.srv.content.account_features())
                if with_settings:
                    self.send(LOBBY, 20802, self.srv.content.player_settings(self.profile))
            except OSError:
                return

    def on_disconnect(self):
        srv = self.srv
        if srv.matches is not None:
            srv.matches.cancel(self.cid)
        if not self.account or srv.social.sessions.get(self.account.account_lo) is not self:
            return
        del srv.social.sessions[self.account.account_lo]
        party = srv.social.leave(self.account)
        if party:
            self.notify_party(party)
        srv.broadcast_presence()

    # ------------------------------------------------------------ progression

    def on_open_box(self, value: dict):
        srv, profile = self.srv, self.profile
        box_id = box_id_from(value)
        drops, box_type, credits, box_name = srv.loot.open_loot_box(box_id, profile, self.account.path)
        self.send(PROGRESSION_IN, 24305, srv.content.box_result((box_id, 0), drops))
        self.send(PROGRESSION_IN, 24307, {"+0x78": profile.credits})
        self.log(f"[>>>] Opened {box_name} box #{box_id}: " + "; ".join(
            f"{srv.items.describe(d['unlock'])}{' (dup +' + str(d['credits']) + ')' if d['duplicate'] else ''}" for d in drops))

    def on_equip(self, value: dict):
        srv, profile = self.srv, self.profile
        hero, guid, slot = value.get("+0x78", 0), value.get("+0x80", 0), value.get("+0x88", 0)
        if not srv.content.equip(profile, hero, guid, slot):
            self.log(f"[<<<] Equip {srv.items.describe(guid)} on 0x{hero:X} slot {slot}: unknown slot")
            return
        self.save()
        self.send(HERO_CATALOG, 24902, {"+0x78": hero, "+0x80": guid, "+0x88": slot})
        if hero == 0:
            self.send(IN_CONNECT, 20502, {"+0x78": srv.content.player_record(profile, self.ident)})
            self.notify_party(srv.social.party_of(self.account))
            srv.broadcast_presence()
        self.log(f"[>>>] Equipped {srv.items.describe(guid)} ({srv.items.hero_name(hero) if hero else 'account'}, slot {slot})")

    def on_gallery_purchase(self, value: dict):
        self.purchase(value.get("+0x80", 0))

    def on_purchase(self, value: dict):
        self.purchase(value.get("+0x78", 0))

    def purchase(self, guid: int):
        srv = self.srv
        try:
            with srv.state_lock:
                profile = deepcopy(self.profile)
                receipt = srv.shop.purchase(profile, guid)
                save_profile(profile, self.account.path)
                self.account.profile = profile
        except ShopError as error:
            self.log(f"[<<<] Purchase {srv.items.describe(guid)} refused: {error}")
            return
        self.send(*srv.content.unlock_granted(guid))
        # 24300 carries all three verified balance fields; a credits-only update
        # leaves the OWL and competitive-point displays stale after a purchase.
        self.send(PROGRESSION_IN, 24300, srv.content.progression(profile))
        self.log(f"[>>>] Purchased {srv.items.describe(guid)} for {receipt['price']} "
                 f"{receipt['currency']}, balance {getattr(profile, receipt['currency'])}")

    # ------------------------------------------------------------ lookups

    def on_name_query(self, value: dict):
        names = []
        for account in value.get("+0x78") or []:
            acc = self.srv.accounts.by_id(account.get("+0x0", 0))
            name = acc.name if acc else f"Player{account.get('+0x0', 0) & 0xFFFF}"
            names.append({"+0x0": account, "+0x10": 0, "+0x18": name})
        self.send(NAME_REPLY, 58301, {"+0x78": names})

    def on_profile_query(self, value: dict):
        target = value.get("+0x88") or {}
        acc = self.srv.accounts.by_id(target.get("+0x0", 0))
        if acc is None:
            self.send_all(self.srv.content.career_profile(self.profile, self.ident, target))
        else:
            ident = Identity.create(acc.account_lo, self.seq)
            self.send_all(self.srv.content.career_profile(acc.profile, ident, target))
        self.log(f"[>>>] Career profile for {acc.name if acc else hex(target.get('+0x0', 0))}")

    def on_store_query(self, value: dict):
        for msg_id, store in self.srv.retail.all(STORE):
            self.send(STORE, msg_id, store)

    # ------------------------------------------------------------ chat

    def on_chat(self, value: dict):
        social = self.srv.social
        channel, text = value.get("+0x78") or {}, value.get("+0x90") or ""
        message = social.chat_message(channel, self.account, text, value.get("+0xB8", 0))
        members = social.channel_members(channel)
        for acc in members:
            session = self.srv.session_of(acc.account_lo)
            if session:
                session.send(CHAT_IN, 20400, message)
        self.log(f"[chat #{channel.get('+0x10')}] {self.account.name}: {text}")
        if social.accounts.bot in members:
            reply = social.chat_message(channel, social.accounts.bot, f"{self.account.name}, I hear you: {text}")
            for acc in members:
                session = self.srv.session_of(acc.account_lo)
                if session:
                    session.send(CHAT_IN, 20400, reply)

    def on_chat_who(self, value: dict):
        self.send(CHAT_IN, 20401, self.srv.social.who(value.get("+0x78") or {}))

    def on_settings(self, value: dict, field: str):
        """22200-22203: the client saves a block of its settings (20802 field); sent back at login."""
        self.profile.settings[field] = value.get("+0x78")
        self.save()
        self.log(f"[<<<] Settings {field} saved")

    def on_setting_values(self, value: dict):
        """22204: changed key/value settings [{value, setting id}], merged by id into 20802 +0x130."""
        values = {v["+0x8"]: v for v in self.profile.settings.get("+0x130", [])}
        for v in value.get("+0x78") or []:
            values[v.get("+0x8")] = v
        self.profile.settings["+0x130"] = list(values.values())
        self.save()
        self.log(f"[<<<] {len(value.get('+0x78') or [])} setting values saved")

    def on_whisper(self, value: dict):
        """27004 (Battle.net whisper): 27116 completes the request, the target gets 27117 {sender, text}.
        The sender's client shows its own line itself (27118 would repeat it)."""
        social, me = self.srv.social, self.account.account_lo
        text = value.get("+0xA0") or ""
        self.send(FRIENDS, 27116, {"+0x78": value.get("+0x78", 0), "+0x80": 0})
        ids = [(value.get(k) or {}).get("+0x0", 0) for k in ("+0x80", "+0x90")]
        target = next((a for a in map(social.accounts.by_id, ids) if a and a.account_lo != me), None)
        if target is None:
            self.log(f"[<<<] Whisper to unknown player {value}")
            return
        self.log(f"[whisper] {self.account.name} -> {target.name}: {text}")
        if target.virtual:
            self.send(FRIENDS, 27117, {"+0x78": target.account, "+0x88": f"{self.account.name}, I hear you: {text}"})
            return
        session = self.srv.session_of(target.account_lo)
        if session:
            session.send(FRIENDS, 27117, {"+0x78": self.account.account, "+0x88": text})

    # ------------------------------------------------------------ parties

    def notify_party(self, party):
        social = self.srv.social
        for acc in list(party.members):
            session = self.srv.session_of(acc.account_lo)
            if session:
                session.send_all(session.party_messages())

    def on_party_invite(self, value: dict):
        social = self.srv.social
        target = social.accounts.by_id((value.get("+0x78") or {}).get("+0x0", 0))
        if target is None or target is self.account:
            self.log(f"[<<<] Party invite for unknown player {value}")
            return
        party = social.invite(self.account, target)
        self.log(f"[<<<] Party invite -> {target.name}")
        if target.virtual:
            social.join(target, party)
            self.notify_party(party)
            hello = social.chat_message(party.chat_channel, target, "Hi! I'm in the group.")
            self.send(CHAT_IN, 20400, hello)
            return
        session = self.srv.session_of(target.account_lo)
        if session:
            session.send(PARTY, 20701, {"+0x78": social.player_record(self.account), "+0xE0": social.player_record(target)})

    def on_party_answer(self, value: dict):
        social = self.srv.social
        accept = bool(value.get("+0x98"))
        party = next((p for p in set(social.parties.values()) if self.account.account_lo in p.invites), None)
        if party is None:
            self.log(f"[<<<] Party answer without invite {value}")
            return
        if accept:
            social.join(self.account, party)
            self.notify_party(party)
            self.log(f"[<<<] Joined {party.leader.name}'s party")
        else:
            party.invites.pop(self.account.account_lo, None)
            self.log(f"[<<<] Declined {party.leader.name}'s party")

    def on_party_kick(self, value: dict):
        social = self.srv.social
        party = social.party_of(self.account)
        target = social.accounts.by_id((value.get("+0x78") or {}).get("+0x0", 0))
        if target is None or party.leader is not self.account or target not in party.members:
            return
        social.leave(target)
        self.notify_party(party)
        session = self.srv.session_of(target.account_lo)
        if session:
            session.send_all(session.party_messages())
        self.log(f"[<<<] Kicked {target.name}")

    def on_party_leave(self, value: dict):
        social = self.srv.social
        party = social.leave(self.account)
        if party:
            self.notify_party(party)
        self.send_all(self.party_messages())
        self.log("[<<<] Left the party")

    # ------------------------------------------------------------ loop

    def run(self, rxjam: Jam):
        try:
            self._run(rxjam)
        except OSError:
            self.log("Client disconnected.")
        finally:
            self.on_disconnect()

    def _run(self, rxjam: Jam):
        dec_buf = b""
        last_keepalive = time.time()
        while True:
            try:
                self.sock.settimeout(2.0)
                data = self.sock.recv(65536)
            except socket.timeout:
                if time.time() - last_keepalive > 10.0:
                    self.send_raw(b"\x00\x03")
                    last_keepalive = time.time()
                continue
            if not data:
                self.log("Client closed socket.")
                return

            dec_buf += rxjam.crypt(data)
            while len(dec_buf) >= 3:
                n = int.from_bytes(dec_buf[:3], "big")
                if len(dec_buf) < 3 + n:
                    break
                frame, dec_buf = dec_buf[3:3 + n], dec_buf[3 + n:]
                if len(frame) < 2:
                    continue
                wire, offset = frame[0], frame[1]
                if wire == 0:
                    if offset == 0:
                        count = struct.unpack_from("<I", frame, 2)[0]
                        self.announce([struct.unpack_from("<I", frame, 6 + i * 4)[0] for i in range(count)])
                        self.send_raw(b"\x00\x02")
                        self.log("[>>>] Base ack (00 02): CONNECTED")
                    elif offset == 3:
                        last_keepalive = time.time()
                        self.send_raw(b"\x00\x03")
                    continue
                self.dispatch(wire, offset, frame[2:])


def handle_connection(c: socket.socket, addr, server: Server):
    with server.connections_lock:
        server.connections += 1
        cid = server.connections
    cfg = server.cfg

    print("\n" + "=" * 60)
    print(f"[lobby #{cid}] Incoming connection from {addr[0]}:{addr[1]}")
    print("=" * 60)

    d = recvn(c, len(HELLO_CLIENT))
    if d != HELLO_CLIENT:
        print(f"[lobby #{cid}] ERROR: Bad hello: {d}")
        return
    c.sendall(HELLO_SERVER)

    d = recvn(c, 40)
    cn = d[8:40]
    sn = os.urandom(32)
    srv_rand = os.urandom(32)
    c.sendall(srv_rand + bytes([DIFFICULTY]) + sn)

    d = recvn(c, 40)
    mac1 = d[8:40]
    if mac1 != hmac.new(ZERO_KEY, cn + sn, hashlib.sha256).digest():
        print(f"[lobby #{cid}] ERROR: MAC1 proof mismatch")
        return

    mac2 = hmac.new(ZERO_KEY, sn + cn, hashlib.sha256).digest()
    s2c_key = hmac.new(ZERO_KEY, cn + sn, hashlib.sha256).digest()
    c2s_key = hmac.new(ZERO_KEY, sn + cn, hashlib.sha256).digest()

    seq = (int(time.time() * 1000) ^ (cid << 16)) & 0xFFFFFFFF
    txjam, rxjam = Jam(s2c_key), Jam(c2s_key)
    c.sendall(mac2 + txjam.crypt(build_state_blob_292(seq, cfg.host, cfg.port)))
    print(f"[lobby #{cid}] [+] Handshake complete (channel type 13). Waiting for announcement...")

    session = LobbySession(server, c, txjam, cid, seq)
    server.sessions.add(session)
    try:
        session.run(rxjam)
    finally:
        server.sessions.discard(session)


def send_research_message(server, item):
    """Send one experiment to the named connection; legacy untargeted files still work."""
    if 'expires_at' in item:
        deadline = item['expires_at']
        if isinstance(deadline, bool) or not isinstance(deadline, (int, float)) or not math.isfinite(deadline):
            raise ValueError('expires_at must be a finite Unix timestamp')
        if time.time() >= deadline:
            return 0
    crc, msg_id = int(str(item['crc']), 16), int(item['msg'])
    body = bytes.fromhex(item['hex']) if 'hex' in item else server.schemas.encode(crc, msg_id, item['value'])
    target_conn = int(item['conn']) if 'conn' in item else None
    target_player = str(item['player']).casefold() if 'player' in item else None
    sent = 0
    for session in list(server.social.sessions.values()):
        if target_conn is not None and session.cid != target_conn:
            continue
        if target_player is not None and session.account.name.casefold() != target_player:
            continue
        wire = session.wire_of.get(crc)
        if wire is None:
            continue
        session.send_raw(bytes([wire, msg_id - server.schemas.base(crc)]) + body)
        session.log(f'[inject] {crc:08X}/{msg_id} {len(body)}B')
        sent += 1
    return sent


def inject_watcher(server: Server):
    """Research hook: every line of inject.jsonl is sent to each logged-in client, then the
    file is deleted. A line is {"crc": "BCD57A46", "msg": 55500, "value": {...}} (encoded with
    the schemas) or {"crc": ..., "msg": ..., "hex": "..."} (raw body)."""
    while True:
        time.sleep(1)
        try:
            lines = INJECT_FILE.read_text(encoding="utf-8").splitlines()
            INJECT_FILE.unlink()
        except OSError:
            continue
        for line in filter(str.strip, lines):
            try:
                item = json.loads(line)
                sent = send_research_message(server, item)
                if not sent:
                    print('[inject] No matching connected client/protocol for experiment')
            except Exception as e:
                print(f"[inject] bad line {line[:120]}: {e}")
                continue


def serve_client(c: socket.socket, addr, server: Server):
    try:
        handle_connection(c, addr, server)
    except Exception as e:
        print(f"[lobby] Connection error: {e}")
        traceback.print_exc()
    finally:
        c.close()


def main():
    parser = argparse.ArgumentParser(description="Overwatch 1.74 Lobby Server")
    parser.add_argument("--host", default="127.0.0.1", help="Host IP to bind (default: 127.0.0.1; 0.0.0.0 for LAN players)")
    parser.add_argument("--port", type=int, default=3724, help="Port to bind (default: 3724)")
    parser.add_argument("--save", default=str(REPO_ROOT / "profile.json"), help="Template profile for new accounts")
    parser.add_argument("--events", default=None, help="Active events for the template profile, comma separated (goodbye, halloween, ...)")
    parser.add_argument("--dashboard-port", type=int, default=3725, help="Web dashboard port (default: 3725, 0 to disable)")
    parser.add_argument("--game-port", type=int, default=3730, help="First per-session UDP instance port (default: 3730, 0 disables instances)")
    args = parser.parse_args()

    template = Path(args.save)
    profile = load_or_create_profile(template)
    if args.events is not None:
        profile.events = [e for e in args.events.split(",") if e.strip() and e.strip().lower() != "none"]
        save_profile(profile, template)

    k = bytes(range(32))
    a, b = Jam(k), Jam(k)
    assert b.crypt(a.crypt(bytes(range(256)))) == bytes(range(256))

    server = Server(template, args)

    if args.dashboard_port > 0:
        start_dashboard(server, port=args.dashboard_port)
    threading.Thread(target=inject_watcher, args=(server,), daemon=True).start()

    print("\n" + "=" * 65)
    print("      OVERWATCH 1.74 LOBBY SERVER")
    print("=" * 65)
    print(f" [*] Bind:           {args.host}:{args.port}")
    print(f" [*] Accounts:       {', '.join(server.accounts.all_saved()) or 'none yet'} (profiles/)")
    print(f" [*] New accounts:   copy of {template.name}")
    print(f" [*] Schemas:        {sum(len(g) for g in server.schemas.groups.values())} messages in {len(server.schemas.groups)} protocols")
    print(f" [*] Game instances: per-session loopback processes from port {args.game_port} (protocol research)")
    print("=" * 65)

    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        s.bind((args.host, args.port))
    except Exception as e:
        print(f"[!] ERROR binding to {args.host}:{args.port}: {e}")
        sys.exit(1)

    s.listen(16)
    print(f"\n[+] Server listening on {args.host}:{args.port}...")
    print(f"[+] Launch game client with:")
    print(f'    Overwatch.exe --tank_TournamentMode --lobbyServer={args.host}:{args.port}\n')

    try:
        while True:
            c, addr = s.accept()
            threading.Thread(target=serve_client, args=(c, addr, server), daemon=True).start()
    except KeyboardInterrupt:
        print("\n[*] Server shutdown.")
    finally:
        if server.matches is not None:
            server.matches.close()


if __name__ == "__main__":
    main()

"""Exercise the real HTTP boundary against disposable player profiles."""

import json
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from ow174.accounts.profile import Profile, load_or_create_profile, save_profile  # noqa: E402
from ow174.accounts.registry import Accounts  # noqa: E402
from ow174.catalog.boxes import BOX_TYPES  # noqa: E402
from ow174.catalog.events import CHALLENGES  # noqa: E402
from ow174.catalog.items import ItemDB  # noqa: E402
from ow174.catalog.templates import RetailTemplates  # noqa: E402
from ow174.content.collection import Collection  # noqa: E402
from ow174.dashboard.server import start_dashboard  # noqa: E402
from ow174.paths import WEB_DIR  # noqa: E402
from ow174.services.shop import ShopService  # noqa: E402

WEB_PREVIEWS = WEB_DIR / "assets" / "previews"
# The real (unlock level, frame GUID) table; frame GUIDs are not in level order.
BORDERS = Collection(RetailTemplates(), ItemDB()).border_levels


class Lobby:
    def __init__(self, root, name="Alpha"):
        template = root / "template.json"
        save_profile(Profile(player_name=name, level=100, credits=1000), template)
        self.accounts = Accounts(root / "profiles", template)
        self.selected = self.accounts.get(name)
        self.accounts.get("Beta")
        self.sessions = set()
        self.settings = SimpleNamespace(host="127.0.0.1", port=3724)
        self.items = SimpleNamespace(hero_names={1: "Tracer"}, get=lambda guid: None)
        self.content = SimpleNamespace(
            collection=SimpleNamespace(
                default_loadouts={1: {}},
                border_levels=BORDERS,
                portrait_frame=lambda profile: profile.frame_guid or BORDERS[0][1],
            )
        )
        self.social = SimpleNamespace(sessions={})
        self.loot = SimpleNamespace(open_all=self.open_all)
        self.pushed = []
        self.granted = []
        self.settings_pushed = []
        self.state_lock = threading.RLock()

    @staticmethod
    def open_all(profile):
        opened = len(profile.loot_boxes)
        profile.loot_boxes = []
        return opened, [0x0250000000000001] if opened else []

    def dashboard_account(self):
        return self.selected

    def select_account(self, name):
        self.selected = self.accounts.get(name)

    def default_account_name(self):
        return getattr(self, "default_name", "")

    def set_default_account(self, name):
        self.default_name = name

    def push_profile(self, account=None, granted=None):
        self.pushed.append(account or self.selected)
        self.granted.append(granted or [])

    def push_settings(self, account):
        self.settings_pushed.append(account)

    def reconnect_own_game(self):
        raise AssertionError("No test should disconnect the game")


class DashboardTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.lobby = Lobby(Path(self.temp.name))
        self.http = start_dashboard(self.lobby, port=0)
        self.addCleanup(self.close_server, self.http)
        self.url = "http://127.0.0.1:" + str(self.http.server_port)

    @staticmethod
    def close_server(server):
        server.shutdown()
        server.server_close()

    def request(self, path, data=None, base=None):
        payload = json.dumps(data).encode() if data is not None else None
        req = urllib.request.Request(
            (base or self.url) + path, data=payload, headers={"Content-Type": "application/json"}
        )
        try:
            response = urllib.request.urlopen(req, timeout=3)
        except urllib.error.HTTPError as error:
            response = error
        with response:
            raw = response.read()
            try:
                value = json.loads(raw)
            except ValueError:
                value = raw.decode(errors="replace")
            return response.status, value

    def test_state_includes_all_box_types_and_actual_online_count(self):
        status, data = self.request("/api/state")
        self.assertEqual(status, 200)
        self.assertEqual({b["id"] for b in data["catalogs"]["box_types"]}, set(BOX_TYPES))
        self.assertEqual(data["server"]["connected_clients"], 0)
        self.assertEqual(data["profile"]["player_name"], "Alpha")

    def test_an_account_can_be_made_the_default(self):
        status, data = self.request("/api/default_account", {"name": "beta"})
        self.assertEqual(status, 200)
        self.assertEqual(self.lobby.default_name, "Beta")  # the saved name, not the typed case
        _, data = self.request("/api/state")
        self.assertEqual([row["name"] for row in data["accounts"] if row["default"]], ["Beta"])

    def test_selected_marks_the_game_account_not_the_page_account(self):
        # Viewing Beta in the dashboard must not move the "Selected" mark off the game's account.
        _, data = self.request("/api/state?account=Beta")
        selected = [row["name"] for row in data["accounts"] if row["selected"]]
        self.assertEqual(selected, ["Alpha"])

    def test_json_update_targets_explicit_account_not_global_selection(self):
        status, _ = self.request("/api/update_profile", {"account": "Beta", "credits": 77})
        self.assertEqual(status, 200)
        self.assertEqual(self.lobby.accounts.get("Beta").profile.credits, 77)
        self.assertEqual(self.lobby.selected.profile.credits, 1000)
        self.assertEqual(self.lobby.pushed[-1].name, "Beta")

    def test_competitive_ratings_are_saved_per_queue(self):
        status, data = self.request("/api/update_profile", {"account": "Alpha", "rating_tank": 4100})
        self.assertEqual(status, 200)
        self.assertEqual(self.lobby.selected.profile.ratings, {"tank": 4100})
        self.assertEqual((data["profile"]["rating_tank"], data["profile"]["rating_open"]), (4100, 2333))
        status, _ = self.request("/api/update_profile", {"account": "Alpha", "rating_open": 9000})
        self.assertEqual(status, 400)
        change = {"account": "Alpha", "matches_tank": 3, "sms_protect": False, "season": "25"}
        status, data = self.request("/api/update_profile", change)
        self.assertEqual(status, 200)
        profile = self.lobby.selected.profile
        self.assertEqual((profile.matches, profile.sms_protect, profile.season), ({"tank": 3}, False, 25))
        self.assertEqual(data["profile"]["matches_open"], 25)

    def test_wins_count_for_the_top_500_but_never_pass_the_matches(self):
        status, data = self.request("/api/update_profile", {"account": "Alpha", "wins_tank": 25})
        self.assertEqual(status, 200)
        self.assertEqual(self.lobby.selected.profile.wins, {"tank": 25})
        self.assertEqual(data["profile"]["wins_damage"], 0)
        status, _ = self.request("/api/update_profile", {"account": "Alpha", "wins_tank": 26})
        self.assertEqual(status, 400)
        status, _ = self.request("/api/update_profile", {"account": "Alpha", "matches_tank": 24})
        self.assertEqual(status, 400)
        self.assertEqual(self.lobby.selected.profile.wins, {"tank": 25})

    def test_invalid_profile_change_is_atomic_and_reported(self):
        before = self.lobby.selected.path.read_bytes()
        status, data = self.request(
            "/api/update_profile", {"account": "Alpha", "credits": 50, "level": "bad"}
        )
        self.assertEqual(status, 400)
        self.assertIn("error", data)
        self.assertEqual(self.lobby.selected.profile.credits, 1000)
        self.assertEqual(self.lobby.selected.path.read_bytes(), before)
        self.assertEqual(self.lobby.pushed, [])

    def test_unknown_event_rejected_without_disabling_existing_event(self):
        status, _ = self.request("/api/update_profile", {"account": "Alpha", "events": ["typo"]})
        self.assertEqual(status, 400)
        self.assertEqual(self.lobby.selected.profile.events, ["goodbye"])

    def test_dates_outside_client_clock_range_cannot_poison_saved_profile(self):
        before = self.lobby.selected.path.read_bytes()
        for value in ("1900-01-01", "1999-12-31", "2000-01-01", "2000-01-02", "2106-02-07", "9999-12-31"):
            status, _ = self.request("/api/update_profile", {"account": "Alpha", "server_date": value})
            self.assertEqual(status, 400, value)
            self.assertEqual(self.lobby.selected.path.read_bytes(), before)
        for value in ("2000-01-03", "2106-02-06", "now", ""):
            status, _ = self.request("/api/update_profile", {"account": "Alpha", "server_date": value})
            self.assertEqual(status, 200, value)

    def test_unavailable_account_cannot_be_created_by_typo(self):
        status, _ = self.request("/api/update_profile", {"account": "NotSaved", "credits": 5})
        self.assertEqual(status, 404)
        self.assertFalse((Path(self.temp.name) / "profiles/NotSaved.json").exists())

    def test_all_known_box_types_can_be_granted_and_ids_stay_unique(self):
        for box_type in BOX_TYPES:
            status, data = self.request("/api/add_boxes", {"account": "Alpha", "type": box_type, "count": 2})
            self.assertEqual(status, 200)
            self.assertEqual(data["added"], 2)
        boxes = self.lobby.selected.profile.loot_boxes
        self.assertEqual(len(boxes), 2 * len(BOX_TYPES))
        self.assertEqual(len({b["id"] for b in boxes}), len(boxes))
        self.assertEqual({b["type"] for b in boxes}, set(BOX_TYPES))

    def test_opening_all_boxes_shows_the_overwatch_2_notice(self):
        status, _ = self.request("/api/open_all_boxes", {"account": "Alpha"})
        self.assertEqual(status, 400)  # no boxes
        self.request("/api/add_boxes", {"account": "Alpha", "type": 0, "count": 3})
        status, data = self.request("/api/open_all_boxes", {"account": "Alpha"})
        self.assertEqual(status, 200)
        self.assertEqual((data["opened"], data["new_items"]), (3, 1))
        profile = self.lobby.selected.profile
        self.assertEqual(profile.loot_boxes, [])
        # The count goes to the client in 20802, which shows the notice on the main menu.
        self.assertIn({"+0x0": 3, "+0x8": 0x00E0B03A}, profile.settings["+0x130"])
        self.assertEqual(self.lobby.settings_pushed, [self.lobby.selected])
        self.assertEqual(self.lobby.granted[-1], [0x0250000000000001])

    def test_invalid_box_type_or_quantity_cannot_mutate_inventory(self):
        for body in ({"type": 999, "count": 1}, {"type": 0, "count": -1}, {"type": 0, "count": 101}):
            status, _ = self.request("/api/add_boxes", dict(account="Alpha", **body))
            self.assertEqual(status, 400)
        self.assertEqual(self.lobby.selected.profile.loot_boxes, [])

    def test_dashboard_instances_do_not_share_accounts(self):
        second = Path(self.temp.name) / "second"
        second.mkdir()
        lobby2 = Lobby(second, "Other")
        http2 = start_dashboard(lobby2, port=0)
        self.addCleanup(self.close_server, http2)
        status, state = self.request("/api/state")
        self.assertEqual(status, 200)
        self.assertEqual(state["profile"]["player_name"], "Alpha")

    def use_real_catalog(self):
        items = ItemDB()
        collection = Collection(RetailTemplates(), items)
        self.lobby.items = items
        self.lobby.content = SimpleNamespace(collection=collection)
        self.lobby.shop = ShopService(collection, items)

    def collection(self, query):
        status, data = self.request(f"/api/collection?account=Alpha&{query}")
        self.assertEqual(status, 200)
        return data

    def test_collection_items_can_be_unlocked_for_free_and_removed(self):
        self.use_real_catalog()
        (spray,) = [
            i for i in self.collection("kind=sprays&hero=Genji&q=Bushi")["items"] if i["name"] == "Bushi"
        ]
        self.assertFalse(spray["owned"])
        self.request("/api/grant_skin", {"account": "Alpha", "guid": spray["guid"]})
        # The game counts the new item only when it arrives as an unlock.
        self.assertEqual(self.lobby.granted[-1], [int(spray["guid"], 16)])
        self.request("/api/grant_skin", {"account": "Alpha", "guid": spray["guid"]})
        self.assertEqual(self.lobby.granted[-1], [])
        (spray,) = [
            i for i in self.collection("kind=sprays&hero=Genji&q=Bushi")["items"] if i["name"] == "Bushi"
        ]
        self.assertTrue(spray["owned"])
        self.assertTrue(spray["removable"])
        self.assertFalse(spray["purchasable"])

    def test_collection_filters_by_type_and_owl(self):
        self.use_real_catalog()
        emotes = self.collection("kind=emotes")
        self.assertTrue(emotes["items"])
        self.assertEqual({i["type"] for i in emotes["items"]}, {"Emote"})
        owl = self.collection("kind=skins&owl=1")
        self.assertTrue(owl["items"])
        self.assertTrue(all(i["owl"] for i in owl["items"]))
        status, _ = self.request("/api/collection?account=Alpha&kind=nope")
        self.assertEqual(status, 400)

    def test_shop_purchase_http_returns_and_persists_token_balance(self):
        self.use_real_catalog()
        status, catalog = self.request(
            "/api/collection?account=Alpha&hero=Reaper&currency=league_tokens&q=Philadelphia"
        )
        self.assertEqual(status, 200)
        self.assertTrue(any(i["guid"] == "0x02500000000013C3" for i in catalog["items"]))
        status, bought = self.request("/api/purchase", {"account": "Alpha", "guid": "0x02500000000013C3"})
        self.assertEqual(status, 200)
        self.assertEqual(bought["profile"]["league_tokens"], 900)
        self.assertEqual(bought["profile"]["credits"], 1000)
        saved = json.loads(self.lobby.selected.path.read_text(encoding="utf-8"))
        self.assertEqual(saved["league_tokens"], 900)
        status, _ = self.request("/api/purchase", {"account": "Alpha", "guid": "0x02500000000013C3"})
        self.assertEqual(status, 409)
        self.assertEqual(self.lobby.selected.profile.league_tokens, 900)

    def test_loot_box_pictures_are_served_and_nothing_outside_their_folder(self):
        with urllib.request.urlopen(self.url + "/assets/boxes/golden.png", timeout=3) as response:
            self.assertEqual(response.headers["Content-Type"], "image/png")
            self.assertEqual(response.read(8), b"\x89PNG\r\n\x1a\n")
        outside = (
            "/assets/boxes/missing.png",
            "/assets/boxes/../index.html",
            "/assets/boxes/%2e%2e/x.png",
        )
        for path in outside:
            status, _ = self.request(path)
            self.assertEqual(status, 404, path)

    def frames(self, page=1):
        status, data = self.request(f"/api/collection?account=Alpha&kind=frames&page={page}")
        self.assertEqual(status, 200)
        return data

    def test_all_portrait_frames_are_listed_with_their_level(self):
        data = self.frames()
        self.assertEqual(data["total"], len(BORDERS))
        first = data["items"][0]
        self.assertEqual(first["guid"], f"0x{BORDERS[0][1]:016X}")
        self.assertIn("level 1+", first["name"])
        self.assertTrue(first["in_use"])
        self.assertIn("level 2991+", self.frames(page=data["pages"])["items"][-1]["name"])

    def test_frame_names_follow_the_unlock_level_not_the_guid(self):
        pages = range(1, self.frames()["pages"] + 1)
        names = {item["guid"]: item["name"] for page in pages for item in self.frames(page)["items"]}
        platinum = next(guid for level, guid in BORDERS if level == 1811)
        self.assertTrue(names[f"0x{platinum:016X}"].startswith("Platinum ·"))
        self.assertTrue(
            names[f"0x{next(g for lv, g in BORDERS if lv == 1791):016X}"].startswith("Gold ★★★★★")
        )

    def test_a_frame_can_be_chosen_and_reset_to_the_level_frame(self):
        gold_guid = next(guid for level, guid in BORDERS if level == 1441)
        gold = f"0x{gold_guid:016X}"
        status, _ = self.request("/api/set_frame", {"account": "Alpha", "guid": gold})
        self.assertEqual(status, 200)
        self.assertEqual(self.lobby.accounts.get("Alpha").profile.frame_guid, gold_guid)
        pages = range(1, self.frames()["pages"] + 1)
        chosen = [item for page in pages for item in self.frames(page)["items"] if item["chosen"]]
        self.assertEqual([item["guid"] for item in chosen], [gold])
        self.request("/api/set_frame", {"account": "Alpha", "guid": None})
        self.assertEqual(self.lobby.accounts.get("Alpha").profile.frame_guid, 0)

    def test_a_new_level_brings_back_the_level_frame(self):
        gold = next(guid for level, guid in BORDERS if level == 1441)
        self.request("/api/set_frame", {"account": "Alpha", "guid": f"0x{gold:016X}"})
        self.request("/api/update_profile", {"account": "Alpha", "credits": 5})
        self.assertEqual(self.lobby.accounts.get("Alpha").profile.frame_guid, gold)
        self.request("/api/update_profile", {"account": "Alpha", "level": 2243})
        self.assertEqual(self.lobby.accounts.get("Alpha").profile.frame_guid, 0)

    def test_only_portrait_frames_can_be_chosen(self):
        status, _ = self.request("/api/set_frame", {"account": "Alpha", "guid": "0x0250000000000001"})
        self.assertEqual(status, 400)

    def test_item_previews_are_served_as_webp(self):
        picture = next(WEB_PREVIEWS.glob("*.webp"))
        with urllib.request.urlopen(f"{self.url}/assets/previews/{picture.name}", timeout=3) as response:
            self.assertEqual(response.headers["Content-Type"], "image/webp")
            self.assertEqual(response.read(4), b"RIFF")
        status, _ = self.request("/assets/previews/0000000000000000.webp")
        self.assertEqual(status, 404)

    def test_static_dashboard_assets_and_the_event_picker(self):
        for path in ("/", "/assets/dashboard.css", "/assets/dashboard.js", "/assets/dashboard-api.mjs"):
            status, data = self.request(path)
            self.assertEqual(status, 200, path)
            self.assertTrue(len(data) > 100)
        status, data = self.request("/api/state")
        ids = {e["id"] for e in data["catalogs"]["events"]}
        self.assertTrue({"anniversary", "anniversary_remix_1", "anniversary_remix_2"} <= ids)
        # Events without a menu scene still bring their loot box and trophies; Tracer's has none.
        self.assertTrue({"summer", "archives"} <= ids)
        self.assertNotIn("tracer_comic", ids)
        status, _ = self.request("/api/update_profile", {"account": "Alpha", "events": ["tracer_comic"]})
        self.assertEqual(status, 400)

    def test_an_event_and_a_challenge_for_all_players(self):
        status, data = self.request("/api/apply_to_all", {"events": ["halloween"]})
        self.assertEqual((status, data["accounts"]), (200, 2))
        for name in ("Alpha", "Beta"):
            self.assertEqual(self.lobby.accounts.get(name).profile.events, ["halloween"])
        template = load_or_create_profile(self.lobby.accounts.template)
        self.assertEqual(template.events, ["halloween"])  # new accounts start from it
        self.lobby.accounts.get("Beta").profile.challenge_wins = 7
        status, _ = self.request("/api/apply_to_all", {"challenge": "Kanezaka Challenge"})
        self.assertEqual(status, 200)
        beta = self.lobby.accounts.get("Beta").profile
        self.assertEqual((beta.challenge, beta.challenge_wins), ("Kanezaka Challenge", 7))  # wins stay
        for wrong in ({"events": ["nope"]}, {"credits": 5}, {}):
            status, _ = self.request("/api/apply_to_all", wrong)
            self.assertEqual(status, 400, wrong)

    def test_only_the_challenges_with_a_play_menu_banner_can_be_picked(self):
        status, data = self.request("/api/state")
        self.assertEqual([c["id"] for c in data["catalogs"]["challenges"]], list(CHALLENGES))
        reaper = {"account": "Alpha", "challenge": "Reaper's Code of Violence Challenge"}
        status, _ = self.request("/api/update_profile", reaper)
        self.assertEqual(status, 400)
        kanezaka = {"account": "Alpha", "challenge": "Kanezaka Challenge"}
        status, _ = self.request("/api/update_profile", kanezaka)
        self.assertEqual(status, 200)


if __name__ == "__main__":
    unittest.main()

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
from ow174.accounts.profile import Profile, save_profile  # noqa: E402
from ow174.accounts.registry import Accounts  # noqa: E402
from ow174.catalog.boxes import BOX_TYPES  # noqa: E402
from ow174.catalog.items import ItemDB  # noqa: E402
from ow174.catalog.templates import RetailTemplates  # noqa: E402
from ow174.content.collection import Collection  # noqa: E402
from ow174.dashboard.server import start_dashboard  # noqa: E402
from ow174.services.shop import ShopService  # noqa: E402


class Lobby:
    def __init__(self, root, name="Alpha"):
        template = root / "template.json"
        save_profile(Profile(player_name=name, level=100, credits=1000), template)
        self.accounts = Accounts(root / "profiles", template)
        self.selected = self.accounts.get(name)
        self.accounts.get("Beta")
        self.sessions = set()
        self.settings = SimpleNamespace(host="127.0.0.1", port=3724)
        self.items = SimpleNamespace(hero_names={1: "Tracer"}, challenges=lambda: {})
        self.content = SimpleNamespace(collection=SimpleNamespace(default_loadouts={1: {}}))
        self.social = SimpleNamespace(sessions={})
        self.pushed = []
        self.state_lock = threading.RLock()

    def dashboard_account(self):
        return self.selected

    def select_account(self, name):
        self.selected = self.accounts.get(name)

    def push_profile(self, account=None):
        self.pushed.append(account or self.selected)

    def reconnect_all(self):
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

    def test_json_update_targets_explicit_account_not_global_selection(self):
        status, _ = self.request("/api/update_profile", {"account": "Beta", "credits": 77})
        self.assertEqual(status, 200)
        self.assertEqual(self.lobby.accounts.get("Beta").profile.credits, 77)
        self.assertEqual(self.lobby.selected.profile.credits, 1000)
        self.assertEqual(self.lobby.pushed[-1].name, "Beta")

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

    def test_shop_purchase_http_returns_and_persists_token_balance(self):
        items = ItemDB()
        self.lobby.shop = ShopService(Collection(RetailTemplates(), items), items)
        status, catalog = self.request(
            "/api/shop?account=Alpha&hero=Reaper&currency=league_tokens&q=Philadelphia"
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

    def test_static_dashboard_assets_and_scene_picker_contains_only_available_scenes(self):
        for path in ("/", "/assets/dashboard.css", "/assets/dashboard.js", "/assets/dashboard-api.mjs"):
            status, data = self.request(path)
            self.assertEqual(status, 200, path)
            self.assertTrue(len(data) > 100)
        status, data = self.request("/api/state")
        ids = {e["id"] for e in data["catalogs"]["events"]}
        self.assertTrue({"anniversary", "anniversary_remix_1", "anniversary_remix_2"} <= ids)
        self.assertFalse(ids & {"summer", "archives", "contenders", "tracer_comic"})
        status, _ = self.request("/api/update_profile", {"account": "Alpha", "events": ["tracer_comic"]})
        self.assertEqual(status, 400)


if __name__ == "__main__":
    unittest.main()

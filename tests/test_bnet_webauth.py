"""The login form the game shows when it has no Battle.net ticket (a player who never logged in)."""

import json
import sys
import unittest
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ow174.bnet.webauth import LOGIN_PATH, start_web_server


class WebLoginTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = start_web_server("127.0.0.1", 0)
        cls.url = f"http://127.0.0.1:{cls.server.server_port}{LOGIN_PATH}?externalChallenge=login&app=pro"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def test_form_cookies_end_with_a_semicolon(self):
        # Tested in game: the client reads JSESSIONID up to the ';' ("jsessionID doesn't end") and
        # shows "Authentication error" instead of the login form when it is missing.
        with urllib.request.urlopen(self.url, timeout=3) as response:
            cookies = response.headers.get_all("Set-Cookie")
            form = json.loads(response.read())
        self.assertEqual({cookie.split("=", 1)[0] for cookie in cookies}, {"web.id", "JSESSIONID"})
        for cookie in cookies:
            self.assertIn(";", cookie)
        self.assertEqual(form["type"], "LOGIN_FORM")

    def test_any_credentials_get_a_login_ticket(self):
        body = json.dumps({"inputs": [{"input_id": "account_name", "value": "a@a.a"}]}).encode()
        request = urllib.request.Request(self.url, data=body, headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(request, timeout=3) as response:
            result = json.loads(response.read())
        self.assertEqual(result["authentication_state"], "DONE")
        self.assertTrue(result["login_ticket"].startswith("US-"))


if __name__ == "__main__":
    unittest.main()

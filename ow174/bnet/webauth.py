"""Web login form for the Battle.net login (plain HTTP on 127.0.0.1:6969).

The client opens the web_auth_url from OnExternalChallenge. It fetches the form description as JSON
(GET). When the player submits, the POST answers with state DONE and a login ticket, and the client
passes that ticket to VerifyWebCredentials on the RPC connection. Nothing is checked: this is a
local, offline server.
"""

import json
import logging
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

log = logging.getLogger("ow174.bnet")

LOGIN_PATH = "/battlenet/login"

FORM = {
    "type": "LOGIN_FORM",
    "inputs": [
        {"input_id": "account_name", "type": "text", "label": "Email or Phone", "max_length": 320},
        {"input_id": "password", "type": "password", "label": "Password", "max_length": 128},
        {"input_id": "log_in_submit", "type": "submit", "label": "Log In"},
    ],
}

LOGIN_TICKET = "US-0000000000000000000000000000000a-000000001"


class Handler(BaseHTTPRequestHandler):
    def _log(self, msg):
        log.info("[webauth] %s", msg)

    def log_message(self, *args):
        pass  # requests are logged through _log instead of to stderr

    def _send_json(self, payload: dict, status=200, cookies=None):
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json;charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Server", "Apache")
        for name, value in (cookies or {}).items():
            # The client reads a cookie value up to the ';' and fails the login without one.
            self.send_header("Set-Cookie", f"{name}={value}; Path=/")
        self.end_headers()
        self.wfile.write(body)

    def _is_login_path(self) -> bool:
        return self.path.split("?", 1)[0] == LOGIN_PATH

    def do_GET(self):
        if self._is_login_path():
            agent = self.headers.get("User-Agent", "-")
            accept = self.headers.get("Accept", "-")
            self._log(f"GET {self.path} -> login form (User-Agent: {agent}; Accept: {accept})")
            self._send_json(
                FORM,
                cookies={
                    "web.id": "US-00000000-0000-0000-0000-000000000000",
                    "JSESSIONID": "00000000-0000-0000-0000-000000000000",
                },
            )
        else:
            self._log(f"GET {self.path} -> 404")
            self._send_json({"error": "not found"}, status=404)

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        self.rfile.read(length)  # the credentials are not checked
        if self._is_login_path():
            self._log(f"POST {self.path} len={length} -> DONE (login accepted)")
            self._send_json({"authentication_state": "DONE", "login_ticket": LOGIN_TICKET})
        else:
            self._log(f"POST {self.path} -> 404")
            self._send_json({"error": "not found"}, status=404)


def start_web_server(host="127.0.0.1", port=6969) -> ThreadingHTTPServer:
    """Serve the login form in a background thread. Raises OSError when the port is taken."""
    httpd = ThreadingHTTPServer((host, port), Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True, name="webauth").start()
    log.info("[webauth] login server on http://%s:%d%s", host, port, LOGIN_PATH)
    return httpd

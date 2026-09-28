"""Web-auth login server for the retail route (plaintext HTTP on 127.0.0.1:6969).

The client opens the web_auth_url from OnExternalChallenge in its login view. It fetches the login
FormInputs JSON (GET), the player submits, and the POST returns a login_ticket with state DONE. The
client then calls VerifyWebCredentials on the RPC channel with that ticket. Credentials are not
checked here; this is a local, offline server.
"""
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

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
    logger = None

    def _log(self, msg):
        if Handler.logger:
            Handler.logger(f"[webauth] {msg}")
        else:
            print(f"[webauth] {msg}", flush=True)

    def log_message(self, *args):
        pass  # silence the default stderr logging

    def _send_json(self, payload: dict, status=200, cookies=None):
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json;charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Server", "Apache")
        for name, value in (cookies or {}).items():
            self.send_header("Set-Cookie", f"{name}={value}")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path.split("?", 1)[0] == LOGIN_PATH:
            # Auto-complete the challenge: this is a local, offline, zero-auth
            # server, so instead of asking the player for an email/password we
            # return DONE immediately. A fresh client (no cached ticket) then
            # proceeds straight to VerifyWebCredentials and logs in with no
            # interaction. (A client with a cached ticket never hits this URL.)
            self._log(f"GET {self.path} -> DONE (auto-login)")
            self._send_json({"authentication_state": "DONE", "login_ticket": LOGIN_TICKET}, cookies={
                "web.id": "US-00000000-0000-0000-0000-000000000000",
                "JSESSIONID": "00000000-0000-0000-0000-000000000000",
            })
        else:
            self._log(f"GET {self.path} -> 404")
            self._send_json({"error": "not found"}, status=404)

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length) if length else b""
        if self.path.split("?", 1)[0] == LOGIN_PATH:
            self._log(f"POST {self.path} len={length} -> DONE (login accepted)")
            self._send_json({"authentication_state": "DONE", "login_ticket": LOGIN_TICKET})
        else:
            self._log(f"POST {self.path} -> 404")
            self._send_json({"error": "not found"}, status=404)


def start_web_server(host="127.0.0.1", port=6969, logger=None):
    Handler.logger = logger
    httpd = ThreadingHTTPServer((host, port), Handler)
    (logger or print)(f"[webauth] login server on http://{host}:{port}{LOGIN_PATH}")
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    return httpd


if __name__ == "__main__":
    start_web_server()
    import time
    while True:
        time.sleep(3600)

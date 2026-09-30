"""The dashboard's loopback HTTP server: the static page in web/ and the JSON API."""

import json
import logging
import re
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlparse

from ow174.dashboard.errors import ApiError
from ow174.dashboard.service import DashboardService
from ow174.paths import WEB_DIR

log = logging.getLogger("ow174.dashboard")

MAX_BODY_BYTES = 65536

# URL path -> (file in web/, MIME type).
STATIC_FILES = {
    "/": ("index.html", "text/html"),
    "/index.html": ("index.html", "text/html"),
    "/assets/dashboard.css": ("dashboard.css", "text/css"),
    "/assets/dashboard-api.mjs": ("dashboard-api.mjs", "text/javascript"),
    "/assets/dashboard.js": ("dashboard.js", "text/javascript"),
}
# Pictures: loot boxes (/assets/boxes/golden.png) and item previews (/assets/previews/<GUID>.webp).
# The strict pattern keeps requests inside those folders.
PICTURE = re.compile(r"/assets/(boxes/[a-z0-9_]+\.png|previews/[0-9A-F]{16}\.webp)")
PICTURE_TYPES = {".png": "image/png", ".webp": "image/webp"}


def last_values(query_string: str, keep_blank_values: bool = False) -> dict:
    """Turn a query string into a plain dict; a repeated key keeps its last value."""
    parsed = parse_qs(query_string, keep_blank_values=keep_blank_values)
    return {key: values[-1] for key, values in parsed.items()}


class DashboardHandler(BaseHTTPRequestHandler):
    def log_message(self, *args) -> None:
        # The page polls every few seconds; logging each request would flood the server console.
        pass

    @property
    def service(self) -> DashboardService:
        return self.server.dashboard_api

    def do_GET(self) -> None:
        url = urlparse(self.path)
        query = last_values(url.query)
        try:
            if url.path in STATIC_FILES:
                self._send_static_file(url.path)
            elif picture := PICTURE.fullmatch(url.path):
                self._send_picture(picture.group(1))
            elif url.path == "/api/state":
                self._send_json(self.service.state(query.get("account")))
            elif url.path == "/api/status":
                self._send_json(self.service.state(query.get("account"))["profile"])
            elif url.path == "/api/collection":
                self._send_json(self.service.collection(query))
            else:
                raise ApiError("Address not found", 404)
        except ApiError as error:
            self._send_json({"error": str(error)}, error.status)
        except (ValueError, TypeError) as error:
            self._send_json({"error": str(error)}, 400)

    def do_POST(self) -> None:
        try:
            data = self._read_body()
            self._send_json(self._run_action(urlparse(self.path).path, data))
        except ApiError as error:
            self._send_json({"error": str(error)}, error.status)
        except (ValueError, TypeError, UnicodeError) as error:
            self._send_json({"error": "Invalid request data: " + str(error)}, 400)
        except OSError as error:
            self._send_json({"error": "Could not save changes: " + str(error)}, 500)

    def _run_action(self, path: str, data: dict) -> dict:
        if path == "/api/update_profile":
            return self.service.update_profile(data)
        if path == "/api/add_boxes":
            return self.service.add_boxes(data)
        if path == "/api/open_all_boxes":
            return self.service.open_all_boxes(data)
        if path == "/api/bot_group":
            return self.service.bot_group(data)
        if path == "/api/purchase":
            return self.service.purchase(data)
        if path == "/api/grant_skin":
            return self.service.grant_skin(data)
        if path == "/api/select_account":
            return self.service.select_account(data)
        if path == "/api/set_frame":
            return self.service.set_frame(data)
        if path == "/api/reconnect":
            return self.service.reconnect()
        if path == "/api/start_game":
            return self.service.start_game(data)
        raise ApiError("Action not found", 404)

    def _read_body(self) -> dict:
        """The request body as a dict, from JSON or from a plain HTML form."""
        length = int(self.headers.get("Content-Length", "0"))
        if not 0 <= length <= MAX_BODY_BYTES:
            raise ApiError("Request too large", 413)
        text = self.rfile.read(length).decode("utf-8")
        if self.headers.get_content_type() != "application/json":
            return last_values(text, keep_blank_values=True)
        data = json.loads(text)
        if not isinstance(data, dict):
            raise ApiError("A JSON object is expected")
        return data

    def _send_static_file(self, url_path: str) -> None:
        file_name, mime = STATIC_FILES[url_path]
        path = WEB_DIR / file_name
        if not path.is_file():
            raise ApiError("The page files are not ready yet. Reload in a moment.", 503)
        self._send(path.read_bytes(), mime)

    def _send_picture(self, relative_path: str) -> None:
        path = WEB_DIR / "assets" / relative_path
        if not path.is_file():
            raise ApiError("Address not found", 404)
        self._send(path.read_bytes(), PICTURE_TYPES[path.suffix])

    def _send_json(self, value, status: int = 200) -> None:
        body = json.dumps(value, ensure_ascii=False).encode("utf-8")
        self._send(body, "application/json", status)

    def _send(self, body: bytes, mime: str, status: int = 200) -> None:
        self.send_response(status)
        text = mime.startswith("text/") or mime == "application/json"
        self.send_header("Content-Type", mime + "; charset=utf-8" if text else mime)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)


def start_dashboard(lobby, port: int = 3725) -> HTTPServer | None:
    """Serve the dashboard on 127.0.0.1 in a background thread; None when the port is taken."""
    try:
        httpd = HTTPServer(("127.0.0.1", port), DashboardHandler)
    except OSError as error:
        log.error("[!] Could not start dashboard on port %d: %s", port, error)
        return None
    httpd.dashboard_api = DashboardService(lobby)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    log.info(" [*] Web Dashboard: http://127.0.0.1:%d", httpd.server_port)
    return httpd

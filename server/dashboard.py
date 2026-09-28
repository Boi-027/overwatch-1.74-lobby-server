"""Loopback HTTP adapter for the lobby dashboard; static UI lives in web/."""
import json
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path
import threading
from urllib.parse import parse_qs, urlparse

try:
    from dashboard_api import DashboardService, ApiError
    from maps import MAP_REGISTRY
except ImportError:
    from .dashboard_api import DashboardService, ApiError
    from .maps import MAP_REGISTRY

WEB = Path(__file__).with_name('web')
ASSETS = {'/': ('index.html', 'text/html'), '/index.html': ('index.html', 'text/html'),
          '/assets/dashboard.css': ('dashboard.css', 'text/css'),
          '/assets/dashboard-api.mjs': ('dashboard-api.mjs', 'text/javascript'),
          '/assets/dashboard.js': ('dashboard.js', 'text/javascript')}


class DashboardHandler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    @property
    def api(self):
        return self.server.dashboard_api

    def _send(self, payload, mime, status=200):
        self.send_response(status)
        self.send_header('Content-Type', mime + '; charset=utf-8')
        self.send_header('Content-Length', str(len(payload)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.end_headers()
        self.wfile.write(payload)

    def _json(self, value, status=200):
        self._send(json.dumps(value, ensure_ascii=False).encode('utf-8'), 'application/json', status)

    def do_GET(self):
        parsed = urlparse(self.path)
        query = {k: v[-1] for k, v in parse_qs(parsed.query).items()}
        try:
            if parsed.path in ASSETS:
                name, mime = ASSETS[parsed.path]
                path = WEB / name
                if not path.is_file():
                    raise ApiError('The interface is still building. Please refresh the page shortly.', 503)
                self._send(path.read_bytes(), mime)
            elif parsed.path == '/api/state':
                self._json(self.api.state(query.get('account')))
            elif parsed.path == '/api/status':
                self._json(self.api.state(query.get('account'))['profile'])
            elif parsed.path == '/api/shop':
                self._json(self.api.shop(query))
            elif parsed.path == '/api/skins':
                self._json(self.api.skins(query))
            elif parsed.path == '/api/maps':
                self._json([{'guid': m.hex_guid, 'index': f'0x{m.index:X}', 'name_en': m.name_en,
                             'name_ru': m.name_ru, 'mode': m.mode, 'is_background': m.is_background}
                            for m in MAP_REGISTRY.values()])
            else:
                raise ApiError('Address not found', 404)
        except ApiError as error:
            self._json({'error': str(error)}, error.status)
        except (ValueError, TypeError) as error:
            self._json({'error': str(error)}, getattr(error, 'status', 400))

    def do_POST(self):
        try:
            length = int(self.headers.get('Content-Length', '0'))
            if not 0 <= length <= 65536:
                raise ApiError('Request too large', 413)
            raw = self.rfile.read(length).decode('utf-8')
            if self.headers.get_content_type() == 'application/json':
                data = json.loads(raw)
                if not isinstance(data, dict):
                    raise ApiError('A JSON object is expected')
            else:
                data = {k: v[-1] for k, v in parse_qs(raw, keep_blank_values=True).items()}
            path = urlparse(self.path).path
            if path == '/api/update_profile':
                result = self.api.update_profile(data)
            elif path == '/api/add_boxes':
                result = self.api.add_boxes(data)
            elif path == '/api/purchase':
                result = self.api.purchase(data)
            elif path == '/api/grant_skin':
                result = self.api.grant_skin(data)
            elif path == '/api/select_account':
                account = self.api.account(data.get('name'))
                self.api.lobby.select_account(account.name)
                result = {'status': 'ok'}
            elif path == '/api/reconnect':
                self.api.lobby.reconnect_all()
                result = {'status': 'ok'}
            else:
                raise ApiError('Action not found', 404)
            self._json(result)
        except ApiError as error:
            self._json({'error': str(error)}, error.status)
        except (ValueError, TypeError, UnicodeError) as error:
            self._json({'error': 'Invalid request data: ' + str(error)}, 400)
        except OSError as error:
            self._json({'error': 'Could not save changes: ' + str(error)}, 500)


def start_dashboard(server, port=3725):
    try:
        httpd = HTTPServer(('127.0.0.1', port), DashboardHandler)
        httpd.dashboard_api = DashboardService(server)
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        print(f' [*] Web Dashboard: http://127.0.0.1:{httpd.server_port}')
        return httpd
    except OSError as error:
        print(f'[!] Could not start dashboard on port {port}: {error}')
        return None

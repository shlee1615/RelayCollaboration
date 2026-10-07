"""Same-origin Relay GUI server, fixed loopback deployment, no arbitrary commands."""
from __future__ import annotations
import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from http.cookies import SimpleCookie
import json
import secrets
import threading
import time
from urllib.parse import urlsplit, parse_qs
from relay_collaboration.console_backend import ConsoleError, ROOT, canonical, safe_path

CSP = "default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self' data:; font-src 'self'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'"
CAPABILITIES = ['app-release-v2', 'app-release-stale-v1', 'app-send-v1', 'review-receipt-v1']
CAPABILITIES += ['binding-console-v1', 'instance-overview-v1']

def handler_for(backend, cookie_secret, csrf_secret, activity_secret):
    # Cookies are scoped by hostname, not port. Distinct runtimes must not
    # overwrite each other's session while several localhost consoles are open.
    profile_id = getattr(backend, 'profile_id', None)
    cookie_name = 'relay_session' + ('_' + profile_id.replace('-', '') if profile_id else '')
    class Handler(BaseHTTPRequestHandler):
        server_version = 'RelayConsole/1'
        def setup(self):
            super().setup(); self.connection.settimeout(10)
        def log_message(self, *_):
            pass
        def respond(self, status, data, content_type='application/json; charset=utf-8', cookie=False):
            raw = canonical(data) if isinstance(data, (dict, list)) else data
            self.send_response(status)
            for key, value in [('Content-Type', content_type), ('Content-Length', str(len(raw))), ('Cache-Control', 'no-store'),
                               ('X-Content-Type-Options', 'nosniff'), ('Content-Security-Policy', CSP), ('Referrer-Policy', 'no-referrer'),
                               ('Cross-Origin-Resource-Policy', 'same-origin'), ('Connection', 'close')]:
                self.send_header(key, value)
            if cookie:
                self.send_header('Set-Cookie', cookie_name + '=' + cookie_secret + '; Path=/; HttpOnly; SameSite=Strict')
            self.end_headers()
            try:
                self.wfile.write(raw)
            except (BrokenPipeError, ConnectionResetError):
                pass
        def fail(self, code, message, status):
            self.respond(status, {'error': {'code': code, 'message': message}})
        def local(self):
            port = self.server.server_port
            hosts = self.headers.get_all('Host', [])
            if self.client_address[0] != '127.0.0.1' or hosts != ['127.0.0.1:' + str(port)]:
                raise ConsoleError('forbidden_host', 'Open the console using its 127.0.0.1 address.', 403)
            origins = self.headers.get_all('Origin', [])
            if len(origins) > 1 or (origins and origins != ['http://' + hosts[0]]):
                raise ConsoleError('forbidden_origin', 'Cross-origin access is not allowed.', 403)
            if self.headers.get('Sec-Fetch-Site') == 'cross-site':
                raise ConsoleError('forbidden_origin', 'Cross-site access is not allowed.', 403)
        def session(self):
            cookies = self.headers.get_all('Cookie', [])
            jar = SimpleCookie()
            try:
                if len(cookies) != 1:
                    raise ValueError()
                jar.load(cookies[0])
                token = jar[cookie_name].value
            except Exception:
                raise ConsoleError('session_required', '請重新整理控制台工作階段。', 401) from None
            if not secrets.compare_digest(token, cookie_secret):
                raise ConsoleError('session_required', '控制台已重啟，請重新取得工作階段。', 401)
        def query(self):
            if len(self.path) > 4096:
                raise ConsoleError('query_too_large', 'Query too long.')
            parsed = urlsplit(self.path)
            fields = parse_qs(parsed.query, keep_blank_values=True, max_num_fields=12)
            if any(len(v) != 1 for v in fields.values()):
                raise ConsoleError('invalid_query', 'Duplicate query fields.')
            return parsed.path, {k: v[0] for k, v in fields.items()}
        def do_GET(self):
            try:
                self.local()
                path, query = self.query()
                if path in {'/', '/index.html', '/style.css', '/app.js', '/flow_model.js', '/bindings.js', '/i18n.js'}:
                    file = ROOT / ('index.html' if path == '/' else path[1:])
                    mime = 'text/html; charset=utf-8' if file.suffix == '.html' else 'text/css; charset=utf-8' if file.suffix == '.css' else 'text/javascript; charset=utf-8'
                    self.respond(200, safe_path(file).read_bytes(), mime); return
                if path == '/health':
                    self.respond(200, {'ok': True, 'service': 'relay-console', 'version': 1}); return
                if path == '/api/session':
                    self.respond(200, {'csrf': csrf_secret, 'server_time': time.time(), 'capabilities': CAPABILITIES,
                        'profile_id': getattr(backend, 'profile_id', None), 'config_sha256': backend.config.digest if getattr(backend, 'config', None) else None}, cookie=True); return
                self.session()
                if path == '/api/summary' and not query:
                    self.respond(200, backend.summary())
                elif path == '/api/instances' and not query:
                    self.respond(200, backend.instance_overview())
                elif path == '/api/tasks' and not set(query) - {'status', 'direction', 'q', 'cursor'}:
                    self.respond(200, backend.observe('tasks', **{k: v for k, v in query.items() if v}))
                elif path.startswith('/api/task/') and not query:
                    self.respond(200, backend.detail(path[len('/api/task/'):]))
                elif path.startswith('/api/review/') and not query:
                    self.respond(200, backend.review_receipt(path[len('/api/review/'):]))
                else:
                    self.fail('not_found', 'Endpoint not found.', 404)
            except ConsoleError as exc:
                self.fail(exc.code, exc.message, exc.status)
            except Exception:
                self.fail('read_failed', '無法取得資料；請稍後重試。', 503)
        def body(self):
            lengths = self.headers.get_all('Content-Length', [])
            if self.headers.get('Transfer-Encoding') or len(lengths) != 1 or not lengths[0].isdigit():
                raise ConsoleError('invalid_body', 'One Content-Length is required.')
            length = int(lengths[0])
            if not 2 <= length <= 2 * 1024 * 1024:
                raise ConsoleError('body_limit', 'Request body exceeds limit.', 413)
            content_types = self.headers.get_all('Content-Type', [])
            if len(content_types) != 1 or content_types[0].split(';')[0] != 'application/json':
                raise ConsoleError('invalid_type', 'JSON content type required.', 415)
            raw = self.rfile.read(length)
            if len(raw) != length:
                raise ConsoleError('invalid_body', 'Incomplete body.')
            def pairs(items):
                value = {}
                for key, item in items:
                    if key in value:
                        raise ValueError()
                    value[key] = item
                return value
            try:
                return json.loads(raw.decode('utf-8'), object_pairs_hook=pairs,
                                  parse_constant=lambda value: (_ for _ in ()).throw(ValueError()))
            except (ValueError, UnicodeError, RecursionError):
                raise ConsoleError('invalid_json', 'Invalid UTF-8 JSON.') from None
        def do_POST(self):
            try:
                self.local()
                if self.path == '/api/codex/activity':
                    auth = self.headers.get_all('Authorization', [])
                    if len(auth) != 1 or not secrets.compare_digest(auth[0], 'Bearer ' + activity_secret):
                        raise ConsoleError('activity_auth', 'Only the reporting console can publish Codex activity.', 403)
                    self.respond(200, backend.report(self.body())); return
                self.session()
                if self.headers.get_all('Origin', []) != ['http://' + self.headers['Host']]:
                    raise ConsoleError('origin_required', 'Same-origin browser request required.', 403)
                tokens = self.headers.get_all('X-CSRF-Token', [])
                if len(tokens) != 1 or not secrets.compare_digest(tokens[0], csrf_secret):
                    raise ConsoleError('csrf_required', '請重新整理後再操作。', 403)
                body = self.body()
                if self.path == '/api/review':
                    value = backend.reviews.submit(body)
                elif self.path == '/api/app':
                    value = backend.app_command(body)
                elif self.path == '/api/binding':
                    value = backend.binding_command(body)
                elif self.path == '/api/review/retry' and isinstance(body, dict) and set(body) == {'client_request_id'}:
                    value = backend.reviews.retry(body['client_request_id'])
                elif self.path == '/api/worker' and isinstance(body, dict) and set(body) in ({'action'}, {'action', 'actor'}):
                    if body.get('actor', 'claude') not in ('codex', 'claude'):
                        raise ConsoleError('invalid_actor', 'Worker actor must be codex or claude.')
                    value = backend.worker.action(body['action'], body['actor']) if 'actor' in body else backend.worker.action(body['action'])
                else:
                    raise ConsoleError('invalid_action', 'Unknown action or unsupported fields.')
                self.respond(202, value)
            except ConsoleError as exc:
                self.fail(exc.code, exc.message, exc.status)
            except Exception:
                self.fail('action_uncertain', '操作結果不確定；請保留同一任務識別並先查詢狀態。', 503)
        def do_OPTIONS(self):
            self.fail('method_not_allowed', 'CORS is not enabled.', 405)
    return Handler

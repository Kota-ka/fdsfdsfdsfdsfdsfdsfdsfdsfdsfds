"""Latest-frame SSE transport, restricted to loopback and the local tosu origin."""

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import secrets
import socket
import threading
import time
from urllib.parse import parse_qs, urlsplit

TOSU_ORIGINS = frozenset(('http://127.0.0.1:24050', 'http://localhost:24050', 'tosu://server'))


class OverlayStream:
    def __init__(self, sample):
        self.sample = sample
        self.token = secrets.token_urlsafe(24)
        self.latest = dict(active=False, connected=False, updated=0)
        self.sequence = 0
        self.lock = threading.Lock()
        self.stopped = threading.Event()
        owner = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = 'HTTP/1.1'

            def log_message(self, *args):
                pass

            def do_GET(self):
                parsed = urlsplit(self.path)
                origin = self.headers.get('Origin')
                if self.headers.get('Host') not in (f'127.0.0.1:{self.server.server_port}', f'localhost:{self.server.server_port}'):
                    self.send_error(403)
                    return
                if origin is not None and origin not in TOSU_ORIGINS:
                    self.send_error(403)
                    return
                if parsed.path != '/events' or parse_qs(parsed.query).get('token') != [owner.token]:
                    self.send_error(404)
                    return
                self.connection.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                self.connection.settimeout(2)
                self.send_response(200)
                self.send_header('Content-Type', 'text/event-stream')
                self.send_header('Cache-Control', 'no-store')
                if origin:
                    self.send_header('Access-Control-Allow-Origin', origin)
                    self.send_header('Vary', 'Origin')
                self.end_headers()
                try:
                    last_sequence, last_cursor, last_send = -1, None, 0.0
                    while not owner.stopped.is_set():
                        started = time.perf_counter()
                        data = owner.snapshot()
                        signature = (data.get('active'), data.get('connected'), data.get('reason'),
                                     tuple(data.get('cursor') or ()), tuple(data.get('hand') or ()))
                        if data['sequence'] != last_sequence:
                            prefix, payload_data = '', data
                        elif signature != last_cursor or started - last_send >= 1 / 120:
                            prefix = 'event: cursor\n'
                            payload_data = {key: data.get(key) for key in
                                            ('cursor', 'hand', 'offset', 'active', 'connected', 'reason', 'updated', 'source_age_ms')}
                        else:
                            payload_data = None
                        if payload_data is not None:
                            payload = json.dumps(payload_data, allow_nan=False, separators=(',', ':'), ensure_ascii=False)
                            self.wfile.write((prefix + 'data: ' + payload + '\n\n').encode('utf-8'))
                            self.wfile.flush()
                            last_sequence, last_cursor, last_send = data['sequence'], signature, started
                        # High-resolution sleep; unchanged cursor samples need no repeated full-state payload.
                        time.sleep(max(0, .001 - (time.perf_counter() - started)))
                except (OSError, ValueError):
                    pass
                finally:
                    self.close_connection = True

        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.server.daemon_threads = True
        self.thread = threading.Thread(target=lambda: self.server.serve_forever(poll_interval=.05), daemon=True)
        self.thread.start()
        self.url = f'http://127.0.0.1:{self.server.server_port}/events?token={self.token}'

    def update(self, data):
        with self.lock:
            self.latest = data
            self.sequence += 1

    def snapshot(self):
        with self.lock:
            data = dict(self.latest)
            data['sequence'] = self.sequence
        try:
            return self.sample(data)
        except (OSError, ValueError, TypeError, KeyError):
            data.update(active=False, reason='cursor sample unavailable', updated=time.time())
            return data

    def close(self):
        if not self.stopped.is_set():
            self.stopped.set()
            self.server.shutdown()
            self.server.server_close()
            self.thread.join(timeout=2)

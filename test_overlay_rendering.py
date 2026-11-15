"""Optional browser regression checks: pip install playwright; uses installed Edge."""

from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import struct
import statistics
import time
import threading
import unittest
from unittest.mock import patch
from urllib.parse import urlsplit
import zlib

from aim_physics import AimConfig
from tosu_overlay import schema
from overlay_stream import OverlayStream


def screenshot_pixel(page, x, y):
    data = page.screenshot(clip=dict(x=x, y=y, width=1, height=1))
    offset, compressed = 8, []
    while offset < len(data):
        length = struct.unpack_from('>I', data, offset)[0]
        if data[offset + 4:offset + 8] == b'IDAT':
            compressed.append(data[offset + 8:offset + 8 + length])
        offset += length + 12
    # First row, first pixel: PNG predictors have no preceding pixel or row.
    return tuple(zlib.decompress(b''.join(compressed))[1:4])


class OverlayRenderingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            raise unittest.SkipTest('Optional browser check requires playwright')
        edge = Path('C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe')
        if not edge.exists():
            raise unittest.SkipTest('Microsoft Edge is not installed at the expected path')
        cls.runtime = sync_playwright().start()
        cls.browser = cls.runtime.chromium.launch(executable_path=str(edge), headless=True)

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.runtime.stop()

    def setUp(self):
        self.page = self.browser.new_page(viewport=dict(width=500, height=500))
        self.addCleanup(self.page.close)
        settings = schema(AimConfig())
        self.transport = None
        self.snapshot_requests = 0
        def serve(route):
            name = urlsplit(route.request.url).path.lstrip('/')
            if name.startswith('AimAssist/'):
                name = name[len('AimAssist/'):]
            if name == 'transport.json' and self.transport:
                return route.fulfill(json=dict(url=self.transport.url))
            if name.startswith('api/'):
                return route.fulfill(json=dict(settings=settings, values={}))
            if name == 'state.json':
                self.snapshot_requests += 1
                return route.fulfill(json=dict(active=False, connected=False, updated=0, settings={}))
            if name == 'presets.json':
                return route.fulfill(json={})
            if name == 'defaults.json':
                return route.fulfill(json={item['uniqueID']: item['value'] for item in settings})
            name = name or 'index.html'
            if name not in ('index.html', 'app.css', 'app.js'):
                return route.fulfill(status=404)
            types = dict(html='text/html; charset=utf-8', css='text/css; charset=utf-8', js='text/javascript; charset=utf-8')
            route.fulfill(body=(Path(__file__).parent / 'overlay' / name).read_bytes(), content_type=types[name.rsplit('.', 1)[1]])
        self.page.route('http://overlay.test/**', serve)

    def test_hud_iframe_preserves_the_background_behind_it(self):
        self.page.set_content('<body style="margin:0;background:#32b86a">'
                              '<iframe style="border:0;width:500px;height:500px" src="http://overlay.test/"></iframe>')
        self.page.frames[1].wait_for_function("typeof draw === 'function'")
        self.assertEqual(screenshot_pixel(self.page, 300, 300), (50, 184, 106))

    def test_configuration_panel_keeps_its_dark_background(self):
        self.page.goto('http://overlay.test/?configure=1')
        self.page.wait_for_selector('#setting-strength')
        self.assertEqual(screenshot_pixel(self.page, 1, 1), (16, 23, 29))

    def test_live_frames_reach_browser_without_repeated_snapshot_requests(self):
        count = 0
        def sample(data):
            nonlocal count
            count += 1
            data.update(cursor=[100 + count % 100, 200], hand=[100 + count % 100, 200], updated=time.time())
            return data
        self.transport = OverlayStream(sample)
        self.addCleanup(self.transport.close)
        values = {item['uniqueID']: item['value'] for item in schema(AimConfig())}
        self.transport.update(dict(active=True, connected=True, window=[0, 0, 500, 500],
                                   radius=30, max_offset=10, settings=values, path=[], reason='assisting'))
        # Use a real loopback document, not route.fulfill: Chromium classifies
        # synthetic documents as public and denies their local-network requests.
        case = self
        class PageHandler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass
            def do_GET(self):
                name = urlsplit(self.path).path.lstrip('/') or 'index.html'
                if name == 'transport.json':
                    body, kind = json.dumps(dict(url=case.transport.url)).encode(), 'application/json'
                elif name == 'state.json':
                    case.snapshot_requests += 1
                    body, kind = b'{"connected":false}', 'application/json'
                elif name in ('index.html', 'app.js', 'app.css'):
                    body = (Path(__file__).parent / 'overlay' / name).read_bytes()
                    kind = {'html': 'text/html', 'js': 'text/javascript', 'css': 'text/css'}[name.rsplit('.', 1)[1]]
                else:
                    self.send_error(404)
                    return
                self.send_response(200)
                self.send_header('Content-Type', kind + '; charset=utf-8')
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                self.wfile.write(body)
        server = ThreadingHTTPServer(('127.0.0.1', 0), PageHandler)
        thread = threading.Thread(target=lambda: server.serve_forever(poll_interval=.05), daemon=True)
        thread.start()
        def stop_page():
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)
        self.addCleanup(stop_page)
        origin = f'http://127.0.0.1:{server.server_port}'
        allowed = patch('overlay_stream.TOSU_ORIGINS', frozenset((origin,)))
        allowed.start()
        self.addCleanup(allowed.stop)
        self.page.goto(origin + '/')
        self.page.wait_for_function('stream && stream.readyState === EventSource.OPEN && connected()')
        ages = self.page.evaluate('''() => new Promise(resolve => {
            const ages = [];
            function frame() {
                ages.push(Math.max(0, Date.now() - state.updated * 1000));
                if (ages.length < 45) requestAnimationFrame(frame); else resolve(ages);
            }
            requestAnimationFrame(frame);
        })''')
        self.assertGreater(count, 10)
        self.assertLessEqual(self.snapshot_requests, 1)
        print(f'Browser live-frame age: median={statistics.median(ages):.1f} ms; max={max(ages):.1f} ms; snapshot fetches={self.snapshot_requests}')


if __name__ == '__main__':
    unittest.main()

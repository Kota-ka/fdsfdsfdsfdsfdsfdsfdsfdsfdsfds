import json
import time
import unittest
from urllib.error import HTTPError
from urllib.request import Request, build_opener, ProxyHandler

from overlay_stream import OverlayStream


def read_event(response):
    event, data = 'message', None
    while True:
        line = response.readline().decode().strip()
        if line.startswith('event: '):
            event = line[7:]
        elif line.startswith('data: '):
            data = json.loads(line[6:])
        elif not line and data is not None:
            return event, data


class StreamTests(unittest.TestCase):
    def setUp(self):
        self.count = 0
        def sample(data):
            self.count += 1
            data.update(cursor=[self.count, 0], updated=time.time())
            return data
        self.stream = OverlayStream(sample)
        self.addCleanup(self.stream.close)
        self.client = build_opener(ProxyHandler({}))

    def test_events_contain_latest_frames_without_a_file_roundtrip(self):
        self.stream.update(dict(active=True, connected=True))
        request = Request(self.stream.url, headers={'Origin': 'http://localhost:24050'})
        with self.client.open(request, timeout=2) as response:
            self.assertEqual(response.headers['Access-Control-Allow-Origin'], 'http://localhost:24050')
            frames = []
            kinds = []
            for _ in range(3):
                event, data = read_event(response)
                kinds.append(event)
                frames.append(data)
        self.assertEqual([data['cursor'][0] for data in frames], [1, 2, 3])
        self.assertEqual(kinds, ['message', 'cursor', 'cursor'])

    def test_untrusted_origin_and_wrong_token_are_rejected(self):
        for url, origin, expected in ((self.stream.url, 'https://example.com', 403),
                                      (self.stream.url + 'bad', 'http://localhost:24050', 404)):
            with self.subTest(origin=origin), self.assertRaises(HTTPError) as error:
                self.client.open(Request(url, headers={'Origin': origin}), timeout=2)
            self.assertEqual(error.exception.code, expected)
            error.exception.close()

    def test_close_is_idempotent(self):
        self.stream.close()
        self.stream.close()
        self.assertFalse(self.stream.thread.is_alive())


if __name__ == '__main__':
    unittest.main()

import threading
import time
import unittest
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from takumi.model_transport import open_stream


class TransportTests(unittest.TestCase):
    def test_cancel_interrupts_wait_for_headers_and_wait_for_token(self):
        for headers_first in (False, True):
            with self.subTest(headers_first=headers_first):
                entered, release, cancel = threading.Event(), threading.Event(), threading.Event()
                class Handler(BaseHTTPRequestHandler):
                    def log_message(self, *args):
                        pass
                    def do_POST(self):
                        self.rfile.read(int(self.headers['Content-Length']))
                        if headers_first:
                            self.send_response(200)
                            self.end_headers()
                            self.wfile.flush()
                        entered.set()
                        release.wait(5)
                http = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
                threading.Thread(target=http.serve_forever, daemon=True).start()
                result = []
                def consume():
                    try:
                        req = urllib.request.Request(f'http://127.0.0.1:{http.server_port}/api/chat', data=b'{}')
                        with open_stream(req, cancel) as response:
                            list(response)
                    except OSError:
                        pass
                    finally:
                        result.append('exited')
                worker = threading.Thread(target=consume)
                worker.start()
                try:
                    self.assertTrue(entered.wait(2))
                    began = time.monotonic()
                    cancel.set()
                    worker.join(1)
                    self.assertFalse(worker.is_alive(), 'Cancellation did not interrupt the owned socket')
                    self.assertLess(time.monotonic() - began, 1)
                    self.assertEqual(result, ['exited'])
                finally:
                    release.set()
                    http.shutdown()
                    http.server_close()


if __name__ == '__main__':
    unittest.main()

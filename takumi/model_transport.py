"""Cancellable request-owned reads; the shared model service is never terminated."""
from contextlib import contextmanager
import http.client
import io
import select
import time
import urllib.error
from urllib.parse import urlsplit


class _Reader(io.RawIOBase):
    def __init__(self, sock, cancel, timeout):
        # HTTP/1.0 transfers connection ownership to this response reader.
        self.sock = sock.dup()
        self.sock.setblocking(False)
        self.cancel, self.timeout = cancel, timeout

    def readable(self):
        return True

    def readinto(self, buffer):
        deadline = time.monotonic() + self.timeout
        while True:
            if self.cancel.is_set():
                raise InterruptedError('Model request cancelled')
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError('Local model response timed out')
            ready, _, _ = select.select([self.sock], [], [], min(.025, remaining))
            if ready:
                try:
                    return self.sock.recv_into(buffer)
                except BlockingIOError:
                    pass

    def close(self):
        if not self.closed:
            self.sock.close()
        super().close()


@contextmanager
def open_stream(request, cancel, timeout=240):
    url = urlsplit(request.full_url)
    if url.scheme != 'http' or url.hostname not in ('127.0.0.1', 'localhost', '::1'):
        raise ValueError('Model streaming requires the configured local HTTP endpoint.')
    class SocketReader:
        def __init__(self, sock):
            self.sock = sock
        def makefile(self, mode):
            return io.BufferedReader(_Reader(self.sock, cancel, timeout))
    class Response(http.client.HTTPResponse):
        def __init__(self, sock, **kwargs):
            super().__init__(SocketReader(sock), **kwargs)
    connection = http.client.HTTPConnection(url.hostname, url.port, timeout=5)
    connection.response_class = Response
    try:
        if cancel.is_set():
            raise InterruptedError('Model request cancelled')
        path = url.path + ('?' + url.query if url.query else '')
        connection.request(request.get_method(), path, body=request.data, headers=dict(request.header_items()))
        response = connection.getresponse()
        if response.status >= 400:
            response.close()
            raise urllib.error.HTTPError(request.full_url, response.status, response.reason, response.headers, None)
        with response:
            yield response
    finally:
        connection.close()

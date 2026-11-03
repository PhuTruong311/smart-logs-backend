"""osrm_test_server.py - OSRM giả chạy cục bộ (127.0.0.1) chỉ dùng cho kiểm thử.

Không phải OSRM thật: thời gian và khoảng cách được tính xấp xỉ theo khoảng cách
Manhattan giữa các toạ độ, dùng để kiểm tra luồng gọi API, retry và xử lý lỗi.
"""
import json
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

for _key in ("NO_PROXY", "no_proxy"):
    os.environ[_key] = "127.0.0.1,localhost"

METERS_PER_DEGREE = 111000.0


class FakeOsrmServer:
    def __init__(self, speed_mps: float = 10.0):
        self.mode = "ok"
        self.fail_first = 0
        self.sleep_s = 0.0
        self.blocked = set()
        self.hits = 0
        self.paths = []
        self.speed_mps = speed_mps
        self._lock = threading.Lock()
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                return None

            def do_GET(self):
                outer._handle(self)

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._server.daemon_threads = True
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    @property
    def base_url(self) -> str:
        host, port = self._server.server_address
        return f"http://{host}:{port}"

    def start(self) -> "FakeOsrmServer":
        self._thread.start()
        return self

    def stop(self) -> None:
        self._server.shutdown()
        self._server.server_close()

    def __enter__(self) -> "FakeOsrmServer":
        return self.start()

    def __exit__(self, *exc) -> None:
        self.stop()

    def _distance(self, a, b) -> float:
        return (abs(a[0] - b[0]) + abs(a[1] - b[1])) * METERS_PER_DEGREE

    def _send(self, h, status: int, payload, raw: bool = False) -> None:
        body = payload if raw else json.dumps(payload).encode("utf-8")
        try:
            h.send_response(status)
            h.send_header("Content-Type", "application/json")
            h.send_header("Content-Length", str(len(body)))
            h.end_headers()
            h.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            return None

    def _handle(self, h) -> None:
        with self._lock:
            self.hits += 1
            n = self.hits
            self.paths.append(h.path)
        if self.sleep_s:
            time.sleep(self.sleep_s)
        if n <= self.fail_first or self.mode == "http500":
            return self._send(h, 500, {"code": "Error", "message": "boom"})
        if self.mode == "badjson":
            return self._send(h, 200, b"<html>not json</html>", raw=True)
        if self.mode == "noroute":
            return self._send(h, 400, {"code": "NoRoute", "message": "no route"})
        if self.mode == "errorcode":
            return self._send(h, 200, {"code": "InvalidQuery", "message": "bad query"})

        parts = h.path.split("?")[0].split("/")
        service = parts[1]
        coords = [tuple(float(v) for v in p.split(",")) for p in parts[4].split(";")]
        pts = [(c[1], c[0]) for c in coords]

        if service == "route":
            if self.mode == "malformed":
                return self._send(h, 200, {"code": "Ok", "routes": []})
            dist = self._distance(pts[0], pts[1])
            return self._send(h, 200, {"code": "Ok", "routes": [
                {"duration": dist / self.speed_mps, "distance": dist}]})

        size = len(pts)
        if self.mode == "malformed":
            return self._send(h, 200, {"code": "Ok", "durations": [[0.0]], "distances": [[0.0]]})
        durations, distances = [], []
        for i in range(size):
            drow, lrow = [], []
            for j in range(size):
                if (i, j) in self.blocked:
                    drow.append(None)
                    lrow.append(None)
                else:
                    d = self._distance(pts[i], pts[j])
                    drow.append(d / self.speed_mps)
                    lrow.append(d)
            durations.append(drow)
            distances.append(lrow)
        return self._send(h, 200, {"code": "Ok", "durations": durations, "distances": distances})

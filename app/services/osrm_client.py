"""osrm_client.py - Client OSRM (Route API và Table API) chỉ dùng thư viện chuẩn.

Nguyên tắc
  - Endpoint và số lần thử tối đa (max_attempts, tức MAX_ATTEMPTS) bắt buộc phải
    được cấu hình; không có giá trị mặc định ngầm và không có endpoint cứng.
  - Thử lại luôn hữu hạn: chỉ thử lại khi lỗi mạng, timeout, HTTP 429 hoặc 5xx.
    Phản hồi không hợp lệ, lỗi 4xx khác và "không có đường" không được thử lại.
  - Lỗi không bị che giấu: sau khi hết số lần thử sẽ ném OsrmUnavailableError.
  - Thời gian và khoảng cách từ OSRM là ước lượng theo dữ liệu đường bộ, không
    gồm tắc đường thời gian thực và không phải thời gian giao hàng thực tế.

Toạ độ truyền vào là (lat, lon); client tự đổi sang thứ tự lon,lat của OSRM.
"""
from __future__ import annotations

import http.client
import json
import math
import os
import re
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Callable, List, Mapping, Optional, Sequence, Tuple

Coord = Tuple[float, float]

ENV_BASE_URL = "OSRM_BASE_URL"
ENV_MAX_ATTEMPTS = "OSRM_MAX_ATTEMPTS"
ENV_TIMEOUT = "OSRM_TIMEOUT_S"


class OsrmError(Exception):
    """Lớp cơ sở cho mọi lỗi OSRM."""


class OsrmConfigError(OsrmError, ValueError):
    """Cấu hình OSRM thiếu hoặc không hợp lệ."""


class OsrmInputError(OsrmError, ValueError):
    """Toạ độ đầu vào không hợp lệ (phát hiện trước khi gọi API)."""


class OsrmUnavailableError(OsrmError):
    """Dịch vụ không phản hồi sau toàn bộ số lần thử (mạng, timeout, 429, 5xx)."""


class OsrmResponseError(OsrmError):
    """Phản hồi không hợp lệ hoặc OSRM báo lỗi (không thử lại)."""


class OsrmNoRouteError(OsrmError):
    """OSRM xác nhận không có đường đi giữa các điểm."""


@dataclass(frozen=True)
class OsrmConfig:
    base_url: str
    timeout_s: float
    max_attempts: int
    profile: str = "driving"
    backoff_s: float = 0.5

    def __post_init__(self):
        if not isinstance(self.base_url, str) or not re.match(r"^https?://[^\s/]+", self.base_url):
            raise OsrmConfigError("base_url phải bắt đầu bằng http:// hoặc https://")
        if isinstance(self.max_attempts, bool) or not isinstance(self.max_attempts, int) \
                or self.max_attempts < 1:
            raise OsrmConfigError("max_attempts phải là số nguyên >= 1")
        if not _num_ok(self.timeout_s) or self.timeout_s <= 0:
            raise OsrmConfigError("timeout_s phải là số dương hữu hạn")
        if not _num_ok(self.backoff_s):
            raise OsrmConfigError("backoff_s phải là số không âm hữu hạn")
        if not isinstance(self.profile, str) or not re.fullmatch(r"[A-Za-z0-9_-]+", self.profile):
            raise OsrmConfigError("profile không hợp lệ")

    @classmethod
    def from_sources(cls, base_url: Optional[str] = None,
                     max_attempts: Optional[int] = None,
                     timeout_s: Optional[float] = None,
                     profile: str = "driving", backoff_s: float = 0.5,
                     environ: Optional[Mapping[str, str]] = None) -> "OsrmConfig":
        """Giá trị truyền trực tiếp được ưu tiên, nếu thiếu thì đọc biến môi trường
        OSRM_BASE_URL, OSRM_MAX_ATTEMPTS, OSRM_TIMEOUT_S."""
        env = os.environ if environ is None else environ
        url = base_url or env.get(ENV_BASE_URL)
        attempts = max_attempts if max_attempts is not None else env.get(ENV_MAX_ATTEMPTS)
        timeout = timeout_s if timeout_s is not None else env.get(ENV_TIMEOUT)
        missing = []
        if not url:
            missing.append(f"base_url ({ENV_BASE_URL})")
        if attempts in (None, ""):
            missing.append(f"max_attempts ({ENV_MAX_ATTEMPTS})")
        if timeout in (None, ""):
            missing.append(f"timeout_s ({ENV_TIMEOUT})")
        if missing:
            raise OsrmConfigError("Thiếu cấu hình OSRM: " + ", ".join(missing))
        try:
            attempts = int(attempts)
            timeout = float(timeout)
        except (TypeError, ValueError) as exc:
            raise OsrmConfigError("max_attempts hoặc timeout_s không phải số hợp lệ") from exc
        return cls(base_url=url, timeout_s=timeout, max_attempts=attempts,
                   profile=profile, backoff_s=backoff_s)


@dataclass(frozen=True)
class RouteResult:
    duration_s: float
    distance_m: float


@dataclass(frozen=True)
class TableResult:
    durations: List[List[Optional[float]]]
    distances: List[List[Optional[float]]]


def _num_ok(x: Any) -> bool:
    if x is None or isinstance(x, bool) or not isinstance(x, (int, float)):
        return False
    return math.isfinite(x) and x >= 0


def _validate_coords(coords: Sequence[Coord], minimum: int) -> List[Coord]:
    if not isinstance(coords, (list, tuple)) or len(coords) < minimum:
        raise OsrmInputError(f"Cần ít nhất {minimum} toạ độ")
    out: List[Coord] = []
    for c in coords:
        if not isinstance(c, (list, tuple)) or len(c) != 2:
            raise OsrmInputError(f"Toạ độ phải là (lat, lon): {c!r}")
        lat, lon = c
        for v in (lat, lon):
            if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
                raise OsrmInputError(f"Toạ độ không hợp lệ: {c!r}")
        if not (-90.0 <= lat <= 90.0) or not (-180.0 <= lon <= 180.0):
            raise OsrmInputError(f"Toạ độ ngoài phạm vi: {c!r}")
        out.append((float(lat), float(lon)))
    return out


def _encode(coords: Sequence[Coord]) -> str:
    return ";".join(f"{lon:.6f},{lat:.6f}" for lat, lon in coords)


def _matrix(value: Any, n: int, name: str) -> List[List[Optional[float]]]:
    if not isinstance(value, list) or len(value) != n:
        raise OsrmResponseError(f"Phản hồi OSRM thiếu hoặc sai kích thước '{name}'")
    out: List[List[Optional[float]]] = []
    for row in value:
        if not isinstance(row, list) or len(row) != n:
            raise OsrmResponseError(f"Phản hồi OSRM sai kích thước '{name}'")
        parsed: List[Optional[float]] = []
        for cell in row:
            if cell is None:
                parsed.append(None)
            elif _num_ok(cell):
                parsed.append(float(cell))
            else:
                raise OsrmResponseError(f"Giá trị không hợp lệ trong '{name}': {cell!r}")
        out.append(parsed)
    return out


class OsrmClient:
    def __init__(self, config: OsrmConfig,
                 opener: Optional[Callable[..., Any]] = None,
                 sleep: Callable[[float], None] = time.sleep):
        if not isinstance(config, OsrmConfig):
            raise OsrmConfigError("config phải là OsrmConfig")
        self.config = config
        self._open = opener or urllib.request.urlopen
        self._sleep = sleep
        self.calls = 0

    def _get_json(self, path: str) -> dict:
        cfg = self.config
        url = cfg.base_url.rstrip("/") + path
        last = "không rõ"
        for attempt in range(1, cfg.max_attempts + 1):
            self.calls += 1
            try:
                req = urllib.request.Request(url, headers={"Accept": "application/json"})
                with self._open(req, timeout=cfg.timeout_s) as resp:
                    body = resp.read()
            except urllib.error.HTTPError as exc:
                if exc.code == 429 or exc.code >= 500:
                    last = f"HTTP {exc.code}"
                    exc.close()
                else:
                    self._raise_client_error(exc)
            except (urllib.error.URLError, http.client.HTTPException, OSError) as exc:
                last = f"{type(exc).__name__}: {exc}"
            else:
                return self._parse(body)
            if attempt < cfg.max_attempts:
                self._sleep(cfg.backoff_s * attempt)
        raise OsrmUnavailableError(
            f"OSRM không khả dụng sau {cfg.max_attempts} lần thử (lỗi cuối: {last})")

    @staticmethod
    def _raise_client_error(exc: urllib.error.HTTPError) -> None:
        code, message = None, None
        try:
            data = json.loads(exc.read().decode("utf-8"))
            if isinstance(data, dict):
                code, message = data.get("code"), data.get("message")
        except (ValueError, UnicodeDecodeError, OSError):
            pass
        finally:
            exc.close()
        if code == "NoRoute":
            raise OsrmNoRouteError(message or "OSRM: không có đường đi")
        detail = " ".join(str(x) for x in (code, message) if x)
        raise OsrmResponseError(f"HTTP {exc.code} từ OSRM {detail}".strip())

    @staticmethod
    def _parse(body: bytes) -> dict:
        try:
            data = json.loads(body.decode("utf-8"))
        except (ValueError, UnicodeDecodeError) as exc:
            raise OsrmResponseError("Phản hồi OSRM không phải JSON hợp lệ") from exc
        if not isinstance(data, dict):
            raise OsrmResponseError("Phản hồi OSRM không phải đối tượng JSON")
        return data

    @staticmethod
    def _check_code(data: dict) -> None:
        code = data.get("code")
        if code == "Ok":
            return
        if code == "NoRoute":
            raise OsrmNoRouteError(str(data.get("message") or "OSRM: không có đường đi"))
        raise OsrmResponseError(f"OSRM trả về code={code!r}: {data.get('message')}")

    def route(self, origin: Coord, destination: Coord) -> RouteResult:
        coords = _validate_coords([origin, destination], 2)
        path = (f"/route/v1/{self.config.profile}/{_encode(coords)}"
                f"?overview=false&alternatives=false")
        data = self._get_json(path)
        self._check_code(data)
        routes = data.get("routes")
        if not isinstance(routes, list) or not routes or not isinstance(routes[0], dict):
            raise OsrmResponseError("Phản hồi OSRM không có tuyến nào")
        duration, distance = routes[0].get("duration"), routes[0].get("distance")
        if not _num_ok(duration) or not _num_ok(distance):
            raise OsrmResponseError("duration hoặc distance của tuyến không hợp lệ")
        return RouteResult(float(duration), float(distance))

    def table(self, coords: Sequence[Coord]) -> TableResult:
        pts = _validate_coords(coords, 2)
        path = (f"/table/v1/{self.config.profile}/{_encode(pts)}"
                f"?annotations=duration,distance")
        data = self._get_json(path)
        self._check_code(data)
        n = len(pts)
        return TableResult(_matrix(data.get("durations"), n, "durations"),
                           _matrix(data.get("distances"), n, "distances"))

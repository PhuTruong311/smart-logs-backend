import math
from dataclasses import dataclass, field
from typing import List, Optional, Tuple, Any, Union, Dict

SOURCE_ML = "ML"
SOURCE_OSRM = "OSRM"


class NoValidRouteError(Exception):
    """Lỗi khi không có tuyến đường nào hợp lệ."""
    pass


@dataclass
class RouteCandidate:
    route_id: str
    origin: Optional[Tuple[float, float]] = None
    destination: Optional[Tuple[float, float]] = None
    drivable: bool = True
    eta_ml: Optional[Any] = None
    eta_osrm: Optional[float] = None
    distance_m: Optional[float] = None

    @classmethod
    def from_dict(cls, data: dict):
        route_id = str(data.get("route_id", ""))

        def _parse_coord(val):
            if val is None:
                return None
            if isinstance(val, (list, tuple)):
                if len(val) != 2:
                    raise ValueError(f"Tọa độ phải có đúng 2 phần tử, nhận được {len(val)}")
                return (float(val[0]), float(val[1]))
            return val

        origin = _parse_coord(data.get("origin"))
        destination = _parse_coord(data.get("destination"))

        return cls(
            route_id=route_id,
            origin=origin,
            destination=destination,
            drivable=data.get("drivable", True),
            eta_ml=data.get("eta_ml"),
            eta_osrm=data.get("eta_osrm"),
            distance_m=data.get("distance_m")
        )

    @property
    def eta_s(self) -> Optional[float]:
        eta, _ = resolve_eta(self)
        return eta

    @property
    def source(self) -> Optional[str]:
        _, src = resolve_eta(self)
        return src


@dataclass
class SelectionResult:
    best: Optional[RouteCandidate] = None
    ranking: List[str] = field(default_factory=list)
    tie_broken: bool = False
    warnings: List[str] = field(default_factory=list)
    excluded: List[Tuple[str, str]] = field(default_factory=list)


def calculate_haversine_distance(origin, destination) -> float:
    """Tính khoảng cách Haversine giữa 2 điểm (km)."""
    if hasattr(origin, 'lat'):
        lat1, lon1 = origin.lat, origin.lng
    else:
        lat1, lon1 = origin[0], origin[1]

    if hasattr(destination, 'lat'):
        lat2, lon2 = destination.lat, destination.lng
    else:
        lat2, lon2 = destination[0], destination[1]

    r = 6371.0
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)

    a = (math.sin(dlat / 2) ** 2 +
         math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) *
         math.sin(dlon / 2) ** 2)
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return r * c


def resolve_eta(candidate: RouteCandidate) -> Tuple[Optional[float], Optional[str]]:
    """Xác định ETA và nguồn dữ liệu hợp lệ (ML -> OSRM)."""
    if not candidate.drivable:
        return None, None

    # Kiểm tra tính hợp lệ của eta_ml
    valid_ml = False
    ml_val = candidate.eta_ml

    if ml_val is not None and not isinstance(ml_val, bool) and isinstance(ml_val, (int, float)):
        if not math.isnan(ml_val) and not math.isinf(ml_val) and ml_val >= 0:
            valid_ml = True

    if valid_ml:
        return float(ml_val), SOURCE_ML

    # Fallback sang OSRM nếu eta_ml không hợp lệ
    osrm_val = candidate.eta_osrm
    if osrm_val is not None and not isinstance(osrm_val, bool) and isinstance(osrm_val, (int, float)):
        if not math.isnan(osrm_val) and not math.isinf(osrm_val) and osrm_val >= 0:
            return float(osrm_val), SOURCE_OSRM

    return None, None


def needs_osrm_fallback(candidate: Union[RouteCandidate, List[RouteCandidate]]) -> bool:
    if isinstance(candidate, RouteCandidate):
        candidates = [candidate]
    else:
        candidates = list(candidate)

    for c in candidates:
        if not c.drivable:
            continue
        eta, _ = resolve_eta(c)
        if eta is None:
            return True
    return False


def fill_osrm_fallback(candidates: List[RouteCandidate], osrm_client) -> Tuple[List[RouteCandidate], Dict[str, str]]:
    failures = {}
    if osrm_client is None:
        return candidates, failures

    cache = {}

    for c in candidates:
        if not c.drivable:
            continue

        eta, _ = resolve_eta(c)
        if eta is not None:
            continue

        if c.origin is None or c.destination is None:
            continue

        cache_key = (c.origin, c.destination)
        if cache_key in cache:
            res_val = cache[cache_key]
            if isinstance(res_val, Exception):
                failures[c.route_id] = str(res_val)
            else:
                c.eta_osrm = res_val.duration_s
                if c.distance_m is None:
                    c.distance_m = res_val.distance_m
            continue

        try:
            res = osrm_client.route(c.origin, c.destination)
            if hasattr(res, 'duration_s'):
                c.eta_osrm = res.duration_s
                if c.distance_m is None:
                    c.distance_m = res.distance_m
                cache[cache_key] = res
            else:
                c.eta_osrm = float(res)
                cache[cache_key] = res
        except Exception as e:
            failures[c.route_id] = str(e)
            cache[cache_key] = e

    return candidates, failures


def select_route(candidates: List[RouteCandidate]) -> SelectionResult:
    if not candidates:
        raise NoValidRouteError("Danh sách tuyến đường rỗng.")

    excluded = []
    seen_ids = set()
    valid_candidates = []

    for c in candidates:
        # 1. Lọc route_id trùng
        if c.route_id in seen_ids:
            excluded.append((c.route_id, "route_id trùng lặp"))
            continue
        seen_ids.add(c.route_id)

        # 2. Lọc tuyến không đi được
        if not c.drivable:
            excluded.append((c.route_id, "không đi được"))
            continue

        # 3. Lọc tuyến thiếu ETA
        eta, _ = resolve_eta(c)
        if eta is None:
            excluded.append((c.route_id, "thiếu ETA"))
            continue

        valid_candidates.append(c)

    if not valid_candidates:
        raise NoValidRouteError("Không có tuyến đường nào hợp lệ.")

    # Kiểm tra nguồn dữ liệu để tạo cảnh báo (warnings)
    sources = {resolve_eta(c)[1] for c in valid_candidates}
    warnings = []
    if SOURCE_OSRM in sources:
        warnings.append("Có tuyến đường dùng fallback")
    if len(sources) > 1:
        warnings.append("Các tuyến đường có nguồn khác nhau")

    # Sắp xếp tuyến đường:
    # 1. ETA thấp nhất
    # 2. Khoảng cách ngắn nhất (m) -> nếu thiếu xem như vô cùng lớn
    # 3. Alphabetical theo route_id
    def sort_key(c: RouteCandidate):
        eta, _ = resolve_eta(c)
        dist = c.distance_m if c.distance_m is not None else float('inf')
        return (eta, dist, c.route_id)

    sorted_candidates = sorted(valid_candidates, key=sort_key)
    best = sorted_candidates[0]

    # Kiểm tra xem có hòa ETA giữa các tuyến top không
    best_eta, _ = resolve_eta(best)
    tie_broken = False
    if len(sorted_candidates) > 1:
        second_eta, _ = resolve_eta(sorted_candidates[1])
        if best_eta == second_eta:
            tie_broken = True

    ranking = sorted_candidates

    return SelectionResult(
        best=best,
        ranking=ranking,
        tie_broken=tie_broken,
        warnings=warnings,
        excluded=excluded
    )


def select_route_with_fallback(candidates: List[RouteCandidate], osrm_client=None) -> SelectionResult:
    if not candidates:
        raise NoValidRouteError("Danh sách tuyến đường rỗng.")

    # Lọc các tuyến cần gọi OSRM
    cands_to_fill = []
    for c in candidates:
        if c.drivable and c.origin is not None and c.destination is not None:
            eta, _ = resolve_eta(c)
            if eta is None:
                cands_to_fill.append(c)

    osrm_failures = {}
    if cands_to_fill and osrm_client is not None:
        _, osrm_failures = fill_osrm_fallback(candidates, osrm_client)

    try:
        res = select_route(candidates)
    except NoValidRouteError as e:
        if osrm_failures:
            first_err = list(osrm_failures.values())[0]
            raise NoValidRouteError(f"OSRM lỗi: {first_err}")
        raise e

    # Bổ sung thông tin lỗi OSRM vào danh sách excluded & warnings
    for route_id, err_msg in osrm_failures.items():
        res.excluded.append((route_id, f"OSRM lỗi: {err_msg}"))
        res.warnings.append("Gọi OSRM thất bại")

    return res
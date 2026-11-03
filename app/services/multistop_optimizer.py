"""multistop_optimizer.py - Tối ưu thứ tự giao hàng cho 1 xe, 1 kho, 3-5 điểm.

Đầu vào
  - time_matrix[i][j]: thời gian di chuyển từ nút i đến nút j (giây). Nút 0 là kho
    (hoặc vị trí hiện tại của xe khi tính lại kế hoạch). None / NaN / inf / số âm
    = đường không đi được. Ma trận có thể do Người 1 cung cấp hoặc lấy từ OSRM
    Table API (optimize_with_osrm).
  - distance_matrix[i][j] (tuỳ chọn): khoảng cách (mét).
  - stops: danh sách Stop(order_id, node, service_s, deadline_s).
  - initial_order (tuỳ chọn): thứ tự giao ban đầu; mặc định là thứ tự của stops.

Mô hình thời gian (xe xuất phát từ nút 0 tại start_time_s, mặc định 0)
  arrival(k)  = complete(k-1) + travel(k-1 -> k)
  complete(k) = arrival(k) + service_s(k)
  Đơn trễ khi complete(k) > deadline_s. Hạn giao là giờ tuyệt đối cùng gốc thời
  gian với start_time_s và luôn là hạn gốc, không đổi khi ETA thay đổi.
  ETA bi quan: mỗi đoạn đường được nhân (1 + risk_margin); thời gian dừng giữ nguyên.
  Đơn "nguy cơ trễ" khi ETA bi quan vượt hạn.

Hai chế độ chọn thứ tự (vét cạn mọi hoán vị vì chỉ 3-5 điểm, nên kết quả là tối ưu
chính xác theo tiêu chí đã chọn)
  1. lexicographic (khi không truyền cost): ít đơn trễ -> tổng thời gian trễ ->
     tổng thời gian -> tổng khoảng cách -> thứ tự order_id.
  2. soft_cost (khi truyền CostConfig):
        cost = late_weight * tổng giây trễ theo ETA bi quan + time_weight * tổng giây hành trình
     Hoà thì xét tổng thời gian, tổng khoảng cách, rồi thứ tự order_id.
     Các tham số CostConfig bắt buộc truyền tường minh, chưa có giá trị mặc định
     được duyệt.

Tính lại có điều kiện (replan): chỉ đổi sang thứ tự mới khi chi phí giảm nhiều hơn
min_improvement so với kế hoạch đang chạy.
"""
from __future__ import annotations

import argparse
import itertools
import json
import math
import sys
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

from app.services.osrm_client import OsrmClient, OsrmConfig, OsrmError

MIN_STOPS = 1
MAX_STOPS = 5
DEPOT = 0
OBJECTIVE_LEXICOGRAPHIC = "lexicographic"
OBJECTIVE_SOFT_COST = "soft_cost"
_EPS = 1e-9


class OptimizerInputError(ValueError):
    """Dữ liệu đầu vào không hợp lệ."""


class NoFeasibleOrderError(ValueError):
    """Không có thứ tự giao nào đi được (đường bị chặn)."""


def _ok(x: Any) -> bool:
    if x is None or isinstance(x, bool):
        return False
    try:
        v = float(x)
    except (TypeError, ValueError):
        return False
    return math.isfinite(v) and v >= 0


@dataclass(frozen=True)
class CostConfig:
    late_weight: float
    time_weight: float
    risk_margin: float
    min_improvement: float

    def __post_init__(self):
        for name in ("late_weight", "time_weight", "risk_margin", "min_improvement"):
            if not _ok(getattr(self, name)):
                raise OptimizerInputError(f"CostConfig.{name} phải là số hữu hạn không âm")
        if self.late_weight == 0 and self.time_weight == 0:
            raise OptimizerInputError("late_weight và time_weight không được cùng bằng 0")

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "CostConfig":
        keys = ("late_weight", "time_weight", "risk_margin", "min_improvement")
        missing = [k for k in keys if k not in d]
        if missing:
            raise OptimizerInputError("Thiếu tham số chi phí: " + ", ".join(missing))
        return cls(**{k: float(d[k]) for k in keys})


@dataclass(frozen=True)
class Stop:
    order_id: str
    node: int
    service_s: float = 0.0
    deadline_s: Optional[float] = None


@dataclass(frozen=True)
class GeoStop:
    order_id: str
    lat: float
    lon: float
    service_s: float = 0.0
    deadline_s: Optional[float] = None


@dataclass
class StopResult:
    seq: int
    order_id: str
    node: int
    arrival_s: float
    complete_s: float
    deadline_s: Optional[float]
    late: bool
    lateness_s: float
    risk_complete_s: float = 0.0
    at_risk: bool = False
    risk_lateness_s: float = 0.0


@dataclass
class Plan:
    sequence: List[str]
    stops: List[StopResult]
    total_time_s: float
    total_distance_m: Optional[float]
    late_count: int
    total_lateness_s: float
    return_to_depot: bool
    risk_margin: float = 0.0
    risk_late_count: int = 0
    total_risk_lateness_s: float = 0.0
    cost: Optional[float] = None
    start_time_s: float = 0.0

    @property
    def late_orders(self) -> List[str]:
        return [s.order_id for s in self.stops if s.late]

    @property
    def at_risk_orders(self) -> List[str]:
        return [s.order_id for s in self.stops if s.at_risk]


@dataclass
class OptimizationResult:
    initial: Optional[Plan]
    optimal: Plan
    changed: bool
    deadline_feasible: bool
    time_saved_s: Optional[float]
    distance_saved_m: Optional[float]
    late_reduced: Optional[int]
    evaluated: int
    unreachable_orders: int
    notes: List[str] = field(default_factory=list)
    objective: str = OBJECTIVE_LEXICOGRAPHIC
    cost_config: Optional[CostConfig] = None
    cost_saved: Optional[float] = None
    risk_feasible: bool = True


@dataclass
class ReplanResult:
    adopted: bool
    current: Optional[Plan]
    candidate: Plan
    chosen: Plan
    improvement: Optional[float]
    reason: str
    evaluated: int
    unreachable_orders: int


def _validate(time_matrix, distance_matrix, stops, max_stops):
    n_stops = len(stops)
    if not (MIN_STOPS <= n_stops <= max_stops):
        raise OptimizerInputError(
            f"Số điểm giao phải từ {MIN_STOPS} đến {max_stops}, nhận được {n_stops}")
    ids = [s.order_id for s in stops]
    if len(set(ids)) != len(ids):
        raise OptimizerInputError("order_id bị trùng")
    nodes = [s.node for s in stops]
    if len(set(nodes)) != len(nodes):
        raise OptimizerInputError("Hai đơn dùng cùng một nút trong ma trận")
    size = len(time_matrix)
    for name, m in (("time_matrix", time_matrix), ("distance_matrix", distance_matrix)):
        if m is None:
            continue
        if len(m) != size or any(len(row) != size for row in m):
            raise OptimizerInputError(f"{name} phải là ma trận vuông {size}x{size}")
    for s in stops:
        if not (1 <= s.node < size):
            raise OptimizerInputError(
                f"Đơn {s.order_id}: node={s.node} ngoài phạm vi 1..{size - 1} "
                f"(nút 0 là kho)")
        if not _ok(s.service_s):
            raise OptimizerInputError(f"Đơn {s.order_id}: service_s không hợp lệ")
        if s.deadline_s is not None and not _ok(s.deadline_s):
            raise OptimizerInputError(f"Đơn {s.order_id}: deadline_s không hợp lệ")


def _check_cost(cost) -> None:
    if cost is not None and not isinstance(cost, CostConfig):
        raise OptimizerInputError("cost phải là CostConfig hoặc None")


def _check_time(name: str, value) -> None:
    if not _ok(value):
        raise OptimizerInputError(f"{name} phải là số hữu hạn không âm")


def evaluate_order(order: Sequence[Stop], time_matrix, distance_matrix=None,
                   return_to_depot: bool = False, start_time_s: float = 0.0,
                   risk_margin: float = 0.0) -> Optional[Plan]:
    """Tính ETA từng đơn cho một thứ tự cụ thể. Trả về None nếu có đoạn không đi được."""
    start = float(start_time_s)
    stretch = 1.0 + float(risk_margin)
    t = start
    rt = start
    dist = 0.0 if distance_matrix is not None else None
    prev = DEPOT
    results: List[StopResult] = []
    late_count, total_late = 0, 0.0
    risk_late_count, total_risk_late = 0, 0.0

    def leg(a: int, b: int) -> Optional[Tuple[float, float]]:
        tt = time_matrix[a][b]
        if not _ok(tt):
            return None
        dd = 0.0
        if distance_matrix is not None:
            if not _ok(distance_matrix[a][b]):
                return None
            dd = float(distance_matrix[a][b])
        return float(tt), dd

    for k, s in enumerate(order, start=1):
        l = leg(prev, s.node)
        if l is None:
            return None
        t += l[0]
        rt += l[0] * stretch
        if dist is not None:
            dist += l[1]
        arrival = t
        t += float(s.service_s)
        rt += float(s.service_s)
        complete, risk_complete = t, rt
        deadline = None if s.deadline_s is None else float(s.deadline_s)
        late = deadline is not None and complete > deadline + _EPS
        lateness = (complete - deadline) if late else 0.0
        at_risk = deadline is not None and risk_complete > deadline + _EPS
        risk_lateness = (risk_complete - deadline) if at_risk else 0.0
        if late:
            late_count += 1
            total_late += lateness
        if at_risk:
            risk_late_count += 1
            total_risk_late += risk_lateness
        results.append(StopResult(k, s.order_id, s.node, arrival, complete,
                                  s.deadline_s, late, lateness,
                                  risk_complete, at_risk, risk_lateness))
        prev = s.node

    if return_to_depot:
        l = leg(prev, DEPOT)
        if l is None:
            return None
        t += l[0]
        if dist is not None:
            dist += l[1]

    return Plan(sequence=[s.order_id for s in order], stops=results,
                total_time_s=t - start, total_distance_m=dist,
                late_count=late_count, total_lateness_s=total_late,
                return_to_depot=return_to_depot, risk_margin=float(risk_margin),
                risk_late_count=risk_late_count,
                total_risk_lateness_s=total_risk_late, start_time_s=start)


def _cost(plan: Plan, cfg: CostConfig) -> float:
    return cfg.late_weight * plan.total_risk_lateness_s + cfg.time_weight * plan.total_time_s


def _rank_key(p: Plan, cost: Optional[CostConfig]):
    dist = round(p.total_distance_m, 6) if p.total_distance_m is not None else 0.0
    if cost is None:
        return (p.late_count, round(p.total_lateness_s, 6), round(p.total_time_s, 6),
                dist, tuple(p.sequence))
    return (round(p.cost, 6), round(p.total_time_s, 6), dist, tuple(p.sequence))


def _search(stops, time_matrix, distance_matrix, return_to_depot, start_time_s, cost):
    margin = cost.risk_margin if cost else 0.0
    best: Optional[Plan] = None
    evaluated = unreachable = 0
    for perm in itertools.permutations(stops):
        evaluated += 1
        plan = evaluate_order(perm, time_matrix, distance_matrix, return_to_depot,
                              start_time_s, margin)
        if plan is None:
            unreachable += 1
            continue
        if cost is not None:
            plan.cost = _cost(plan, cost)
        if best is None or _rank_key(plan, cost) < _rank_key(best, cost):
            best = plan
    return best, evaluated, unreachable


def optimize(time_matrix, stops: Sequence[Stop], distance_matrix=None,
             initial_order: Optional[Sequence[str]] = None,
             return_to_depot: bool = False,
             max_stops: int = MAX_STOPS,
             cost: Optional[CostConfig] = None,
             start_time_s: float = 0.0) -> OptimizationResult:
    stops = list(stops)
    _validate(time_matrix, distance_matrix, stops, max_stops)
    _check_cost(cost)
    _check_time("start_time_s", start_time_s)
    by_id = {s.order_id: s for s in stops}

    if initial_order is None:
        init_stops = stops
    else:
        if sorted(initial_order) != sorted(by_id):
            raise OptimizerInputError("initial_order phải gồm đúng các order_id của stops")
        init_stops = [by_id[i] for i in initial_order]

    best, evaluated, unreachable = _search(stops, time_matrix, distance_matrix,
                                           return_to_depot, start_time_s, cost)
    if best is None:
        raise NoFeasibleOrderError(
            "Không có thứ tự giao nào đi được: mọi hoán vị đều có đoạn đường bị chặn")

    margin = cost.risk_margin if cost else 0.0
    initial = evaluate_order(init_stops, time_matrix, distance_matrix, return_to_depot,
                             start_time_s, margin)
    if initial is not None and cost is not None:
        initial.cost = _cost(initial, cost)

    notes: List[str] = []
    if initial is None:
        notes.append("Thứ tự ban đầu có đoạn đường không đi được nên không tính được ETA.")
    if best.late_count > 0:
        notes.append(f"Không thể giao đúng hạn tất cả đơn; thứ tự tối ưu vẫn trễ "
                     f"{best.late_count} đơn: {', '.join(best.late_orders)}.")
    if cost is not None and best.risk_late_count > 0:
        notes.append(f"Với biên rủi ro {cost.risk_margin:g}, vẫn còn {best.risk_late_count} "
                     f"đơn có nguy cơ trễ: {', '.join(best.at_risk_orders)}.")

    return OptimizationResult(
        initial=initial,
        optimal=best,
        changed=[s.order_id for s in init_stops] != best.sequence,
        deadline_feasible=best.late_count == 0,
        time_saved_s=(initial.total_time_s - best.total_time_s) if initial else None,
        distance_saved_m=(initial.total_distance_m - best.total_distance_m)
        if initial and initial.total_distance_m is not None
        and best.total_distance_m is not None else None,
        late_reduced=(initial.late_count - best.late_count) if initial else None,
        evaluated=evaluated,
        unreachable_orders=unreachable,
        notes=notes,
        objective=OBJECTIVE_SOFT_COST if cost is not None else OBJECTIVE_LEXICOGRAPHIC,
        cost_config=cost,
        cost_saved=(initial.cost - best.cost)
        if initial is not None and initial.cost is not None and best.cost is not None
        else None,
        risk_feasible=best.risk_late_count == 0,
    )


def replan(time_matrix, stops: Sequence[Stop], current_order: Sequence[str],
           cost: CostConfig, distance_matrix=None, now_s: float = 0.0,
           delay_s: float = 0.0, max_stops: int = MAX_STOPS) -> ReplanResult:
    """Tính lại kế hoạch khi xe đang chạy.

    time_matrix: nút 0 là VỊ TRÍ HIỆN TẠI của xe; stops là các đơn chưa giao.
    now_s: giờ hiện tại (cùng gốc thời gian với deadline_s).
    delay_s: thời gian mất thêm do sự cố (hỏng xe, chờ...), cộng vào giờ xuất phát.
    Chỉ đổi thứ tự khi chi phí giảm nhiều hơn cost.min_improvement.
    """
    stops = list(stops)
    _validate(time_matrix, distance_matrix, stops, max_stops)
    if not isinstance(cost, CostConfig):
        raise OptimizerInputError("replan cần CostConfig")
    _check_time("now_s", now_s)
    _check_time("delay_s", delay_s)
    by_id = {s.order_id: s for s in stops}
    if sorted(current_order) != sorted(by_id):
        raise OptimizerInputError("current_order phải gồm đúng các order_id của stops")

    start = float(now_s) + float(delay_s)
    candidate, evaluated, unreachable = _search(stops, time_matrix, distance_matrix,
                                                False, start, cost)
    if candidate is None:
        raise NoFeasibleOrderError(
            "Không có thứ tự giao nào đi được: mọi hoán vị đều có đoạn đường bị chặn")

    current = evaluate_order([by_id[i] for i in current_order], time_matrix,
                             distance_matrix, False, start, cost.risk_margin)
    if current is None:
        return ReplanResult(True, None, candidate, candidate, None,
                            "Kế hoạch hiện tại có đoạn đường không đi được nên bắt buộc đổi.",
                            evaluated, unreachable)
    current.cost = _cost(current, cost)
    improvement = current.cost - candidate.cost
    if improvement > cost.min_improvement + _EPS:
        return ReplanResult(True, current, candidate, candidate, improvement,
                            f"Chi phí giảm {improvement:.2f} > ngưỡng "
                            f"{cost.min_improvement:g}, đổi sang thứ tự mới.",
                            evaluated, unreachable)
    return ReplanResult(False, current, candidate, current, improvement,
                        f"Chi phí chỉ giảm {improvement:.2f} <= ngưỡng "
                        f"{cost.min_improvement:g}, giữ kế hoạch hiện tại.",
                        evaluated, unreachable)


def matrices_from_osrm(client, origin: Tuple[float, float], geo_stops: Sequence[GeoStop]):
    """Lấy (time_matrix, distance_matrix) từ OSRM Table API.

    Nút 0 là origin, nút i (i >= 1) là geo_stops[i-1]. client là osrm_client.OsrmClient.
    Ô OSRM không có đường đi (null) được giữ là None và coi như không đi được.
    """
    coords = [tuple(origin)] + [(g.lat, g.lon) for g in geo_stops]
    table = client.table(coords)
    return table.durations, table.distances


def optimize_with_osrm(client, origin: Tuple[float, float], geo_stops: Sequence[GeoStop],
                       initial_order: Optional[Sequence[str]] = None,
                       return_to_depot: bool = False,
                       cost: Optional[CostConfig] = None,
                       start_time_s: float = 0.0,
                       max_stops: int = MAX_STOPS) -> OptimizationResult:
    geo_stops = list(geo_stops)
    if not (MIN_STOPS <= len(geo_stops) <= max_stops):
        raise OptimizerInputError(
            f"Số điểm giao phải từ {MIN_STOPS} đến {max_stops}, nhận được {len(geo_stops)}")
    if len({g.order_id for g in geo_stops}) != len(geo_stops):
        raise OptimizerInputError("order_id bị trùng")
    for g in geo_stops:
        if not _ok(g.service_s):
            raise OptimizerInputError(f"Đơn {g.order_id}: service_s không hợp lệ")
        if g.deadline_s is not None and not _ok(g.deadline_s):
            raise OptimizerInputError(f"Đơn {g.order_id}: deadline_s không hợp lệ")
    time_matrix, distance_matrix = matrices_from_osrm(client, origin, geo_stops)
    stops = [Stop(g.order_id, i + 1, g.service_s, g.deadline_s)
             for i, g in enumerate(geo_stops)]
    return optimize(time_matrix, stops, distance_matrix, initial_order, return_to_depot,
                    max_stops, cost, start_time_s)


def fmt_time(seconds: float, start_clock: Optional[str] = None) -> str:
    """'HH:MM:SS' kể từ lúc xuất phát; nếu có start_clock 'HH:MM' thì trả về giờ
    trong ngày."""
    s = int(round(seconds))
    if start_clock:
        h0, m0 = (int(x) for x in start_clock.split(":")[:2])
        s += h0 * 3600 + m0 * 60
        return f"{(s // 3600) % 24:02d}:{(s % 3600) // 60:02d}:{s % 60:02d}"
    return f"{s // 3600:02d}:{(s % 3600) // 60:02d}:{s % 60:02d}"


def _plan_lines(title: str, p: Optional[Plan], start_clock: Optional[str]) -> List[str]:
    lines = [title]
    if p is None:
        return lines + ["  (không tính được: có đoạn đường không đi được)"]
    risk = p.risk_margin > 0
    lines.append("  Thứ tự: kho -> " + " -> ".join(p.sequence)
                 + (" -> kho" if p.return_to_depot else ""))
    header = f"  {'STT':<5}{'Đơn':<10}{'Đến':<10}{'Giao xong':<11}"
    if risk:
        header += f"{'ETA bi quan':<13}"
    lines.append(header + f"{'Hạn':<10}Trạng thái")
    for s in p.stops:
        dl = fmt_time(s.deadline_s, start_clock) if s.deadline_s is not None else "-"
        if s.late:
            st = f"TRỄ {fmt_time(s.lateness_s)}"
        elif s.at_risk:
            st = f"NGUY CƠ TRỄ {fmt_time(s.risk_lateness_s)}"
        else:
            st = "đúng hạn"
        row = (f"  {s.seq:<5}{s.order_id:<10}{fmt_time(s.arrival_s, start_clock):<10}"
               f"{fmt_time(s.complete_s, start_clock):<11}")
        if risk:
            row += f"{fmt_time(s.risk_complete_s, start_clock):<13}"
        lines.append(row + f"{dl:<10}{st}")
    dist = f"{p.total_distance_m / 1000:.2f} km" if p.total_distance_m is not None else "n/a"
    summary = (f"  Tổng thời gian: {fmt_time(p.total_time_s)} | Tổng khoảng cách: {dist} "
               f"| Số đơn trễ: {p.late_count}"
               + (f" ({', '.join(p.late_orders)})" if p.late_count else ""))
    if risk:
        summary += f" | Nguy cơ trễ: {p.risk_late_count}"
    if p.cost is not None:
        summary += f" | Chi phí: {p.cost:.2f}"
    lines.append(summary)
    return lines


def format_report(res: OptimizationResult, start_clock: Optional[str] = None) -> str:
    out = ["=" * 70, "BÁO CÁO TỐI ƯU THỨ TỰ GIAO HÀNG", "=" * 70]
    out += _plan_lines("[A] Thứ tự ban đầu", res.initial, start_clock)
    out.append("")
    out += _plan_lines("[B] Thứ tự tối ưu", res.optimal, start_clock)
    out += ["", "[C] So sánh"]
    if res.objective == OBJECTIVE_SOFT_COST and res.cost_config is not None:
        c = res.cost_config
        out.append("  Mục tiêu: hàm chi phí mềm (phạt theo giây trễ trên ETA bi quan + thời gian)")
        out.append(f"  Tham số: late_weight={c.late_weight:g}, time_weight={c.time_weight:g}, "
                   f"risk_margin={c.risk_margin:g}, min_improvement={c.min_improvement:g}")
    else:
        out.append("  Mục tiêu: ưu tiên lần lượt số đơn trễ, tổng trễ, thời gian, khoảng cách")
    out.append("  Thứ tự thay đổi: " + ("có" if res.changed else "không"))
    if res.time_saved_s is not None:
        out.append(f"  Thời gian tiết kiệm: {res.time_saved_s:+.0f} giây (dương = nhanh hơn)")
    if res.distance_saved_m is not None:
        out.append(f"  Khoảng cách tiết kiệm: {res.distance_saved_m / 1000:+.2f} km")
    if res.late_reduced is not None:
        out.append(f"  Số đơn trễ giảm: {res.late_reduced:+d}")
    if res.cost_saved is not None:
        out.append(f"  Chi phí giảm: {res.cost_saved:+.2f}")
    out.append(f"  Giao đúng hạn tất cả đơn: {'có' if res.deadline_feasible else 'KHÔNG'}")
    if res.objective == OBJECTIVE_SOFT_COST:
        out.append(f"  Không có đơn nguy cơ trễ (ETA bi quan): "
                   f"{'có' if res.risk_feasible else 'KHÔNG'}")
    out.append(f"  Đã xét {res.evaluated} hoán vị, loại {res.unreachable_orders} "
               f"hoán vị do đường không đi được")
    for n in res.notes:
        out.append(f"  Lưu ý: {n}")
    return "\n".join(out)


def format_replan_report(r: ReplanResult, start_clock: Optional[str] = None) -> str:
    out = ["=" * 70, "BÁO CÁO TÍNH LẠI KẾ HOẠCH", "=" * 70]
    out.append(f"Quyết định: {'ĐỔI thứ tự' if r.adopted else 'GIỮ kế hoạch hiện tại'}")
    out.append(f"Lý do: {r.reason}")
    out.append("")
    out += _plan_lines("[A] Kế hoạch hiện tại", r.current, start_clock)
    out.append("")
    out += _plan_lines("[B] Kế hoạch tốt nhất tìm được", r.candidate, start_clock)
    out.append("")
    out.append(f"Đã xét {r.evaluated} hoán vị, loại {r.unreachable_orders} do đường không đi được")
    return "\n".join(out)


def result_to_dict(res: OptimizationResult) -> Dict[str, Any]:
    d = asdict(res)
    d["optimal"]["late_orders"] = res.optimal.late_orders
    d["optimal"]["at_risk_orders"] = res.optimal.at_risk_orders
    if res.initial:
        d["initial"]["late_orders"] = res.initial.late_orders
        d["initial"]["at_risk_orders"] = res.initial.at_risk_orders
    return d


def load_input(path: str) -> Dict[str, Any]:
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        raise OptimizerInputError("File đầu vào phải là một đối tượng JSON")
    try:
        cost = CostConfig.from_dict(data["cost"]) if data.get("cost") is not None else None
        common = {
            "initial_order": data.get("initial_order"),
            "return_to_depot": bool(data.get("return_to_depot", False)),
            "start_clock": data.get("start_clock"),
            "start_time_s": float(data.get("start_time_s", 0)),
            "cost": cost,
        }
        if "time_matrix" in data:
            stops = [Stop(str(s["order_id"]), int(s["node"]), float(s.get("service_s", 0)),
                          s.get("deadline_s")) for s in data["stops"]]
            return {"mode": "matrix", "time_matrix": data["time_matrix"],
                    "distance_matrix": data.get("distance_matrix"), "stops": stops, **common}
        if "depot" in data:
            origin = (float(data["depot"]["lat"]), float(data["depot"]["lon"]))
            geo = [GeoStop(str(s["order_id"]), float(s["lat"]), float(s["lon"]),
                           float(s.get("service_s", 0)), s.get("deadline_s"))
                   for s in data["stops"]]
            return {"mode": "osrm", "origin": origin, "geo_stops": geo, **common}
    except OptimizerInputError:
        raise
    except (KeyError, TypeError, ValueError) as exc:
        raise OptimizerInputError(
            f"File đầu vào không hợp lệ: {type(exc).__name__}: {exc}") from exc
    raise OptimizerInputError("Cần time_matrix hoặc depot kèm toạ độ các điểm")


def demo_data():
    """5 điểm giao; ma trận mẫu (giây, mét). Nút 0 = kho."""
    t = [[0, 600, 900, 1200, 1500, 1800],
         [600, 0, 300, 600, 900, 1200],
         [900, 300, 0, 300, 600, 900],
         [1200, 600, 300, 0, 300, 600],
         [1500, 900, 600, 300, 0, 300],
         [1800, 1200, 900, 600, 300, 0]]
    d = [[int(x * 8 / 6) for x in row] for row in t]
    stops = [Stop("D1", 5, 300, 3600), Stop("D2", 2, 300, 2400), Stop("D3", 4, 300, 4200),
             Stop("D4", 1, 300, 1200), Stop("D5", 3, 300, 3000)]
    return t, stops, d


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Tối ưu thứ tự giao hàng 1 xe, 1 kho, 3-5 điểm")
    ap.add_argument("--input", help="file JSON đầu vào; bỏ trống để chạy dữ liệu mẫu")
    ap.add_argument("--out", help="ghi kết quả (JSON) ra file")
    ap.add_argument("--start-clock", help="giờ xuất phát HH:MM để hiển thị giờ trong ngày")
    ap.add_argument("--osrm-url", help="endpoint OSRM (hoặc biến môi trường OSRM_BASE_URL)")
    ap.add_argument("--osrm-max-attempts", type=int,
                    help="số lần thử tối đa (hoặc OSRM_MAX_ATTEMPTS)")
    ap.add_argument("--osrm-timeout", type=float,
                    help="timeout mỗi lần gọi, giây (hoặc OSRM_TIMEOUT_S)")
    args = ap.parse_args(argv)

    try:
        results: List[OptimizationResult] = []
        if args.input:
            data = load_input(args.input)
            clock = args.start_clock or data["start_clock"]
            if data["mode"] == "osrm":
                config = OsrmConfig.from_sources(base_url=args.osrm_url,
                                                 max_attempts=args.osrm_max_attempts,
                                                 timeout_s=args.osrm_timeout)
                results.append(optimize_with_osrm(
                    OsrmClient(config), data["origin"], data["geo_stops"],
                    data["initial_order"], data["return_to_depot"], data["cost"],
                    data["start_time_s"]))
            else:
                results.append(optimize(
                    data["time_matrix"], data["stops"], data["distance_matrix"],
                    data["initial_order"], data["return_to_depot"], MAX_STOPS,
                    data["cost"], data["start_time_s"]))
        else:
            t, stops, d = demo_data()
            clock = args.start_clock
            results.append(optimize(t, stops, d))
            demo_cost = CostConfig(late_weight=1.0, time_weight=0.1,
                                   risk_margin=0.1, min_improvement=60.0)
            results.append(optimize(t, stops, d, cost=demo_cost))
            print("Dữ liệu mẫu: tham số chi phí dưới đây chỉ để minh hoạ, chưa được duyệt.\n")
        for r in results:
            print(format_report(r, clock))
            print()
        if args.out:
            payload = (result_to_dict(results[0]) if len(results) == 1
                       else [result_to_dict(r) for r in results])
            with open(args.out, "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False, indent=2)
            print(f"Đã ghi kết quả JSON: {args.out}")
    except (OptimizerInputError, NoFeasibleOrderError, OsrmError, OSError, json.JSONDecodeError) as exc:
        print(f"LỖI: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())

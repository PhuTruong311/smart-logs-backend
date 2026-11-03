import contextlib
import io
import itertools
import json
import os
import tempfile
import unittest
from unittest import mock

import osrm_test_server
from app.services.multistop_optimizer import (
    CostConfig, GeoStop, NoFeasibleOrderError,
    OBJECTIVE_LEXICOGRAPHIC, OBJECTIVE_SOFT_COST,
    OptimizerInputError, Stop, evaluate_order, fmt_time,
    format_replan_report, format_report, main,
    matrices_from_osrm, optimize, optimize_with_osrm, replan
)
from app.services.osrm_client import OsrmClient, OsrmConfig, OsrmUnavailableError
from osrm_test_server import FakeOsrmServer
INF = float("inf")


def line_matrix(positions, unit=60):
    xs = [0] + list(positions)
    t = [[abs(a - b) * unit for b in xs] for a in xs]
    d = [[abs(a - b) * 1000 for b in xs] for a in xs]
    return t, d


def cfg(late=1.0, time=0.01, margin=0.0, improve=0.0):
    return CostConfig(late_weight=late, time_weight=time, risk_margin=margin,
                      min_improvement=improve)


class TestMultistop(unittest.TestCase):
    def test_3_diem_thu_tu_toi_uu(self):
        t, d = line_matrix([3, 1, 2])
        stops = [Stop("A", 1), Stop("B", 2), Stop("C", 3)]
        res = optimize(t, stops, d)
        self.assertEqual(res.optimal.sequence, ["B", "C", "A"])
        self.assertEqual(res.optimal.total_time_s, 180)
        self.assertEqual(res.initial.total_time_s, 3 * 60 + 2 * 60 + 1 * 60)
        self.assertEqual(res.time_saved_s, 180)
        self.assertEqual(res.distance_saved_m, 3000)
        self.assertTrue(res.changed)
        self.assertEqual(res.evaluated, 6)
        self.assertEqual(res.objective, OBJECTIVE_LEXICOGRAPHIC)

    def test_4_diem(self):
        t, d = line_matrix([4, 2, 1, 3])
        stops = [Stop("A", 1), Stop("B", 2), Stop("C", 3), Stop("D", 4)]
        res = optimize(t, stops, d)
        self.assertEqual(res.optimal.sequence, ["C", "B", "D", "A"])
        self.assertEqual(res.optimal.total_time_s, 240)
        self.assertEqual(res.evaluated, 24)

    def test_5_diem_khop_vet_can_doc_lap(self):
        t, d = line_matrix([5, 1, 4, 2, 3])
        stops = [Stop(c, i + 1, service_s=30) for i, c in enumerate("ABCDE")]
        res = optimize(t, stops, d)
        self.assertEqual(res.optimal.sequence, ["B", "D", "E", "C", "A"])
        self.assertEqual(res.evaluated, 120)
        expected = min(evaluate_order(p, t, d).total_time_s
                       for p in itertools.permutations(stops))
        self.assertEqual(res.optimal.total_time_s, expected)
        self.assertEqual(res.optimal.total_time_s, 5 * 60 + 5 * 30)

    def test_thu_tu_ban_dau_da_toi_uu(self):
        t, d = line_matrix([1, 2, 3])
        stops = [Stop("A", 1), Stop("B", 2), Stop("C", 3)]
        res = optimize(t, stops, d)
        self.assertFalse(res.changed)
        self.assertEqual(res.time_saved_s, 0)

    def test_initial_order_tuy_chon(self):
        t, d = line_matrix([1, 2, 3])
        stops = [Stop("A", 1), Stop("B", 2), Stop("C", 3)]
        res = optimize(t, stops, d, initial_order=["C", "B", "A"])
        self.assertEqual(res.initial.sequence, ["C", "B", "A"])
        self.assertEqual(res.optimal.sequence, ["A", "B", "C"])

    def test_eta_cong_thoi_gian_dung(self):
        t, d = line_matrix([1, 2, 3])
        stops = [Stop("A", 1, service_s=100), Stop("B", 2, service_s=50),
                 Stop("C", 3, service_s=10)]
        p = evaluate_order(stops, t, d)
        self.assertEqual([s.arrival_s for s in p.stops], [60, 220, 330])
        self.assertEqual([s.complete_s for s in p.stops], [160, 270, 340])
        self.assertEqual(p.total_time_s, 340)

    def test_return_to_depot(self):
        t, d = line_matrix([1, 2])
        stops = [Stop("A", 1), Stop("B", 2)]
        p = evaluate_order(stops, t, d, return_to_depot=True)
        self.assertEqual(p.total_time_s, 240)
        self.assertEqual(p.total_distance_m, 4000)

    def test_deadline_buoc_doi_thu_tu(self):
        t, d = line_matrix([3, 1])
        stops = [Stop("A", 1, deadline_s=400), Stop("B", 2, deadline_s=100)]
        res = optimize(t, stops, d)
        self.assertEqual(res.initial.late_orders, ["B"])
        self.assertEqual(res.optimal.sequence, ["B", "A"])
        self.assertEqual(res.optimal.late_count, 0)
        self.assertTrue(res.deadline_feasible)
        self.assertEqual(res.late_reduced, 1)

    def test_deadline_khong_kha_thi(self):
        t, d = line_matrix([1, 2])
        stops = [Stop("A", 1, deadline_s=10), Stop("B", 2, deadline_s=10)]
        res = optimize(t, stops, d)
        self.assertFalse(res.deadline_feasible)
        self.assertEqual(res.optimal.late_count, 2)
        self.assertTrue(res.notes)
        self.assertEqual(sorted(res.optimal.late_orders), ["A", "B"])
        self.assertEqual(res.optimal.sequence, ["A", "B"])
        self.assertAlmostEqual(res.optimal.total_lateness_s, 50 + 110)

    def test_it_don_tre_uu_tien_hon_thoi_gian(self):
        t = [[0, 10, 50],
             [10, 0, 100],
             [50, 100, 0]]
        stops = [Stop("A", 1, service_s=0, deadline_s=None),
                 Stop("B", 2, service_s=0, deadline_s=60)]
        res = optimize(t, stops)
        self.assertEqual(res.optimal.sequence, ["B", "A"])
        self.assertEqual(res.optimal.late_count, 0)
        self.assertEqual(res.optimal.total_time_s, 150)

    def test_duong_khong_di_duoc_bi_tranh(self):
        t, d = line_matrix([1, 2, 3])
        t[1][2] = t[2][1] = INF
        stops = [Stop("A", 1), Stop("B", 2), Stop("C", 3)]
        res = optimize(t, stops, d)
        for a, b in zip(res.optimal.sequence, res.optimal.sequence[1:]):
            self.assertNotEqual({a, b}, {"A", "B"})
        self.assertGreater(res.unreachable_orders, 0)

    def test_thu_tu_ban_dau_khong_di_duoc(self):
        t, d = line_matrix([1, 2, 3])
        t[1][2] = None
        stops = [Stop("A", 1), Stop("B", 2), Stop("C", 3)]
        res = optimize(t, stops, d, initial_order=["A", "B", "C"])
        self.assertIsNone(res.initial)
        self.assertIsNone(res.time_saved_s)
        self.assertTrue(any("không đi được" in n for n in res.notes))
        self.assertIn("không tính được", format_report(res))

    def test_tat_ca_hoan_vi_bi_chan(self):
        t, d = line_matrix([1, 2, 3])
        for j in range(1, 4):
            t[0][j] = INF
        stops = [Stop("A", 1), Stop("B", 2), Stop("C", 3)]
        with self.assertRaises(NoFeasibleOrderError):
            optimize(t, stops, d)

    def test_thieu_khoang_cach_tren_doan_da_co_thoi_gian(self):
        t, d = line_matrix([1, 2, 3])
        d[0][1] = None
        res = optimize(t, [Stop("A", 1), Stop("B", 2), Stop("C", 3)], d)
        self.assertNotEqual(res.optimal.sequence[0], "A")

    def test_khong_co_ma_tran_khoang_cach(self):
        t, _ = line_matrix([1, 2, 3])
        res = optimize(t, [Stop("A", 1), Stop("B", 2), Stop("C", 3)])
        self.assertIsNone(res.optimal.total_distance_m)
        self.assertIn("n/a", format_report(res))

    def test_so_diem_ngoai_pham_vi(self):
        t, d = line_matrix(range(1, 8))
        with self.assertRaises(OptimizerInputError):
            optimize(t, [Stop(str(i), i) for i in range(1, 7)], d)
        with self.assertRaises(OptimizerInputError):
            optimize(t, [], d)

    def test_dau_vao_sai(self):
        t, d = line_matrix([1, 2, 3])
        with self.assertRaises(OptimizerInputError):
            optimize(t, [Stop("A", 1), Stop("A", 2)], d)
        with self.assertRaises(OptimizerInputError):
            optimize(t, [Stop("A", 0), Stop("B", 2)], d)
        with self.assertRaises(OptimizerInputError):
            optimize(t, [Stop("A", 1), Stop("B", 9)], d)
        with self.assertRaises(OptimizerInputError):
            optimize(t, [Stop("A", 1), Stop("B", 2)], d, initial_order=["A", "Z"])
        with self.assertRaises(OptimizerInputError):
            optimize(t[:-1], [Stop("A", 1), Stop("B", 2)], d)
        with self.assertRaises(OptimizerInputError):
            optimize(t, [Stop("A", 1), Stop("B", 2)], d, start_time_s=-1)
        with self.assertRaises(OptimizerInputError):
            optimize(t, [Stop("A", 1), Stop("B", 2)], d, cost="x")

    def test_bao_cao(self):
        t, d = line_matrix([3, 1])
        stops = [Stop("A", 1, 60, 400), Stop("B", 2, 60, 100)]
        res = optimize(t, stops, d)
        rpt = format_report(res, start_clock="08:00")
        self.assertIn("Thứ tự ban đầu", rpt)
        self.assertIn("Thứ tự tối ưu", rpt)
        self.assertIn("08:", rpt)
        self.assertIn("TRỄ", rpt)
        self.assertEqual(fmt_time(3661), "01:01:01")
        self.assertEqual(fmt_time(0, "08:30"), "08:30:00")


class TestCostConfig(unittest.TestCase):
    def test_tham_so_khong_hop_le(self):
        for kwargs in (dict(late_weight=-1), dict(time_weight=float("nan")),
                       dict(risk_margin=-0.1), dict(min_improvement=float("inf")),
                       dict(late_weight=0.0, time_weight=0.0)):
            base = dict(late_weight=1.0, time_weight=0.1, risk_margin=0.0, min_improvement=0.0)
            base.update(kwargs)
            with self.assertRaises(OptimizerInputError, msg=str(kwargs)):
                CostConfig(**base)

    def test_from_dict_thieu_khoa(self):
        with self.assertRaises(OptimizerInputError) as ctx:
            CostConfig.from_dict({"late_weight": 1})
        self.assertIn("time_weight", str(ctx.exception))

    def test_from_dict_day_du(self):
        c = CostConfig.from_dict({"late_weight": 1, "time_weight": 0.5,
                                  "risk_margin": 0.1, "min_improvement": 30})
        self.assertEqual((c.late_weight, c.min_improvement), (1.0, 30.0))


class TestSoftCost(unittest.TestCase):
    def setUp(self):
        self.t = [[0, 50, 100],
                  [50, 0, 2000],
                  [100, 100, 0]]
        self.stops = [Stop("A", 1, 0, 60), Stop("B", 2, 0, 90)]

    def test_che_do_cu_hy_sinh_mot_don_tre_rat_lau(self):
        res = optimize(self.t, self.stops)
        self.assertEqual(res.optimal.sequence, ["A", "B"])
        self.assertEqual(res.optimal.late_count, 1)
        self.assertAlmostEqual(res.optimal.total_lateness_s, 1960)

    def test_chi_phi_mem_tranh_tre_qua_lau(self):
        res = optimize(self.t, self.stops, cost=cfg())
        self.assertEqual(res.objective, OBJECTIVE_SOFT_COST)
        self.assertEqual(res.optimal.sequence, ["B", "A"])
        self.assertEqual(res.optimal.late_count, 2)
        self.assertAlmostEqual(res.optimal.total_lateness_s, 150)
        self.assertAlmostEqual(res.optimal.cost, 150 + 0.01 * 200)
        self.assertAlmostEqual(res.initial.cost, 1960 + 0.01 * 2050)
        self.assertAlmostEqual(res.cost_saved, (1960 + 20.5) - (150 + 2))

    def test_trong_so_thoi_gian_lon_doi_ket_qua(self):
        t = [[0, 10, 10],
             [10, 0, 10],
             [10, 500, 0]]
        stops = [Stop("A", 1, 0, 1000), Stop("B", 2, 0, 15)]
        fast = optimize(t, stops, cost=cfg(late=0.001, time=1.0))
        safe = optimize(t, stops, cost=cfg(late=1.0, time=0.01))
        self.assertEqual(fast.optimal.sequence, ["A", "B"])
        self.assertEqual(fast.optimal.late_orders, ["B"])
        self.assertEqual(safe.optimal.sequence, ["B", "A"])
        self.assertEqual(safe.optimal.late_orders, [])

    def test_bien_rui_ro_danh_gia_tren_eta_bi_quan(self):
        t = [[0, 100], [100, 0]]
        p = evaluate_order([Stop("X", 1, 0, 120)], t, risk_margin=0.5)
        s = p.stops[0]
        self.assertFalse(s.late)
        self.assertTrue(s.at_risk)
        self.assertAlmostEqual(s.complete_s, 100)
        self.assertAlmostEqual(s.risk_complete_s, 150)
        self.assertAlmostEqual(s.risk_lateness_s, 30)
        self.assertEqual(p.at_risk_orders, ["X"])
        self.assertEqual(p.late_orders, [])

    def test_bien_rui_ro_khong_nhan_voi_thoi_gian_dung(self):
        t = [[0, 100], [100, 0]]
        p = evaluate_order([Stop("X", 1, 20, 120)], t, risk_margin=0.5)
        self.assertAlmostEqual(p.stops[0].complete_s, 120)
        self.assertFalse(p.stops[0].late)
        self.assertAlmostEqual(p.stops[0].risk_complete_s, 170)
        self.assertAlmostEqual(p.stops[0].risk_lateness_s, 50)

    def test_bien_rui_ro_doi_thu_tu_chua_du_phong(self):
        t = [[0, 100, 100],
             [100, 0, 10],
             [100, 10, 0]]
        stops = [Stop("X", 1, 0, 125), Stop("Y", 2, 0, 115)]
        no_margin = optimize(t, stops, cost=cfg(margin=0.0))
        with_margin = optimize(t, stops, cost=cfg(margin=0.2))
        self.assertEqual(no_margin.optimal.sequence, ["X", "Y"])
        self.assertEqual(with_margin.optimal.sequence, ["Y", "X"])
        self.assertEqual(with_margin.optimal.late_count, 0)
        self.assertEqual(with_margin.optimal.risk_late_count, 2)
        self.assertAlmostEqual(with_margin.optimal.total_risk_lateness_s, 12)
        self.assertFalse(with_margin.risk_feasible)
        self.assertTrue(with_margin.deadline_feasible)
        self.assertTrue(any("nguy cơ trễ" in n for n in with_margin.notes))

    def test_bao_cao_hien_thi_eta_bi_quan(self):
        res = optimize(self.t, self.stops, cost=cfg(margin=0.2))
        rpt = format_report(res, "08:00")
        self.assertIn("ETA bi quan", rpt)
        self.assertIn("hàm chi phí mềm", rpt)
        self.assertIn("risk_margin=0.2", rpt)
        self.assertIn("Chi phí", rpt)

    def test_hoa_chi_phi_thi_xet_thoi_gian_roi_thu_tu(self):
        t, d = line_matrix([1, 2, 3])
        stops = [Stop("B", 2), Stop("A", 1), Stop("C", 3)]
        res = optimize(t, stops, d, cost=cfg())
        self.assertEqual(res.optimal.sequence, ["A", "B", "C"])

    def test_tinh_nhat_quan_voi_vet_can_doc_lap(self):
        t, d = line_matrix([5, 1, 4, 2, 3])
        stops = [Stop(c, i + 1, 30, 400 + 60 * i) for i, c in enumerate("ABCDE")]
        c = cfg(margin=0.1)
        res = optimize(t, stops, d, cost=c)
        costs = []
        for perm in itertools.permutations(stops):
            p = evaluate_order(perm, t, d, False, 0.0, c.risk_margin)
            costs.append(c.late_weight * p.total_risk_lateness_s + c.time_weight * p.total_time_s)
        self.assertAlmostEqual(res.optimal.cost, min(costs))

    def test_start_time_tuyet_doi(self):
        t, d = line_matrix([1, 2])
        stops = [Stop("A", 1, 0, 150), Stop("B", 2, 0, 400)]
        p = evaluate_order(stops, t, d, start_time_s=100)
        self.assertEqual([s.arrival_s for s in p.stops], [160, 220])
        self.assertTrue(p.stops[0].late)
        self.assertAlmostEqual(p.stops[0].lateness_s, 10)
        self.assertEqual(p.total_time_s, 120)


class TestReplan(unittest.TestCase):
    def setUp(self):
        self.t = [[0, 900, 100],
                  [900, 0, 500],
                  [100, 100, 0]]
        self.stops = [Stop("A", 1, 0, 1100), Stop("B", 2, 0, 200)]

    def test_ket_xe_doi_thu_tu_khi_cai_thien_du_lon(self):
        r = replan(self.t, self.stops, ["A", "B"], cfg(improve=60))
        self.assertTrue(r.adopted)
        self.assertEqual(r.chosen.sequence, ["B", "A"])
        self.assertAlmostEqual(r.current.cost, 1200 + 0.01 * 1400)
        self.assertAlmostEqual(r.candidate.cost, 0.01 * 200)
        self.assertGreater(r.improvement, 1200)
        self.assertEqual(r.chosen.late_orders, [])

    def test_giu_ke_hoach_khi_cai_thien_nho_hon_nguong(self):
        r = replan(self.t, self.stops, ["A", "B"], cfg(improve=5000))
        self.assertFalse(r.adopted)
        self.assertEqual(r.chosen.sequence, ["A", "B"])
        self.assertEqual(r.candidate.sequence, ["B", "A"])
        self.assertIn("giữ kế hoạch hiện tại", r.reason)

    def test_ke_hoach_hien_tai_da_toi_uu_thi_giu(self):
        r = replan(self.t, self.stops, ["B", "A"], cfg(improve=0))
        self.assertFalse(r.adopted)
        self.assertEqual(r.chosen.sequence, ["B", "A"])
        self.assertAlmostEqual(r.improvement, 0)

    def test_ke_hoach_hien_tai_khong_di_duoc_thi_bat_buoc_doi(self):
        t = [row[:] for row in self.t]
        t[1][2] = None
        r = replan(t, self.stops, ["A", "B"], cfg(improve=1e9))
        self.assertTrue(r.adopted)
        self.assertIsNone(r.current)
        self.assertIsNone(r.improvement)
        self.assertEqual(r.chosen.sequence, ["B", "A"])
        self.assertIn("bắt buộc", r.reason)

    def test_su_co_hong_xe_cong_them_tre(self):
        r = replan(self.t, self.stops, ["B", "A"], cfg(), now_s=100, delay_s=300)
        self.assertEqual(r.chosen.sequence, ["B", "A"])
        self.assertAlmostEqual(r.chosen.stops[0].arrival_s, 500)
        self.assertEqual(r.chosen.late_orders, ["B"])
        self.assertAlmostEqual(r.chosen.stops[0].lateness_s, 300)
        self.assertEqual(r.chosen.start_time_s, 400)
        self.assertAlmostEqual(r.chosen.total_time_s, 200)

    def test_don_da_qua_han_la_tre_chac_chan(self):
        r = replan(self.t, self.stops, ["B", "A"], cfg(), now_s=300)
        self.assertIn("B", r.chosen.late_orders)
        self.assertGreater(r.chosen.stops[0].lateness_s, 0)

    def test_khong_con_duong_nao(self):
        t = [row[:] for row in self.t]
        t[0][1] = t[0][2] = None
        with self.assertRaises(NoFeasibleOrderError):
            replan(t, self.stops, ["A", "B"], cfg())

    def test_dau_vao_sai(self):
        with self.assertRaises(OptimizerInputError):
            replan(self.t, self.stops, ["A", "Z"], cfg())
        with self.assertRaises(OptimizerInputError):
            replan(self.t, self.stops, ["A", "B"], None)
        with self.assertRaises(OptimizerInputError):
            replan(self.t, self.stops, ["A", "B"], cfg(), now_s=-5)
        with self.assertRaises(OptimizerInputError):
            replan(self.t, self.stops, ["A", "B"], cfg(), delay_s=float("nan"))

    def test_bao_cao_replan(self):
        r = replan(self.t, self.stops, ["A", "B"], cfg(improve=60))
        rpt = format_replan_report(r, "08:00")
        self.assertIn("ĐỔI thứ tự", rpt)
        self.assertIn("Kế hoạch hiện tại", rpt)
        keep = format_replan_report(replan(self.t, self.stops, ["A", "B"], cfg(improve=5000)))
        self.assertIn("GIỮ kế hoạch hiện tại", keep)


def geo_stops():
    return [GeoStop("P3", 10.0, 106.03, 0, None),
            GeoStop("P1", 10.0, 106.01, 0, None),
            GeoStop("P2", 10.0, 106.02, 0, None)]


def osrm_client(server, attempts=2):
    return OsrmClient(OsrmConfig(server.base_url, timeout_s=2.0, max_attempts=attempts,
                                 backoff_s=0.0))


class TestOptimizeWithOsrm(unittest.TestCase):
    ORIGIN = (10.0, 106.0)

    def test_ma_tran_tu_table_api(self):
        with FakeOsrmServer() as server:
            t, d = matrices_from_osrm(osrm_client(server), self.ORIGIN, geo_stops())
        self.assertEqual(len(t), 4)
        self.assertAlmostEqual(t[0][1], 333.0, delta=0.5)
        self.assertAlmostEqual(t[0][2], 111.0, delta=0.5)
        self.assertAlmostEqual(d[0][2], 1110.0, delta=0.5)
        self.assertEqual(server.hits, 1)

    def test_toi_uu_voi_osrm(self):
        with FakeOsrmServer() as server:
            res = optimize_with_osrm(osrm_client(server), self.ORIGIN, geo_stops())
            self.assertEqual(server.hits, 1)
        self.assertEqual(res.optimal.sequence, ["P1", "P2", "P3"])
        self.assertAlmostEqual(res.optimal.total_time_s, 333.0, delta=0.5)
        self.assertEqual(res.initial.sequence, ["P3", "P1", "P2"])
        self.assertAlmostEqual(res.initial.total_time_s, 666.0, delta=0.5)
        self.assertAlmostEqual(res.time_saved_s, 333.0, delta=0.5)
        self.assertAlmostEqual(res.distance_saved_m, 3330.0, delta=1.0)

    def test_o_osrm_null_la_khong_di_duoc(self):
        with FakeOsrmServer() as server:
            server.blocked = {(2, 3)}
            res = optimize_with_osrm(osrm_client(server), self.ORIGIN, geo_stops())
        self.assertEqual(res.optimal.sequence, ["P1", "P3", "P2"])
        self.assertEqual(res.unreachable_orders, 2)
        self.assertIsNone(res.initial)

    def test_osrm_loi_nem_ra_khong_che_giau(self):
        with FakeOsrmServer() as server:
            server.mode = "http500"
            client = osrm_client(server, attempts=2)
            with self.assertRaises(OsrmUnavailableError):
                optimize_with_osrm(client, self.ORIGIN, geo_stops())
            self.assertEqual(client.calls, 2)

    def test_kiem_tra_dau_vao_truoc_khi_goi_api(self):
        with FakeOsrmServer() as server:
            client = osrm_client(server)
            six = [GeoStop(f"S{i}", 10.0, 106.0 + i / 100) for i in range(6)]
            with self.assertRaises(OptimizerInputError):
                optimize_with_osrm(client, self.ORIGIN, six)
            with self.assertRaises(OptimizerInputError):
                optimize_with_osrm(client, self.ORIGIN, [])
            with self.assertRaises(OptimizerInputError):
                optimize_with_osrm(client, self.ORIGIN,
                                   [GeoStop("A", 10.0, 106.01), GeoStop("A", 10.0, 106.02)])
            with self.assertRaises(OptimizerInputError):
                optimize_with_osrm(client, self.ORIGIN, [GeoStop("A", 10.0, 106.01, -1)])
            with self.assertRaises(OptimizerInputError):
                optimize_with_osrm(client, self.ORIGIN, [GeoStop("A", 10.0, 106.01, 0, -5)])
            self.assertEqual(server.hits, 0)

    def test_osrm_voi_chi_phi_mem(self):
        stops = [GeoStop("P3", 10.0, 106.03, 0, 200),
                 GeoStop("P1", 10.0, 106.01, 0, 150),
                 GeoStop("P2", 10.0, 106.02, 0, 400)]
        with FakeOsrmServer() as server:
            res = optimize_with_osrm(osrm_client(server), self.ORIGIN, stops, cost=cfg())
        self.assertEqual(res.objective, OBJECTIVE_SOFT_COST)
        self.assertIsNotNone(res.optimal.cost)


class TestCli(unittest.TestCase):
    def run_main(self, argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = main(argv)
        return code, out.getvalue(), err.getvalue()

    def write_json(self, data):
        fd, path = tempfile.mkstemp(suffix=".json")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f)
        self.addCleanup(os.remove, path)
        return path

    def test_demo(self):
        code, out, err = self.run_main([])
        self.assertEqual(code, 0, err)
        self.assertIn("Thứ tự tối ưu", out)
        self.assertIn("chưa được duyệt", out)
        self.assertIn("hàm chi phí mềm", out)

    def test_input_ma_tran_co_chi_phi_va_ghi_json(self):
        t = [[0, 50, 100], [50, 0, 2000], [100, 100, 0]]
        path = self.write_json({
            "time_matrix": t,
            "stops": [{"order_id": "A", "node": 1, "deadline_s": 60},
                      {"order_id": "B", "node": 2, "deadline_s": 90}],
            "cost": {"late_weight": 1, "time_weight": 0.01, "risk_margin": 0, "min_improvement": 0},
            "start_clock": "08:00",
        })
        fd, out_path = tempfile.mkstemp(suffix=".json")
        os.close(fd)
        self.addCleanup(os.remove, out_path)
        code, out, err = self.run_main(["--input", path, "--out", out_path])
        self.assertEqual(code, 0, err)
        self.assertIn("08:", out)
        with open(out_path, encoding="utf-8") as f:
            data = json.load(f)
        self.assertEqual(data["optimal"]["sequence"], ["B", "A"])
        self.assertEqual(data["objective"], OBJECTIVE_SOFT_COST)
        self.assertIn("late_orders", data["optimal"])

    def test_input_osrm_voi_tham_so_dong_lenh(self):
        path = self.write_json({
            "depot": {"lat": 10.0, "lon": 106.0},
            "stops": [{"order_id": "P3", "lat": 10.0, "lon": 106.03},
                      {"order_id": "P1", "lat": 10.0, "lon": 106.01},
                      {"order_id": "P2", "lat": 10.0, "lon": 106.02}],
        })
        with FakeOsrmServer() as server:
            code, out, err = self.run_main(["--input", path, "--osrm-url", server.base_url,
                                            "--osrm-max-attempts", "2", "--osrm-timeout", "2"])
        self.assertEqual(code, 0, err)
        self.assertIn("kho -> P1 -> P2 -> P3", out)

    def test_thieu_cau_hinh_osrm_bao_loi_ro_rang(self):
        path = self.write_json({
            "depot": {"lat": 10.0, "lon": 106.0},
            "stops": [{"order_id": "P1", "lat": 10.0, "lon": 106.01}],
        })
        with mock.patch.dict(os.environ, {}, clear=False):
            for key in ("OSRM_BASE_URL", "OSRM_MAX_ATTEMPTS", "OSRM_TIMEOUT_S"):
                os.environ.pop(key, None)
            code, out, err = self.run_main(["--input", path])
        self.assertEqual(code, 2)
        self.assertIn("Thiếu cấu hình OSRM", err)

    def test_osrm_loi_tra_ma_loi(self):
        path = self.write_json({
            "depot": {"lat": 10.0, "lon": 106.0},
            "stops": [{"order_id": "P1", "lat": 10.0, "lon": 106.01}],
        })
        with FakeOsrmServer() as server:
            server.mode = "http500"
            code, out, err = self.run_main(["--input", path, "--osrm-url", server.base_url,
                                            "--osrm-max-attempts", "2", "--osrm-timeout", "1"])
        self.assertEqual(code, 2)
        self.assertIn("OSRM không khả dụng", err)

    def test_file_dau_vao_sai(self):
        for data in ({"stops": []}, {"time_matrix": [[0]], "stops": [{"node": 1}]},
                     {"time_matrix": [[0, 1], [1, 0]], "stops": [], "cost": {"late_weight": 1}}):
            code, out, err = self.run_main(["--input", self.write_json(data)])
            self.assertEqual(code, 2, msg=str(data))
            self.assertIn("LỖI", err)

    def test_file_khong_ton_tai(self):
        code, out, err = self.run_main(["--input", "/khong/ton/tai.json"])
        self.assertEqual(code, 2)


if __name__ == "__main__":
    unittest.main()

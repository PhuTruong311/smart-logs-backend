import math
import unittest

import osrm_test_server
from app.services.osrm_client import (
    OsrmClient, OsrmConfig, OsrmNoRouteError, OsrmUnavailableError,
    RouteResult
)
from osrm_test_server import FakeOsrmServer
from app.services.route_selector import (
    NoValidRouteError, RouteCandidate, SOURCE_ML, SOURCE_OSRM,
    fill_osrm_fallback, needs_osrm_fallback, resolve_eta,
    select_route, select_route_with_fallback
)
A = (10.0, 106.0)
B = (10.1, 106.0)


class StubOsrm:
    def __init__(self, results=None, error=None):
        self.results = results or {}
        self.error = error
        self.calls = []

    def route(self, origin, destination):
        self.calls.append((origin, destination))
        if self.error is not None:
            raise self.error
        return self.results[(origin, destination)]


class TestRouteSelector(unittest.TestCase):
    def test_chon_eta_ml_thap_nhat(self):
        res = select_route([
            RouteCandidate("A", eta_ml=900, distance_m=5000),
            RouteCandidate("B", eta_ml=700, distance_m=7000),
            RouteCandidate("C", eta_ml=800, distance_m=4000),
        ])
        self.assertEqual(res.best.route_id, "B")
        self.assertEqual([r.route_id for r in res.ranking], ["B", "C", "A"])
        self.assertFalse(res.tie_broken)

    def test_uu_tien_ml_du_osrm_thap_hon(self):
        res = select_route([
            RouteCandidate("A", eta_ml=1000, eta_osrm=100),
            RouteCandidate("B", eta_ml=900, eta_osrm=2000),
        ])
        self.assertEqual(res.best.route_id, "B")
        self.assertEqual(res.best.source, SOURCE_ML)

    def test_fallback_osrm_khi_thieu_ml(self):
        res = select_route([
            RouteCandidate("A", eta_ml=None, eta_osrm=600),
            RouteCandidate("B", eta_ml=800, eta_osrm=300),
        ])
        self.assertEqual(res.best.route_id, "A")
        self.assertEqual(res.best.source, SOURCE_OSRM)
        self.assertTrue(any("fallback" in w for w in res.warnings))
        self.assertTrue(any("nguồn khác nhau" in w for w in res.warnings))

    def test_eta_ml_khong_hop_le_dung_fallback(self):
        for bad in (math.nan, -5, math.inf, "abc", True):
            eta, src = resolve_eta(RouteCandidate("X", eta_ml=bad, eta_osrm=500))
            self.assertEqual((eta, src), (500.0, SOURCE_OSRM), msg=str(bad))

    def test_eta_bang_nhau_chon_tuyen_ngan_hon(self):
        res = select_route([
            RouteCandidate("A", eta_ml=600, distance_m=8000),
            RouteCandidate("B", eta_ml=600, distance_m=6000),
        ])
        self.assertEqual(res.best.route_id, "B")
        self.assertTrue(res.tie_broken)

    def test_eta_va_khoang_cach_bang_nhau_theo_route_id(self):
        a = select_route([RouteCandidate("B", eta_ml=600, distance_m=6000),
                          RouteCandidate("A", eta_ml=600, distance_m=6000)])
        b = select_route([RouteCandidate("A", eta_ml=600, distance_m=6000),
                          RouteCandidate("B", eta_ml=600, distance_m=6000)])
        self.assertEqual(a.best.route_id, "A")
        self.assertEqual(b.best.route_id, "A")

    def test_thieu_khoang_cach_khi_hoa_xep_sau(self):
        res = select_route([RouteCandidate("A", eta_ml=600),
                            RouteCandidate("B", eta_ml=600, distance_m=9000)])
        self.assertEqual(res.best.route_id, "B")

    def test_loai_tuyen_thieu_eta_va_khong_di_duoc(self):
        res = select_route([
            RouteCandidate("A"),
            RouteCandidate("B", eta_ml=100, drivable=False),
            RouteCandidate("C", eta_ml=500),
        ])
        self.assertEqual(res.best.route_id, "C")
        reasons = dict(res.excluded)
        self.assertIn("thiếu ETA", reasons["A"])
        self.assertIn("không đi được", reasons["B"])

    def test_khong_co_tuyen_hop_le(self):
        with self.assertRaises(NoValidRouteError):
            select_route([RouteCandidate("A"),
                          RouteCandidate("B", eta_ml=100, drivable=False)])

    def test_danh_sach_rong(self):
        with self.assertRaises(NoValidRouteError):
            select_route([])

    def test_route_id_trung_lap_bi_loai(self):
        res = select_route([RouteCandidate("A", eta_ml=500),
                            RouteCandidate("A", eta_ml=100)])
        self.assertEqual(res.best.eta_s, 500)
        self.assertEqual(len(res.excluded), 1)

    def test_from_dict(self):
        c = RouteCandidate.from_dict({"route_id": 7, "eta_ml": 300, "drivable": False})
        self.assertEqual((c.route_id, c.eta_ml, c.drivable), ("7", 300, False))

    def test_from_dict_toa_do(self):
        c = RouteCandidate.from_dict({"route_id": "R", "origin": [10.0, 106.0],
                                      "destination": [10.1, 106.0]})
        self.assertEqual((c.origin, c.destination), (A, B))
        with self.assertRaises(ValueError):
            RouteCandidate.from_dict({"route_id": "R", "origin": [1, 2, 3]})


class TestFallbackWithStub(unittest.TestCase):
    def test_khong_goi_osrm_khi_da_co_eta(self):
        stub = StubOsrm()
        cands = [RouteCandidate("A", eta_ml=500, origin=A, destination=B),
                 RouteCandidate("B", eta_osrm=400, origin=A, destination=B),
                 RouteCandidate("C", eta_ml=100, drivable=False, origin=A, destination=B)]
        res = select_route_with_fallback(cands, stub)
        self.assertEqual(stub.calls, [])
        self.assertEqual(res.best.route_id, "B")

    def test_goi_osrm_dung_cho_tuyen_thieu_eta(self):
        stub = StubOsrm({(A, B): RouteResult(450.0, 7000.0)})
        cands = [RouteCandidate("A", eta_ml=900),
                 RouteCandidate("B", origin=A, destination=B)]
        res = select_route_with_fallback(cands, stub)
        self.assertEqual(stub.calls, [(A, B)])
        self.assertEqual(res.best.route_id, "B")
        self.assertEqual(res.best.source, SOURCE_OSRM)
        self.assertEqual(res.best.distance_m, 7000.0)
        self.assertTrue(any("fallback" in w for w in res.warnings))

    def test_giu_khoang_cach_da_co_khi_dien_fallback(self):
        stub = StubOsrm({(A, B): RouteResult(450.0, 7000.0)})
        filled, failures = fill_osrm_fallback(
            [RouteCandidate("B", distance_m=1234.0, origin=A, destination=B)], stub)
        self.assertEqual(filled[0].distance_m, 1234.0)
        self.assertEqual(filled[0].eta_osrm, 450.0)
        self.assertEqual(failures, {})

    def test_khong_toa_do_thi_khong_goi_va_bi_loai(self):
        stub = StubOsrm()
        res = select_route_with_fallback(
            [RouteCandidate("A", eta_ml=900), RouteCandidate("B")], stub)
        self.assertEqual(stub.calls, [])
        self.assertIn("thiếu ETA", dict(res.excluded)["B"])

    def test_loi_osrm_khong_bi_che_giau(self):
        stub = StubOsrm(error=OsrmUnavailableError("hết số lần thử"))
        res = select_route_with_fallback(
            [RouteCandidate("A", eta_ml=900), RouteCandidate("B", origin=A, destination=B)],
            stub)
        self.assertEqual(res.best.route_id, "A")
        self.assertIn("OSRM lỗi", dict(res.excluded)["B"])
        self.assertTrue(any("Gọi OSRM thất bại" in w for w in res.warnings))

    def test_tat_ca_loi_osrm_nem_no_valid_route(self):
        stub = StubOsrm(error=OsrmNoRouteError("không có đường"))
        with self.assertRaises(NoValidRouteError) as ctx:
            select_route_with_fallback([RouteCandidate("B", origin=A, destination=B)], stub)
        self.assertIn("OSRM lỗi", str(ctx.exception))

    def test_tuyen_khong_di_duoc_khong_goi_osrm(self):
        stub = StubOsrm()
        with self.assertRaises(NoValidRouteError):
            select_route_with_fallback(
                [RouteCandidate("B", drivable=False, origin=A, destination=B)], stub)
        self.assertEqual(stub.calls, [])

    def test_route_id_trung_chi_goi_mot_lan(self):
        stub = StubOsrm({(A, B): RouteResult(450.0, 7000.0)})
        select_route_with_fallback(
            [RouteCandidate("B", origin=A, destination=B),
             RouteCandidate("B", origin=A, destination=B)], stub)
        self.assertEqual(len(stub.calls), 1)

    def test_needs_osrm_fallback(self):
        self.assertTrue(needs_osrm_fallback(RouteCandidate("A")))
        self.assertFalse(needs_osrm_fallback(RouteCandidate("A", eta_ml=1)))
        self.assertFalse(needs_osrm_fallback(RouteCandidate("A", drivable=False)))

    def test_khong_truyen_osrm_giong_select_route(self):
        res = select_route_with_fallback([RouteCandidate("A", eta_ml=5)])
        self.assertEqual(res.best.route_id, "A")


class TestFallbackWithFakeServer(unittest.TestCase):
    def test_tich_hop_client_that_voi_server_gia(self):
        with FakeOsrmServer() as server:
            client = OsrmClient(OsrmConfig(server.base_url, timeout_s=2, max_attempts=2,
                                           backoff_s=0))
            cands = [RouteCandidate("A", eta_ml=5000),
                     RouteCandidate("B", origin=A, destination=B)]
            res = select_route_with_fallback(cands, client)
            self.assertEqual(res.best.route_id, "B")
            self.assertAlmostEqual(res.best.eta_s, 1110.0, delta=0.01)
            self.assertEqual(server.hits, 1)

    def test_server_loi_tuyen_bi_loai_co_ly_do(self):
        with FakeOsrmServer() as server:
            server.mode = "http500"
            client = OsrmClient(OsrmConfig(server.base_url, timeout_s=2, max_attempts=2,
                                           backoff_s=0))
            res = select_route_with_fallback(
                [RouteCandidate("A", eta_ml=5000),
                 RouteCandidate("B", origin=A, destination=B)], client)
            self.assertEqual(res.best.route_id, "A")
            self.assertIn("OSRM lỗi", dict(res.excluded)["B"])
            self.assertEqual(server.hits, 2)


if __name__ == "__main__":
    unittest.main()

import unittest

import osrm_test_server
from app.services.osrm_client import (
    OsrmClient, OsrmConfig, OsrmConfigError, OsrmInputError,
    OsrmNoRouteError, OsrmResponseError, OsrmUnavailableError
)
from osrm_test_server import FakeOsrmServer

A = (10.0, 106.0)
B = (10.1, 106.0)
C = (10.0, 106.1)


def make_client(server, max_attempts=3, timeout_s=2.0, sleep=None):
    config = OsrmConfig(server.base_url, timeout_s=timeout_s, max_attempts=max_attempts,
                        backoff_s=0.0)
    if sleep is None:
        return OsrmClient(config)
    return OsrmClient(config, sleep=sleep)


class TestConfig(unittest.TestCase):
    def test_cau_hinh_hop_le(self):
        cfg = OsrmConfig("http://localhost:5000", timeout_s=3, max_attempts=2)
        self.assertEqual(cfg.profile, "driving")

    def test_cau_hinh_khong_hop_le(self):
        bad = [
            dict(base_url="localhost:5000", timeout_s=1, max_attempts=1),
            dict(base_url="ftp://x", timeout_s=1, max_attempts=1),
            dict(base_url="http://x", timeout_s=0, max_attempts=1),
            dict(base_url="http://x", timeout_s=float("nan"), max_attempts=1),
            dict(base_url="http://x", timeout_s=1, max_attempts=0),
            dict(base_url="http://x", timeout_s=1, max_attempts=True),
            dict(base_url="http://x", timeout_s=1, max_attempts="3"),
            dict(base_url="http://x", timeout_s=1, max_attempts=1, backoff_s=-1),
            dict(base_url="http://x", timeout_s=1, max_attempts=1, profile="a/b"),
        ]
        for kwargs in bad:
            with self.assertRaises(OsrmConfigError, msg=str(kwargs)):
                OsrmConfig(**kwargs)

    def test_from_sources_thieu_cau_hinh(self):
        with self.assertRaises(OsrmConfigError) as ctx:
            OsrmConfig.from_sources(environ={})
        self.assertIn("max_attempts", str(ctx.exception))
        self.assertIn("base_url", str(ctx.exception))

    def test_from_sources_doc_bien_moi_truong(self):
        env = {"OSRM_BASE_URL": "http://h:1", "OSRM_MAX_ATTEMPTS": "4", "OSRM_TIMEOUT_S": "2.5"}
        cfg = OsrmConfig.from_sources(environ=env)
        self.assertEqual((cfg.base_url, cfg.max_attempts, cfg.timeout_s), ("http://h:1", 4, 2.5))

    def test_from_sources_uu_tien_tham_so_truc_tiep(self):
        env = {"OSRM_BASE_URL": "http://h:1", "OSRM_MAX_ATTEMPTS": "4", "OSRM_TIMEOUT_S": "2.5"}
        cfg = OsrmConfig.from_sources(base_url="http://x:2", max_attempts=1, timeout_s=9,
                                      environ=env)
        self.assertEqual((cfg.base_url, cfg.max_attempts, cfg.timeout_s), ("http://x:2", 1, 9.0))

    def test_from_sources_gia_tri_khong_phai_so(self):
        env = {"OSRM_BASE_URL": "http://h:1", "OSRM_MAX_ATTEMPTS": "abc", "OSRM_TIMEOUT_S": "2"}
        with self.assertRaises(OsrmConfigError):
            OsrmConfig.from_sources(environ=env)


class TestRouteAndTable(unittest.TestCase):
    def test_route_thanh_cong(self):
        with FakeOsrmServer() as server:
            res = make_client(server).route(A, B)
        self.assertAlmostEqual(res.distance_m, 11100.0, delta=0.01)
        self.assertAlmostEqual(res.duration_s, 1110.0, delta=0.01)
        self.assertIn("/route/v1/driving/106.000000,10.000000;106.000000,10.100000", server.paths[0])

    def test_table_thanh_cong_va_o_khong_di_duoc(self):
        with FakeOsrmServer() as server:
            server.blocked = {(0, 2)}
            res = make_client(server).table([A, B, C])
        self.assertEqual(len(res.durations), 3)
        self.assertAlmostEqual(res.durations[0][1], 1110.0, delta=0.01)
        self.assertAlmostEqual(res.durations[1][2], 2220.0, delta=0.01)
        self.assertIsNone(res.durations[0][2])
        self.assertIsNone(res.distances[0][2])
        self.assertEqual(res.durations[0][0], 0.0)


class TestRetryAndErrors(unittest.TestCase):
    def test_thu_lai_roi_thanh_cong(self):
        with FakeOsrmServer() as server:
            server.fail_first = 2
            client = make_client(server, max_attempts=3)
            res = client.route(A, B)
            self.assertAlmostEqual(res.duration_s, 1110.0, delta=0.01)
            self.assertEqual(server.hits, 3)
            self.assertEqual(client.calls, 3)

    def test_het_so_lan_thu_nem_loi_khong_che_giau(self):
        with FakeOsrmServer() as server:
            server.mode = "http500"
            client = make_client(server, max_attempts=3)
            with self.assertRaises(OsrmUnavailableError) as ctx:
                client.route(A, B)
            self.assertEqual(server.hits, 3)
            self.assertEqual(client.calls, 3)
            self.assertIn("3 lần thử", str(ctx.exception))
            self.assertIn("HTTP 500", str(ctx.exception))

    def test_so_lan_thu_la_huu_han_va_dung_cau_hinh(self):
        with FakeOsrmServer() as server:
            server.mode = "http500"
            client = make_client(server, max_attempts=1)
            with self.assertRaises(OsrmUnavailableError):
                client.table([A, B])
            self.assertEqual(server.hits, 1)

    def test_timeout(self):
        with FakeOsrmServer() as server:
            server.sleep_s = 0.6
            client = make_client(server, max_attempts=2, timeout_s=0.1)
            with self.assertRaises(OsrmUnavailableError):
                client.route(A, B)
            self.assertEqual(client.calls, 2)

    def test_loi_mang_ket_noi_bi_tu_choi(self):
        server = FakeOsrmServer().start()
        url = server.base_url
        server.stop()
        client = OsrmClient(OsrmConfig(url, timeout_s=1.0, max_attempts=2, backoff_s=0.0))
        with self.assertRaises(OsrmUnavailableError):
            client.route(A, B)
        self.assertEqual(client.calls, 2)

    def test_backoff_chi_giua_cac_lan_thu(self):
        slept = []
        with FakeOsrmServer() as server:
            server.mode = "http500"
            config = OsrmConfig(server.base_url, timeout_s=2.0, max_attempts=3, backoff_s=0.5)
            client = OsrmClient(config, sleep=slept.append)
            with self.assertRaises(OsrmUnavailableError):
                client.route(A, B)
        self.assertEqual(slept, [0.5, 1.0])

    def test_json_hong_khong_thu_lai(self):
        with FakeOsrmServer() as server:
            server.mode = "badjson"
            with self.assertRaises(OsrmResponseError):
                make_client(server, max_attempts=3).route(A, B)
            self.assertEqual(server.hits, 1)

    def test_khong_co_duong_khong_thu_lai(self):
        with FakeOsrmServer() as server:
            server.mode = "noroute"
            with self.assertRaises(OsrmNoRouteError):
                make_client(server, max_attempts=3).route(A, B)
            self.assertEqual(server.hits, 1)

    def test_code_loi_khong_thu_lai(self):
        with FakeOsrmServer() as server:
            server.mode = "errorcode"
            with self.assertRaises(OsrmResponseError) as ctx:
                make_client(server, max_attempts=3).table([A, B])
            self.assertIn("InvalidQuery", str(ctx.exception))
            self.assertEqual(server.hits, 1)

    def test_phan_hoi_sai_cau_truc(self):
        with FakeOsrmServer() as server:
            server.mode = "malformed"
            client = make_client(server)
            with self.assertRaises(OsrmResponseError):
                client.route(A, B)
            with self.assertRaises(OsrmResponseError):
                client.table([A, B, C])

    def test_toa_do_sai_bi_chan_truoc_khi_goi_api(self):
        with FakeOsrmServer() as server:
            client = make_client(server)
            bad_inputs = [
                lambda: client.route((91.0, 106.0), B),
                lambda: client.route((10.0, 181.0), B),
                lambda: client.route((float("nan"), 106.0), B),
                lambda: client.route((True, 106.0), B),
                lambda: client.route((10.0,), B),
                lambda: client.table([A]),
                lambda: client.table("abc"),
            ]
            for call in bad_inputs:
                with self.assertRaises(OsrmInputError):
                    call()
            self.assertEqual(server.hits, 0)
            self.assertEqual(client.calls, 0)


if __name__ == "__main__":
    unittest.main()

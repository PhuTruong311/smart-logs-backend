import sys
import os

# Thêm thư mục gốc vào sys.path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)

def test_health_check():
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "message": "Smart Logs Backend đang hoạt động bình thường"}

def test_predict_route_endpoint():
    payload = {
        "origin": {"lat": 10.7769, "lng": 106.7009},
        "destination": {"lat": 10.8021, "lng": 106.6438},
        "departure_time": "2026-10-20T08:30:00Z"
    }
    response = client.post("/api/v1/routes/predict", json=payload)
    assert response.status_code == 200
    data = response.json()
    assert "recommended_route_id" in data
    assert len(data["candidate_routes"]) > 0

def test_update_eta_endpoint():
    payload = {
        "trip_id": "TRIP_101",
        "current_location": {"lat": 10.7850, "lng": 106.6910},
        "timestamp": "2026-10-20T08:42:00Z"
    }
    response = client.post("/api/v1/routes/update-eta", json=payload)
    assert response.status_code == 200
    data = response.json()
    assert data["trip_id"] == "TRIP_101"

def test_compare_routes_endpoint():
    payload = {
        "origin": {"lat": 10.7769, "lng": 106.7009},
        "destination": {"lat": 10.8021, "lng": 106.6438},
        "departure_time": "2026-11-03T08:30:00Z"
    }
    response = client.post("/api/v1/routes/compare", json=payload)
    assert response.status_code == 200
    data = response.json()
    assert "recommended_route_id" in data
    assert len(data["routes"]) == 3

def test_multistop_endpoint():
    payload = {
        "depot": {"lat": 10.7691, "lng": 106.7064},
        "delivery_points": [
            {"order_id": "ORD_1", "lat": 10.7760, "lng": 106.7062, "service_s": 300},
            {"order_id": "ORD_2", "lat": 10.7843, "lng": 106.7064, "service_s": 300}
        ],
        "return_to_depot": True
    }
    response = client.post("/api/v1/routes/multistop", json=payload)
    assert response.status_code == 200
    data = response.json()
    assert data["source"] in ["OSRM_OPTIMIZED", "FALLBACK_HAVERSINE"]

def test_traffic_summary_endpoint():
    response = client.get("/api/v1/traffic/summary")
    assert response.status_code == 200
    data = response.json()
    assert "total_records" in data
    assert isinstance(data["data"], list)

def test_traffic_summary_filter():
    response = client.get("/api/v1/traffic/summary?route_id=R1&time_slot=cao_diem_chieu")
    assert response.status_code == 200
    data = response.json()
    for item in data["data"]:
        assert item["route_id"] == "R1"
        assert item["time_slot"] == "cao_diem_chieu"

def test_gps_track_endpoint():
    payload = {
        "device_id": "TRUCK_01",
        "trip_id": "TRIP_999",
        "lat": 10.7769,
        "lng": 106.7009,
        "speed_kmh": 42.0,
        "timestamp": "2026-11-03T14:30:00Z"
    }
    response = client.post("/api/v1/gps/track", json=payload)
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "SUCCESS"
    assert data["device_id"] == "TRUCK_01"

def test_invalid_lat_validation():
    payload = {
        "device_id": "TRUCK_ERR",
        "lat": 999.0,  # Vĩ độ không hợp lệ (> 90)
        "lng": 106.7009,
        "timestamp": "2026-11-03T14:30:00Z"
    }
    response = client.post("/api/v1/gps/track", json=payload)
    assert response.status_code == 422  # Validation Error
# Smart Logs - ETA API Service

Hệ thống Backend tích hợp trí tuệ nhân tạo (ML) và tối ưu hóa vận tải xây dựng bằng **FastAPI** thuộc dự án **Smart Logs**, hỗ trợ dự báo thời gian di chuyển (ETA), so sánh tuyến đường, tối ưu hóa giao hàng đa điểm và tổng hợp dữ liệu giao thông tại khu vực TP. Hồ Chí Minh.

---

## 🛠 Công nghệ sử dụng (Tech Stack)

- **Ngôn ngữ:** Python 3.12
- **Framework:** FastAPI
- **Data Validation & Schemas:** Pydantic v2
- **Data Processing:** Pandas, NumPy
- **Machine Learning Model:** Scikit-learn (Random Forest)
- **Routing & Optimization:** OSRM (Open Source Routing Machine) & Fallback Haversine
- **ASGI Server:** Uvicorn
- **Unit & Integration Testing:** Pytest, TestClient & HTTPX

---

## 📂 Cấu trúc dự án (Project Structure)

```text
Đồ Án Thực Tế SM/
├── app/
│   ├── api/
│   │   ├── v1/
│   │   │   ├── __init__.py
│   │   │   └── endpoints.py          # Xử lý Router, Validation & Logic các API (3.5.2 - 3.5.7)
│   │   └── __init__.py
│   ├── schemas/
│   │   ├── __init__.py
│   │   ├── gps.py                    # Schema Pydantic cho dữ liệu GPS telemetry (3.5.7)
│   │   └── route.py                  # Schema Pydantic cho Route, Compare & Multistop
│   ├── services/
│   │   ├── __init__.py
│   │   ├── multistop_optimizer.py    # Thuật toán tối ưu chuỗi lộ trình giao hàng (3.5.3)
│   │   ├── osrm_client.py            # Client giao tiếp dịch vụ định tuyến OSRM
│   │   └── route_selector.py         # Hàm tính khoảng cách Haversine & hỗ trợ chọn tuyến
│   ├── __init__.py
│   └── main.py                       # Khởi tạo và cấu hình ứng dụng FastAPI
├── data/                             # Thư mục lưu trữ dữ liệu giao thông & lộ trình (Người 2)
│   ├── road_segments.csv             # Danh mục đoạn đường & thông số hình học
│   ├── traffic_observations.csv      # Dữ liệu quan sát tốc độ giao thông theo khung giờ
│   └── trips_history.csv             # Lịch sử chuyến đi thực tế
├── ml/                               # Mô hình Machine Learning dự báo ETA
│   ├── artifacts/                    # Lưu trữ mô hình Random Forest (.pkl)
│   └── inference/
│       └── predict_eta.py            # Module dự báo ETA từ mô hình ML
├── tests/                            # Bộ kiểm thử tự động (Test Suite)
│   ├── __init__.py
│   ├── test_api.py                   # Integration Tests toàn bộ các API Endpoints
│   ├── test_multistop_optimizer.py   # Unit test thuật toán tối ưu đa điểm
│   ├── test_osrm_client.py           # Unit test kết nối & fallback OSRM
│   └── test_route_selector.py        # Unit test khoảng cách & lọc tuyến
├── osrm_test_server.py               # Server Mock OSRM cho kiểm thử
├── venv/                             # Môi trường ảo Python
├── README.md                         # Tài liệu hướng dẫn hệ thống
└── requirements.txt                  # Danh sách thư viện phụ thuộc
```

Hướng dẫn Cài đặt & Khởi chạy (Setup Guide)
1. Khởi tạo & Kích hoạt Môi trường ảo (Virtual Environment)
# Tạo môi trường ảo
python -m venv venv

# Kích hoạt venv (Windows PowerShell)
.\venv\Scripts\activate
---

2. Cài đặt các thư viện phụ thuộc
python -m pip install -r requirements.txt
---

3. Khởi chạy Server FastAPI
uvicorn app.main:app --reload
Server sẽ khởi chạy tại địa chỉ: http://127.0.0.1:8000
---

Tài liệu API & Thử nghiệm (Swagger UI)
Khi server đang chạy, truy cập giao diện Interactive Swagger UI tại:
👉 http://127.0.0.1:8000/docs   
Danh sách các Endpoints chính:
1. GET /health
Mục đích: Kiểm tra trạng thái hoạt động của hệ thống (Health check).

2. POST /api/v1/routes/predict (Legacy)
Mục đích: Dự báo thời gian di chuyển (ETA) đơn lẻ cho tuyến mặc định.

3. POST /api/v1/routes/update-eta (Legacy)
Mục đích: Cập nhật lại ETA thời gian thực theo tọa độ xe gửi về.

4. POST /api/v1/routes/compare [3.5.2]
Mục đích: So sánh nhiều tuyến đường ứng cử viên, tích hợp mô hình ML Random Forest để dự báo ETA và đề xuất tuyến tối ưu nhất.

# Request JSON mẫu:
{
  "origin": {
    "lat": 10.7769,
    "lng": 106.7009
  },
  "destination": {
    "lat": 10.8021,
    "lng": 106.6438
  },
  "departure_time": "2026-11-03T08:30:00Z"
}

# Response 200 OK mẫu:
{
  "recommended_route_id": "ROUTE_A_MAI_CHI_THO",
  "routes": [
    {
      "route_id": "ROUTE_A_MAI_CHI_THO",
      "distance_km": 7.5,
      "predicted_eta_min": 18.2,
      "source": "ML_RANDOM_FOREST"
    },
    {
      "route_id": "ROUTE_B_VO_VAN_KIET",
      "distance_km": 8.25,
      "predicted_eta_min": 21.5,
      "source": "ML_RANDOM_FOREST"
    }
  ],
  "selection_reason": "Chọn ROUTE_A_MAI_CHI_THO vì thời gian di chuyển ngắn nhất (18.2 phút) từ nguồn ML_RANDOM_FOREST."
}

5. POST /api/v1/routes/multistop [3.5.3 & 3.5.5]
Mục đích: Tối ưu hóa thứ tự giao hàng đa điểm (1 kho + $N$ điểm giao) kết hợp OSRM Table API và tự động kích hoạt Fallback Haversine khi OSRM gặp sự cố.

# Request JSON mẫu:
{
  "depot": {
    "lat": 10.7691,
    "lng": 106.7064
  },
  "delivery_points": [
    {
      "order_id": "ORD_01",
      "lat": 10.7760,
      "lng": 106.7062,
      "service_s": 300
    },
    {
      "order_id": "ORD_02",
      "lat": 10.7843,
      "lng": 106.7064,
      "service_s": 300
    }
  ],
  "return_to_depot": true
}

# Response 200 OK mẫu:
{
  "total_distance_km": 4.25,
  "total_duration_min": 18.4,
  "optimized_sequence": ["ORD_01", "ORD_02"],
  "stops": [
    {
      "sequence": 1,
      "order_id": "ORD_01",
      "lat": 10.7760,
      "lng": 106.7062,
      "arrival_s": 210.0,
      "complete_s": 510.0,
      "late": false
    }
  ],
  "source": "OSRM_OPTIMIZED",
  "selection_reason": "Thứ tự đã tối ưu qua OSRM Table API."
}

6. GET /api/v1/traffic/summary [3.5.4]
Mục đích: Aggregation tổng hợp dữ liệu giao thông theo từng đoạn đường (segment_id) và khung giờ (time_slot) từ nguồn tập tin data/traffic_observations.csv của Người 2.   Query Parameters (Tùy chọn): route_id, segment_id, time_slot.   

# Response 200 OK mẫu:
{
  "total_records": 72,
  "data": [
    {
      "segment_id": "SEG01",
      "route_id": "R1",
      "segment_name": "Tôn Đức Thắng - đoạn Công trường Mê Linh",
      "road_type": "arterial",
      "time_slot": "cao_diem_chieu",
      "avg_speed_kmh": 19.48,
      "min_speed_kmh": 14.1,
      "max_speed_kmh": 25.5,
      "observation_count": 65
    }
  ],
  "source": "TRAFFIC_OBSERVATIONS_CSV",
  "selection_reason": "Tổng hợp thành công dữ liệu giao thông cho 72 đoạn đường."
}

7. POST /api/v1/gps/track [3.5.7]
Mục đích: API Stub tiếp nhận dữ liệu GPS telemetry (tọa độ, tốc độ, thiết bị) sẵn sàng chuẩn bị cho tích hợp theo dõi thời gian thực ở Tuần 4[cite: 7, 14, 15].

# Request JSON mẫu:
{
  "device_id": "TRUCK_01",
  "trip_id": "TRIP_101",
  "lat": 10.7769,
  "lng": 106.7009,
  "speed_kmh": 35.5,
  "timestamp": "2026-11-03T09:00:00Z"
}

# Kiểm thử Tự động (Testing Suite)Dự án sử dụng pytest kết hợp với TestClient để đảm bảo 100% test coverage trên toàn bộ hệ thống API và dịch vụ logic.   
Để chạy toàn bộ bài test:
pytest
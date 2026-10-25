# Smart Logs - ETA API Service

Hệ thống Backend xây dựng bằng **FastAPI** thuộc dự án **Smart Logs**, hỗ trợ dự báo thời gian di chuyển (ETA) và đề xuất chọn tuyến đường tối ưu tại khu vực TP. Hồ Chí Minh.

---

## 🛠 Công nghệ sử dụng (Tech Stack)

* **Ngôn ngữ:** Python 3.12
* **Framework:** FastAPI
* **Data Validation:** Pydantic v2
* **ASGI Server:** Uvicorn
* **Unit Testing:** Pytest & HTTPX

---

## 📂 Cấu trúc dự án (Project Structure)

```text
Đồ Án Thực Tế SM/
├── app/
│   ├── api/
│   │   ├── v1/
│   │   │   ├── __init__.py
│   │   │   └── endpoints.py      # Xử lý router & logic các API endpoints
│   │   └── __init__.py
│   ├── schemas/
│   │   ├── __init__.py
│   │   └── route.py          # Định nghĩa cấu trúc dữ liệu Pydantic (Request/Response)
│   ├── __init__.py
│   └── main.py               # Khởi tạo và cấu hình chính ứng dụng FastAPI
├── tests/
│   ├── __init__.py
│   └── test_api.py           # Kịch bản kiểm thử tự động (Unit Tests)
├── venv/                     # Môi trường ảo Python
├── README.md                 # Tài liệu hướng dẫn dự án
└── requirements.txt          # Danh sách thư viện phụ thuộc
```

---

## Hướng dẫn Cài đặt & Khởi chạy (Setup Guide)

### 1. Khởi tạo & Kích hoạt Môi trường ảo (Virtual Environment)

# Tạo môi trường ảo
python -m venv venv

# Kích hoạt venv (Windows PowerShell)
.\venv\Scripts\activate

### 2. Cài đặt các thư viện phụ thuộc

python -m pip install -r requirements.txt

### 3. Khởi chạy Server FastAPI

python -m uvicorn app.main:app --reload

Server sẽ khởi chạy tại địa chỉ: `http://127.0.0.1:8000`

---

## Tài liệu API & Thử nghiệm (Swagger UI)

Khi server đang chạy, truy cập giao diện Interactive Swagger UI tại:
👉 **`http://127.0.0.1:8000/docs`**

### Các Endpoints chính:

#### 1. `GET /health`
* **Mục đích:** Kiểm tra trạng thái hoạt động của server (Health check).

---

#### 2. `POST /api/v1/routes/predict`
* **Mục đích:** Dự báo thời gian di chuyển (ETA) và trả về danh sách tuyến đường đề xuất.
* **Request JSON mẫu:**
```json
{
  "origin": {
    "lat": 10.7769,
    "lng": 106.7009
  },
  "destination": {
    "lat": 10.8021,
    "lng": 106.6438
  },
  "departure_time": "2026-10-20T08:30:00Z"
}
```

* **Response 200 OK mẫu:**
```json
{
  "recommended_route_id": "ROUTE_A",
  "candidate_routes": [
    {
      "route_id": "ROUTE_A",
      "distance_km": 7.5,
      "predicted_duration_min": 22.3,
      "baseline_duration_min": 25.0,
      "geometry": "sample_polyline_a"
    }
  ],
  "selection_reason": "Tuyến có ETA ngắn nhất (22.3 phút)"
}
```

---

#### 3. `POST /api/v1/routes/update-eta`
* **Mục đích:** Cập nhật lại ETA thời gian thực dựa trên tọa độ vị trí GPS gửi về.
* **Request JSON mẫu:**
```json
{
  "trip_id": "TRIP_101",
  "current_location": {
    "lat": 10.7850,
    "lng": 106.6910
  },
  "timestamp": "2026-10-20T08:42:00Z"
}
```

* **Response 200 OK mẫu:**
```json
{
  "trip_id": "TRIP_101",
  "updated_eta_min": 11.2,
  "estimated_arrival_time": "2026-10-20T08:53:12Z"
}
```

---

## Chạy Kiểm thử Tự động (Unit Testing)

python -m pytest

---

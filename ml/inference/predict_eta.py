import os
import joblib
import pandas as pd
from datetime import datetime

# Sửa đường dẫn để trỏ đúng vào thư mục ml/models/rf_model_pipeline.joblib
MODEL_PATH = os.path.join(os.path.dirname(__file__), '../models/rf_model_pipeline.joblib')

class ETAPredictor:
    def __init__(self, model_path=MODEL_PATH):
        self.model_path = model_path
        self.pipeline = None
        self._load_model()

    def _load_model(self):
        """Nạp file model ML (.joblib) đã được huấn luyện"""
        if os.path.exists(self.model_path):
            try:
                self.pipeline = joblib.load(self.model_path)
                print("[INFO] Đã nạp thành công mô hình ML Random Forest.")
            except Exception as e:
                print(f"[ERROR] Không thể nạp mô hình ML: {e}")
                self.pipeline = None
        else:
            print(f"[WARN] Không tìm thấy file model tại: {self.model_path}")

    def _extract_time_features(self, departure_time):
        """Trích xuất giờ, thứ, cuối tuần, giờ cao điểm từ chuỗi thời gian"""
        if isinstance(departure_time, str):
            dt = datetime.fromisoformat(departure_time)
        else:
            dt = departure_time

        hour = dt.hour
        day_of_week = dt.weekday()
        is_weekend = 1 if day_of_week >= 5 else 0
        # Giờ cao điểm Sài Gòn: 7h-9h sáng và 17h-19h chiều
        is_rush_hour = 1 if (7 <= hour <= 9 or 17 <= hour <= 19) else 0

        return hour, day_of_week, is_weekend, is_rush_hour

    def predict_eta(self, route_id: str, distance_km: float, departure_time: str) -> dict:
        """
        Dự báo thời gian di chuyển.
        Fallback Level 1: Tính dựa trên vận tốc trung bình (25km/h) nếu ML lỗi.
        """
        try:
            # 1. Trích xuất đặc trưng thời gian
            hour, day_of_week, is_weekend, is_rush_hour = self._extract_time_features(departure_time)

            # 2. Đóng gói dữ liệu đầu vào cho model
            input_df = pd.DataFrame([{
                'route_id': route_id,
                'distance_km': float(distance_km),
                'hour_of_day': hour,
                'day_of_week': day_of_week,
                'is_weekend': is_weekend,
                'is_rush_hour': is_rush_hour
            }])

            # 3. Kịch bản chạy Model ML (Thành công)
            if self.pipeline is not None:
                predicted_eta = float(self.pipeline.predict(input_df)[0])
                return {
                    "route_id": route_id,
                    "distance_km": distance_km,
                    "predicted_eta_min": round(predicted_eta, 2),
                    "source": "ML_RANDOM_FOREST",
                    "status": "SUCCESS"
                }

        except Exception as e:
            print(f"[ERROR] Quá trình chạy dự báo lỗi: {e}")

        # 4. Kịch bản Fallback (Dự phòng khi lỗi / thiếu dữ liệu)
        # Giả sử tốc độ di chuyển trung bình ở Sài Gòn là 25 km/h
        fallback_eta = (float(distance_km) / 25.0) * 60.0
        return {
            "route_id": route_id,
            "distance_km": distance_km,
            "predicted_eta_min": round(fallback_eta, 2),
            "source": "BASELINE_FALLBACK",
            "status": "FALLBACK_USED"
        }

# Khởi tạo đối tượng chung để dùng cho API
predictor = ETAPredictor()

def predict_eta(route_id: str, distance_km: float, departure_time: str) -> dict:
    """Hàm giao tiếp chính dành cho Backend gọi"""
    return predictor.predict_eta(route_id, distance_km, departure_time)
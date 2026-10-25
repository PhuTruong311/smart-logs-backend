import math
from typing import List, Tuple
from app.schemas.route import CandidateRoute, Location

def calculate_haversine_distance(loc1: Location, loc2: Location) -> float:
    """Tính khoảng cách đường chim bay (Haversine) giữa 2 tọa độ (km)"""
    R = 6371.0  # Bán kính Trái Đất (km)
    lat1, lon1 = math.radians(loc1.lat), math.radians(loc1.lng)
    lat2, lon2 = math.radians(loc2.lat), math.radians(loc2.lng)
    
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    
    a = math.sin(dlat / 2)**2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2)**2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return round(R * c, 2)

def evaluate_and_select_route(origin: Location, destination: Location) -> Tuple[str, List[CandidateRoute], str]:
    """
    Module chọn tuyến đường tối ưu dựa trên khoảng cách và dự báo thời gian ETA.
    Ghép module định tuyến/chọn tuyến vào FastAPI Backend.
    """
    base_dist = calculate_haversine_distance(origin, destination)
    if base_dist == 0:
        base_dist = 5.0  # Mặc định khoảng cách mẫu nếu tọa độ trùng
        
    # Tạo 3 tuyến ứng viên giả định cho khu vực TP.HCM
    routes = [
        CandidateRoute(
            route_id="ROUTE_A_MAI_CHI_THO",
            distance_km=round(base_dist * 1.1, 2),
            predicted_duration_min=round((base_dist * 1.1) * 2.5, 1),  # Tốc độ ~24 km/h
            baseline_duration_min=round((base_dist * 1.1) * 3.2, 1),
            geometry="polyline_mai_chi_tho_express"
        ),
        CandidateRoute(
            route_id="ROUTE_B_VO_VAN_KIET",
            distance_km=round(base_dist * 1.3, 2),
            predicted_duration_min=round((base_dist * 1.3) * 2.1, 1),  # Tuyến thoáng hơn
            baseline_duration_min=round((base_dist * 1.3) * 2.8, 1),
            geometry="polyline_vo_van_kiet_highway"
        ),
        CandidateRoute(
            route_id="ROUTE_C_NOI_THANH",
            distance_km=round(base_dist * 0.95, 2),
            predicted_duration_min=round((base_dist * 0.95) * 3.8, 1),  # Tuyến ngắn hơn nhưng kẹt xe
            baseline_duration_min=round((base_dist * 0.95) * 4.2, 1),
            geometry="polyline_inner_city"
        )
    ]
    
    # Sắp xếp các tuyến theo thời gian dự báo (ETA) ngắn nhất
    sorted_routes = sorted(routes, key=lambda x: x.predicted_duration_min)
    best_route = sorted_routes[0]
    
    reason = f"Đã chọn {best_route.route_id} vì có ETA tối ưu nhất ({best_route.predicted_duration_min} phút cho {best_route.distance_km} km)"
    
    return best_route.route_id, sorted_routes, reason
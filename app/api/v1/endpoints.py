import os
from typing import List
from fastapi import APIRouter, status, HTTPException

# 1. Import đầy đủ Schemas cho cả 3.5.2 và 3.5.3
from app.schemas.route import (
    RouteCompareRequest,
    RouteCompareResponse,
    RouteCompareItem,
    MultistopRequest,
    MultistopResponse,
    OptimizedStopItem,
    Location
)

# 2. Import các module hỗ trợ
from app.services.route_selector import calculate_haversine_distance
from ml.inference.predict_eta import predict_eta
from app.services.osrm_client import OsrmClient, OsrmConfig, OsrmError
from app.services.multistop_optimizer import (
    GeoStop,
    Stop,
    optimize,
    optimize_with_osrm
)

router = APIRouter()


# ==========================================
# [3.5.2] API So sánh tuyến đường tích hợp ML
# ==========================================
@router.post(
    "/routes/compare",
    response_model=RouteCompareResponse,
    status_code=status.HTTP_200_OK,
    summary="[3.5.2] So sánh các tuyến đường tích hợp mô hình ML Random Forest"
)
def compare_routes(payload: RouteCompareRequest):
    # 1. Tính khoảng cách cơ sở (Haversine)
    base_dist = calculate_haversine_distance(payload.origin, payload.destination)
    if base_dist == 0:
        base_dist = 5.0  # Mặc định 5km nếu tọa độ trùng

    # 2. Danh sách các tuyến đường ứng viên (Giả định 3 tuyến TP.HCM)
    candidate_routes = [
        {"route_id": "ROUTE_B_VO_VAN_KIET", "dist": round(base_dist * 1.1, 2)},
        {"route_id": "ROUTE_A_MAI_CHI_THO", "dist": round(base_dist * 1.0, 2)},
        {"route_id": "ROUTE_C_NOI_THANH", "dist": round(base_dist * 1.25, 2)},
    ]

    # 3. Gọi hàm ML predict_eta cho từng tuyến
    dep_time = payload.departure_time or "2026-10-25T08:30:00Z"
    results = []

    for item in candidate_routes:
        res = predict_eta(
            route_id=item["route_id"],
            distance_km=item["dist"],
            departure_time=dep_time
        )
        results.append(RouteCompareItem(**res))

    # 4. Sắp xếp tìm tuyến có ETA ngắn nhất
    sorted_routes = sorted(results, key=lambda x: x.predicted_eta_min)
    best_route = sorted_routes[0]

    return RouteCompareResponse(
        recommended_route_id=best_route.route_id,
        routes=sorted_routes,
        selection_reason=f"Chọn {best_route.route_id} vì thời gian di chuyển ngắn nhất ({best_route.predicted_eta_min} phút) từ nguồn {best_route.source}."
    )


# ==========================================
# [3.5.3] API Tối ưu hóa giao hàng đa điểm
# ==========================================
@router.post(
    "/routes/multistop",
    response_model=MultistopResponse,
    status_code=status.HTTP_200_OK,
    summary="[3.5.3] Tối ưu hóa lộ trình giao hàng đa điểm (1 kho + nhiều điểm)"
)
def plan_multistop_route(payload: MultistopRequest):
    coords_map = {pt.order_id: (pt.lat, pt.lng) for pt in payload.delivery_points}
    depot_coords = (payload.depot.lat, payload.depot.lng)
    
    # Chuyển đổi dữ liệu sang định dạng GeoStop
    geo_stops = [
        GeoStop(
            order_id=pt.order_id,
            lat=pt.lat,
            lon=pt.lng,
            service_s=pt.service_s or 0.0,
            deadline_s=pt.deadline_s
        )
        for pt in payload.delivery_points
    ]

    try:
        # Gọi module OSRM Client & Optimizer của Tấn Phát
        osrm_url = os.getenv("OSRM_BASE_URL", "http://router.project-osrm.org").replace("\\", "").rstrip("/")
        config = OsrmConfig.from_sources(
            base_url=osrm_url,
            max_attempts=2,
            timeout_s=5.0
        )
        client = OsrmClient(config)

        result = optimize_with_osrm(
            client=client,
            origin=depot_coords,
            geo_stops=geo_stops,
            return_to_depot=payload.return_to_depot or False,
            start_time_s=payload.start_time_s or 0.0
        )
        
        plan = result.optimal
        stop_items = [
            OptimizedStopItem(
                sequence=s.seq,
                order_id=s.order_id,
                lat=coords_map[s.order_id][0],
                lng=coords_map[s.order_id][1],
                arrival_s=round(s.arrival_s, 1),
                complete_s=round(s.complete_s, 1),
                late=s.late
            )
            for s in plan.stops
        ]

        return MultistopResponse(
            total_distance_km=round((plan.total_distance_m or 0.0) / 1000.0, 2),
            total_duration_min=round(plan.total_time_s / 60.0, 2),
            optimized_sequence=plan.sequence,
            stops=stop_items,
            source="OSRM_OPTIMIZED",
            selection_reason=f"Thứ tự đã tối ưu qua OSRM Table API ({result.evaluated} hoán vị đã xét)."
        )

    except (OsrmError, Exception) as e:
        # Cơ chế Fallback Haversine nếu OSRM bị sự cố
        nodes_coords = [Location(lat=payload.depot.lat, lng=payload.depot.lng)] + [
            Location(lat=pt.lat, lng=pt.lng) for pt in payload.delivery_points
        ]
        n = len(nodes_coords)
        
        time_matrix = [[0.0] * n for _ in range(n)]
        dist_matrix = [[0.0] * n for _ in range(n)]
        
        for i in range(n):
            for j in range(n):
                if i != j:
                    d_km = calculate_haversine_distance(nodes_coords[i], nodes_coords[j])
                    d_m = d_km * 1000.0
                    dist_matrix[i][j] = d_m
                    time_matrix[i][j] = d_m / 6.94  # ~25 km/h

        stops_internal = [
            Stop(order_id=pt.order_id, node=idx + 1, service_s=pt.service_s or 0.0, deadline_s=pt.deadline_s)
            for idx, pt in enumerate(payload.delivery_points)
        ]

        fb_result = optimize(
            time_matrix=time_matrix,
            stops=stops_internal,
            distance_matrix=dist_matrix,
            return_to_depot=payload.return_to_depot or False,
            start_time_s=payload.start_time_s or 0.0
        )
        
        plan = fb_result.optimal
        stop_items = [
            OptimizedStopItem(
                sequence=s.seq,
                order_id=s.order_id,
                lat=coords_map[s.order_id][0],
                lng=coords_map[s.order_id][1],
                arrival_s=round(s.arrival_s, 1),
                complete_s=round(s.complete_s, 1),
                late=s.late
            )
            for s in plan.stops
        ]

        return MultistopResponse(
            total_distance_km=round((plan.total_distance_m or 0.0) / 1000.0, 2),
            total_duration_min=round(plan.total_time_s / 60.0, 2),
            optimized_sequence=plan.sequence,
            stops=stop_items,
            source="FALLBACK_HAVERSINE",
            selection_reason=f"Dùng Fallback Haversine do OSRM không phản hồi ({str(e)})."
        )
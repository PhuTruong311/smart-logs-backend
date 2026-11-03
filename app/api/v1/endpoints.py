from fastapi import APIRouter, status
from app.schemas.route import RouteCompareRequest, RouteCompareResponse, RouteCompareItem
from app.services.route_selector import calculate_haversine_distance
# Import hàm predict_eta từ ml/inference/predict_eta.py
from ml.inference.predict_eta import predict_eta

router = APIRouter()

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
        # Gọi trực tiếp module của Quỳnh Phiếu
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
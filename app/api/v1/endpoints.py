from fastapi import APIRouter, status
from app.schemas.route import (
    RoutePredictionRequest,
    RoutePredictionResponse,
    RealtimeETAUpdateRequest,
    RealtimeETAUpdateResponse
)
from app.services.route_selector import evaluate_and_select_route
from datetime import timedelta

router = APIRouter()

@router.post(
    "/routes/predict",
    response_model=RoutePredictionResponse,
    status_code=status.HTTP_200_OK,
    summary="[Màn hình 2] Dự báo thời gian di chuyển và chọn tuyến tối ưu"
)
def predict_route(payload: RoutePredictionRequest):
    # Gọi module ghép thuật toán chọn tuyến tối ưu
    best_id, candidate_routes, selection_reason = evaluate_and_select_route(
        payload.origin, 
        payload.destination
    )
    
    return RoutePredictionResponse(
        recommended_route_id=best_id,
        candidate_routes=candidate_routes,
        selection_reason=selection_reason
    )

@router.post(
    "/routes/update-eta",
    response_model=RealtimeETAUpdateResponse,
    status_code=status.HTTP_200_OK,
    summary="[Màn hình 3] Cập nhật ETA Realtime theo vị trí GPS"
)
def update_realtime_eta(payload: RealtimeETAUpdateRequest):
    # Logic giả định tính lại ETA thời gian thực khi xe đang di chuyển
    updated_eta = 11.2
    arrival_time = payload.timestamp + timedelta(minutes=updated_eta)
    
    return RealtimeETAUpdateResponse(
        trip_id=payload.trip_id,
        updated_eta_min=updated_eta,
        estimated_arrival_time=arrival_time
    )
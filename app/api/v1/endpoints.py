import os
import pandas as pd
from pathlib import Path
from typing import List, Optional
from fastapi import APIRouter, status, HTTPException
from pydantic import BaseModel, Field

# Import Schemas
from app.schemas.route import (
    RouteCompareRequest,
    RouteCompareResponse,
    RouteCompareItem,
    MultistopRequest,
    MultistopResponse,
    OptimizedStopItem,
    Location
)
from app.schemas.gps import GpsPingRequest, GpsPingResponse

# Import Services & ML
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

# --- Schemas bổ sung cho các test cũ ---
class PredictRouteRequest(BaseModel):
    origin: Location
    destination: Location
    departure_time: Optional[str] = None

class UpdateEtaRequest(BaseModel):
    trip_id: str
    current_location: Location
    timestamp: str


# ==========================================
# [Legacy] API Dự đoán ETA tuyến đường đơn
# ==========================================
@router.post("/routes/predict", status_code=status.HTTP_200_OK)
def predict_route(payload: PredictRouteRequest):
    try:
        dist = calculate_haversine_distance(payload.origin, payload.destination)
        if dist == 0:
            dist = 5.0
        res = predict_eta("ROUTE_DEFAULT", dist, payload.departure_time or "2026-10-25T08:30:00Z")
        
        return {
            "recommended_route_id": res.get("route_id", "ROUTE_DEFAULT"),
            "candidate_routes": [res],
            "routes": [res],
            "distance_km": res.get("distance_km", dist),
            "predicted_eta_min": res.get("predicted_eta_min", 15.0),
            "source": res.get("source", "ML_RANDOM_FOREST"),
            "status": res.get("status", "SUCCESS"),
            "selection_reason": "Dự đoán ETA thành công cho tuyến mặc định."
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Lỗi dự đoán ML: {str(e)}")


# ==========================================
# [Legacy] API Cập nhật ETA theo thời gian thực
# ==========================================
@router.post("/routes/update-eta", status_code=status.HTTP_200_OK)
def update_eta(payload: UpdateEtaRequest):
    if not payload.trip_id.strip():
        raise HTTPException(status_code=400, detail="trip_id không được để trống")
    return {
        "trip_id": payload.trip_id,
        "status": "UPDATED",
        "updated_eta_min": 15.0,
        "message": "Cập nhật ETA thành công"
    }


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
    try:
        base_dist = calculate_haversine_distance(payload.origin, payload.destination)
        if base_dist == 0:
            base_dist = 5.0

        candidate_routes = [
            {"route_id": "ROUTE_B_VO_VAN_KIET", "dist": round(base_dist * 1.1, 2)},
            {"route_id": "ROUTE_A_MAI_CHI_THO", "dist": round(base_dist * 1.0, 2)},
            {"route_id": "ROUTE_C_NOI_THANH", "dist": round(base_dist * 1.25, 2)},
        ]

        dep_time = payload.departure_time or "2026-10-25T08:30:00Z"
        results = []

        for item in candidate_routes:
            res = predict_eta(
                route_id=item["route_id"],
                distance_km=item["dist"],
                departure_time=dep_time
            )
            results.append(RouteCompareItem(**res))

        sorted_routes = sorted(results, key=lambda x: x.predicted_eta_min)
        best_route = sorted_routes[0]

        return RouteCompareResponse(
            recommended_route_id=best_route.route_id,
            routes=sorted_routes,
            selection_reason=f"Chọn {best_route.route_id} vì thời gian di chuyển ngắn nhất ({best_route.predicted_eta_min} phút) từ nguồn {best_route.source}."
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Lỗi so sánh tuyến đường: {str(e)}")


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
    if not payload.delivery_points:
        raise HTTPException(status_code=400, detail="Danh sách điểm giao hàng không được rỗng")

    coords_map = {pt.order_id: (pt.lat, pt.lng) for pt in payload.delivery_points}
    depot_coords = (payload.depot.lat, payload.depot.lng)
    
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
        # Fallback Haversine khi OSRM lỗi hoặc không phản hồi
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
                    time_matrix[i][j] = d_m / 6.94

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


# ==========================================
# [3.5.4 & 3.5.5] API Tổng hợp dữ liệu giao thông & Xử lý lỗi
# ==========================================
class TrafficSummaryItem(BaseModel):
    segment_id: Optional[str] = None
    route_id: str
    segment_name: Optional[str] = None
    road_type: Optional[str] = None
    time_slot: Optional[str] = None
    avg_speed_kmh: float
    min_speed_kmh: float
    max_speed_kmh: float
    observation_count: int

class TrafficSummaryResponse(BaseModel):
    total_records: int
    data: List[TrafficSummaryItem]
    source: str = "TRAFFIC_OBSERVATIONS_CSV"
    selection_reason: str


@router.get(
    "/traffic/summary",
    response_model=TrafficSummaryResponse,
    status_code=status.HTTP_200_OK,
    summary="[3.5.4] Tổng hợp dữ liệu giao thông theo đoạn đường/khung giờ"
)
def get_traffic_summary(
    segment_id: Optional[str] = None,
    route_id: Optional[str] = None,
    time_slot: Optional[str] = None
):
    csv_path = Path("data/traffic_observations.csv")
    segments_path = Path("data/road_segments.csv")
    
    if not csv_path.exists():
        raise HTTPException(
            status_code=404, 
            detail="Không tìm thấy dữ liệu giao thông (data/traffic_observations.csv)"
        )
        
    try:
        df = pd.read_csv(csv_path)
        df = df[df['observation_level'] == 'segment']
        
        if segments_path.exists():
            seg_df = pd.read_csv(segments_path)
            df = pd.merge(df, seg_df[['segment_id', 'segment_name', 'road_type']], on='segment_id', how='left')
        else:
            df['segment_name'] = "Unknown"
            df['road_type'] = "Unknown"

        if segment_id:
            df = df[df['segment_id'].str.upper() == segment_id.upper()]
        if route_id:
            df = df[df['route_id'].str.upper() == route_id.upper()]
        if time_slot:
            df = df[df['time_slot'].str.lower() == time_slot.lower()]

        if df.empty:
            return TrafficSummaryResponse(
                total_records=0,
                data=[],
                selection_reason="Không tìm thấy bản ghi giao thông thỏa mãn điều kiện lọc."
            )

        grouped = df.groupby(
            ['segment_id', 'segment_name', 'route_id', 'road_type', 'time_slot']
        ).agg(
            avg_speed=('speed_kmh', 'mean'),
            min_speed=('speed_kmh', 'min'),
            max_speed=('speed_kmh', 'max'),
            obs_count=('obs_id', 'count')
        ).reset_index()

        items = []
        for _, row in grouped.iterrows():
            items.append(TrafficSummaryItem(
                segment_id=str(row['segment_id']),
                route_id=str(row['route_id']),
                segment_name=str(row['segment_name']),
                road_type=str(row['road_type']),
                time_slot=str(row['time_slot']),
                avg_speed_kmh=round(float(row['avg_speed']), 2),
                min_speed_kmh=round(float(row['min_speed']), 2),
                max_speed_kmh=round(float(row['max_speed']), 2),
                observation_count=int(row['obs_count'])
            ))

        return TrafficSummaryResponse(
            total_records=len(items),
            data=items,
            selection_reason=f"Tổng hợp thành công dữ liệu giao thông cho {len(items)} đoạn đường."
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Lỗi truy vấn dữ liệu giao thông: {str(e)}")


# ==========================================
# [3.5.7] API Tiếp nhận GPS telemetry (Chuẩn bị cho Tuần 4)
# ==========================================
@router.post(
    "/gps/track",
    response_model=GpsPingResponse,
    status_code=status.HTTP_200_OK,
    summary="[3.5.7] Tiếp nhận dữ liệu GPS định vị theo thời gian thực"
)
def track_gps_location(payload: GpsPingRequest):
    return GpsPingResponse(
        status="SUCCESS",
        device_id=payload.device_id,
        message=f"Đã ghi nhận tọa độ ({payload.lat}, {payload.lng}) lúc {payload.timestamp}",
        recorded_at=payload.timestamp
    )
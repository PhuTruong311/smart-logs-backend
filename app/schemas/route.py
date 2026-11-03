from pydantic import BaseModel
from typing import List, Optional

class Location(BaseModel):
    lat: float
    lng: float

# Class phục vụ cho route_selector.py
class CandidateRoute(BaseModel):
    route_id: str
    distance_km: float
    predicted_duration_min: float
    baseline_duration_min: float
    geometry: Optional[str] = None

# Schemas phục vụ cho 3.5.2 /routes/compare
class RouteCompareRequest(BaseModel):
    origin: Location
    destination: Location
    departure_time: Optional[str] = "2026-10-25T08:30:00Z"

class RouteCompareItem(BaseModel):
    route_id: str
    distance_km: float
    predicted_eta_min: float
    source: str
    status: str

class RouteCompareResponse(BaseModel):
    recommended_route_id: str
    routes: List[RouteCompareItem]
    selection_reason: str

# --- Schemas cho Nhiệm vụ 3.5.3 /routes/multistop ---
class GeoDeliveryPoint(BaseModel):
    order_id: str
    lat: float
    lng: float
    service_s: Optional[float] = 0.0
    deadline_s: Optional[float] = None

class MultistopRequest(BaseModel):
    depot: Location
    delivery_points: List[GeoDeliveryPoint]
    return_to_depot: Optional[bool] = False
    start_time_s: Optional[float] = 0.0

class OptimizedStopItem(BaseModel):
    sequence: int
    order_id: str
    lat: float
    lng: float
    arrival_s: float
    complete_s: float
    late: bool

class MultistopResponse(BaseModel):
    total_distance_km: float
    total_duration_min: float
    optimized_sequence: List[str]
    stops: List[OptimizedStopItem]
    source: str
    selection_reason: str
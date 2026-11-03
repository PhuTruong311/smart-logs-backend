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
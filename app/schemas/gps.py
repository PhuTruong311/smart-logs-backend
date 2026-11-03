from pydantic import BaseModel, Field
from typing import Optional

class GpsPingRequest(BaseModel):
    device_id: str = Field(..., description="ID thiết bị GPS/xe", example="TRUCK_01")
    trip_id: Optional[str] = Field(None, description="Mã chuyến hàng nếu có", example="TRIP_101")
    lat: float = Field(..., ge=-90.0, le=90.0, description="Vĩ độ", example=10.7769)
    lng: float = Field(..., ge=-180.0, le=180.0, description="Kinh độ", example=106.7009)
    speed_kmh: Optional[float] = Field(0.0, ge=0.0, description="Tốc độ di chuyển (km/h)", example=35.5)
    bearing: Optional[float] = Field(None, ge=0.0, le=360.0, description="Góc hướng di chuyển (0-360 độ)", example=180.0)
    timestamp: str = Field(..., description="Thời gian ghi nhận GPS (ISO string)", example="2026-11-03T09:00:00Z")

class GpsPingResponse(BaseModel):
    status: str = "SUCCESS"
    device_id: str
    message: str
    recorded_at: str
from typing import Optional, Dict, Any, List, Union
from pydantic import BaseModel, Field, model_validator


class DatasetRequest(BaseModel):
    provider_id: str = Field(..., description="Provider identifier (e.g. 'era5')")
    parameter_type: Optional[str] = Field(
        None,
        description="Single parameter name, e.g. 'total_precipitation' or 'rainfall' (backward compatible)"
    )
    parameters: Optional[List[str]] = Field(
        None,
        description="List of multiple parameters to fetch simultaneously, e.g. ['2m_temperature', 'total_precipitation', 'volumetric_soil_water_layer_1']"
    )
    location: str = Field(..., description="Target location name (e.g. 'gujarat/ahmedabad') or 'lat,lon'")
    start_date: str = Field(..., description="Start date in YYYY-MM-DD format")
    end_date: str = Field(..., description="End date in YYYY-MM-DD format")
    resolution: Optional[str] = Field("0.1deg", description="Resolution (e.g., '0.1deg' for ERA5-Land, '0.25deg' for ERA5)")
    temporal_resolution: Optional[str] = Field("hourly", description="'hourly' (24 readings/day) or 'daily'")
    hours: Optional[List[str]] = Field(None, description="Optional specific hours, e.g. ['00:00', '06:00', '12:00', '18:00'] or default all 24 hours")
    version: Optional[str] = Field("v1", description="Dataset specification version")
    
    # Optional precise coordinates override
    latitude: Optional[float] = None
    longitude: Optional[float] = None

    @model_validator(mode="after")
    def validate_parameters(self):
        if not self.parameters and not self.parameter_type:
            raise ValueError("Either 'parameter_type' (single string) or 'parameters' (list of variables) must be provided.")
        if self.parameters and not self.parameter_type:
            self.parameter_type = self.parameters[0] if len(self.parameters) == 1 else "multi_parameter"
        return self

    @property
    def requested_parameters(self) -> List[str]:
        if self.parameters and len(self.parameters) > 0:
            return self.parameters
        if self.parameter_type:
            return [self.parameter_type]
        return []


class DatasetResponse(BaseModel):
    status: str = Field(..., description="'accepted', 'running', 'ready', or 'failed'")
    job_id: Optional[str] = None
    download_url: Optional[str] = None
    cached: Optional[bool] = None
    data_key: Optional[str] = None
    detail: Optional[str] = None
    record_count: Optional[int] = None
    file_size_bytes: Optional[int] = None
    parameters: Optional[List[str]] = None
    summary_stats: Optional[Dict[str, Any]] = None


class DatasetDataPreview(BaseModel):
    data_key: str
    parameter_type: str
    parameters: List[str]
    units: Dict[str, str]
    total_records: int
    sample_records: List[Dict[str, Any]]
    summary: Dict[str, Any]

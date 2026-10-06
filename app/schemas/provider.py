from typing import List, Dict, Any
from pydantic import BaseModel, Field


class ProviderInfo(BaseModel):
    id: str = Field(..., description="Unique provider ID")
    name: str = Field(..., description="Human readable provider name")
    description: str = Field(..., description="Description of provider capabilities and data source")
    supported_parameters: List[str] = Field(..., description="Supported parameters (e.g. rainfall, temperature)")
    default_resolution: str = Field(..., description="Native provider resolution")
    status: str = Field(..., description="'active', 'simulation_mode', or 'unconfigured'")
    requires_api_key: bool
    is_configured: bool
    details: Dict[str, Any] = {}

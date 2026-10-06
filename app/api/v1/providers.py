from typing import List, Dict, Any
from fastapi import APIRouter
from app.providers.registry import registry
from app.providers.era5 import ERA5Provider
from app.schemas.provider import ProviderInfo

router = APIRouter(prefix="/providers", tags=["Providers"])


@router.get("", response_model=List[ProviderInfo])
def list_providers():
    """
    List all registered data providers and their supported parameters and status.
    """
    return registry.list_all()


@router.get("/era5/variables")
def get_era5_variables_catalog() -> Dict[str, Any]:
    """
    Explore the complete catalog of ERA5 and ERA5-Land variables available in the platform,
    including soil moisture layers (0-7cm to 289cm), temperature, evaporation, runoff, radiation, and wind.
    """
    era5_provider: ERA5Provider = registry.get("era5")
    return era5_provider.get_variable_catalog()


@router.get("/{provider_id}", response_model=ProviderInfo)
def get_provider(provider_id: str):
    """
    Get detailed information about a specific data provider.
    """
    provider = registry.get(provider_id)
    return provider.get_info()

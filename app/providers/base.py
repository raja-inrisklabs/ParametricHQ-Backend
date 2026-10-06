from abc import ABC, abstractmethod
from typing import List, Dict, Any, Optional
import pandas as pd
from app.schemas.dataset import DatasetRequest
from app.schemas.provider import ProviderInfo


class BaseProvider(ABC):
    """
    Abstract Base Class for all Parametric Environmental Data Providers.
    Any new provider (e.g. ERA5, NOAA, CHIRPS, Sentinel, Open-Meteo) must implement this interface.
    """

    @property
    @abstractmethod
    def provider_id(self) -> str:
        """Unique slug identifier for the provider (e.g. 'era5')."""
        pass

    @property
    @abstractmethod
    def name(self) -> str:
        """Human-readable provider name."""
        pass

    @property
    @abstractmethod
    def description(self) -> str:
        """Short overview of provider datasets, resolution, and latency."""
        pass

    @property
    @abstractmethod
    def supported_parameters(self) -> List[str]:
        """List of supported parameters (e.g. ['rainfall', 'temperature', 'wind_speed'])."""
        pass

    @property
    @abstractmethod
    def default_resolution(self) -> str:
        """Default spatial resolution (e.g. '0.25deg' ~ 31km for ERA5)."""
        pass

    @property
    @abstractmethod
    def is_configured(self) -> bool:
        """Returns True if required credentials (API keys, tokens) are present."""
        pass

    @property
    def status(self) -> str:
        if self.is_configured:
            return "active"
        return "simulation_mode"

    def get_info(self) -> ProviderInfo:
        return ProviderInfo(
            id=self.provider_id,
            name=self.name,
            description=self.description,
            supported_parameters=self.supported_parameters,
            default_resolution=self.default_resolution,
            status=self.status,
            requires_api_key=True,
            is_configured=self.is_configured,
            details=self.get_extra_details()
        )

    def get_extra_details(self) -> Dict[str, Any]:
        return {}

    @abstractmethod
    def fetch_data(
        self,
        request: DatasetRequest,
        latitude: float,
        longitude: float
    ) -> pd.DataFrame:
        """
        Fetches and transforms data into a standard pandas DataFrame.
        Columns required in returned DataFrame:
          - 'timestamp': str (ISO 8601 or YYYY-MM-DD HH:MM:SS)
          - 'value': float
          - 'unit': str (e.g., 'mm', 'degC', 'm/s')
          - 'parameter': str
          - 'latitude': float
          - 'longitude': float
        """
        pass

    def health_check(self) -> Dict[str, Any]:
        """Validates provider readiness."""
        return {
            "provider_id": self.provider_id,
            "status": self.status,
            "is_configured": self.is_configured
        }

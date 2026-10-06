from typing import Dict, List, Optional
from app.core.exceptions import ProviderNotFoundError
from app.providers.base import BaseProvider
from app.providers.era5 import ERA5Provider
from app.providers.mock_provider import FrontendMockProvider
from app.schemas.provider import ProviderInfo


class ProviderRegistry:
    def __init__(self):
        self._providers: Dict[str, BaseProvider] = {}

    def register(self, provider: BaseProvider) -> None:
        self._providers[provider.provider_id.lower()] = provider

    def get(self, provider_id: str) -> BaseProvider:
        pid = provider_id.lower()
        if pid not in self._providers:
            raise ProviderNotFoundError(provider_id)
        return self._providers[pid]

    def has(self, provider_id: str) -> bool:
        return provider_id.lower() in self._providers

    def list_all(self) -> List[ProviderInfo]:
        return [provider.get_info() for provider in self._providers.values()]


# Singleton registry instance
registry = ProviderRegistry()

# Register ERA5 as the primary provider
registry.register(ERA5Provider())

# Register mock providers for existing frontend dropdown compatibility
registry.register(FrontendMockProvider("provider-a", "rainfall", "Provider A (Rainfall)"))
registry.register(FrontendMockProvider("provider-b", "rainfall", "Provider B (Rainfall)"))
registry.register(FrontendMockProvider("provider-c", "temperature", "Provider C (Temperature)"))
registry.register(FrontendMockProvider("provider-d", "satellite", "Provider D (Satellite)"))

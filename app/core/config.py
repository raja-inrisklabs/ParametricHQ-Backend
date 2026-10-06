from typing import List, Union, Optional
from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from pathlib import Path


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore"
    )

    APP_NAME: str = "Parametric Data Platform"
    ENVIRONMENT: str = "development"
    DEBUG: bool = True
    HOST: str = "0.0.0.0"
    PORT: int = 8000
    API_PREFIX: str = "/api"
    BASE_URL: str = "http://localhost:8000"
    CORS_ORIGINS: Union[List[str], str] = ["*"]

    # Storage: Scalable GCS-ready filesystem layout
    STORAGE_BACKEND: str = "local"  # 'local' or 'gcs'
    STORAGE_DIR: str = "./storage"
    GCS_BUCKET_NAME: Optional[str] = ""

    # ERA5 (ECMWF Copernicus Climate Data Store)
    CDSAPI_URL: str = "https://cds.climate.copernicus.eu/api"
    CDSAPI_KEY: str = ""
    ERA5_FALLBACK_SIMULATION: bool = True

    @field_validator("CORS_ORIGINS", mode="before")
    @classmethod
    def assemble_cors_origins(cls, v: Union[str, List[str]]) -> List[str]:
        if isinstance(v, str) and not v.startswith("["):
            return [i.strip() for i in v.split(",")]
        elif isinstance(v, list):
            return v
        return ["*"]

    @property
    def storage_path(self) -> Path:
        p = Path(self.STORAGE_DIR)
        p.mkdir(parents=True, exist_ok=True)
        return p

    @property
    def has_era5_credentials(self) -> bool:
        return bool(self.CDSAPI_KEY and self.CDSAPI_KEY.strip())


settings = Settings()

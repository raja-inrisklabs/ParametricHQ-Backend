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
    API_PREFIX: str = "/api/v1"
    BASE_URL: str = "http://localhost:8000"
    CORS_ORIGINS: Union[List[str], str] = ["*"]

    # Storage: local files for tests, Google Cloud Storage for the running API.
    STORAGE_BACKEND: str = "gcs"  # 'local' or 'gcs'
    STORAGE_DIR: str = "./storage"
    GCS_BUCKET_NAME: str = "parametric_hq"
    GOOGLE_CLOUD_PROJECT: str = "testing-41686"

    # ERA5 (ECMWF Copernicus Climate Data Store)
    CDSAPI_URL: str = "https://cds.climate.copernicus.eu/api"
    CDSAPI_KEY: str = ""
    ERA5_FALLBACK_SIMULATION: bool = True
    # One CDS account key per parallel month. CDSAPI_KEY is the first key.
    CDSAPI_KEY_1: str = ""
    CDSAPI_KEY_2: str = ""
    CDSAPI_KEY_3: str = ""
    CDSAPI_KEY_4: str = ""
    CDSAPI_KEY_5: str = ""
    CDSAPI_KEY_6: str = ""
    CDSAPI_KEY_7: str = ""
    CDSAPI_KEY_8: str = ""
    CDSAPI_KEY_9: str = ""
    CDSAPI_KEY_10: str = ""
    CDSAPI_KEY_11: str = ""
    CDSAPI_KEY_12: str = ""

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
        return bool(self.cds_api_keys)

    @property
    def cds_api_keys(self) -> List[str]:
        """Keys that can run a month at the same time. Blank and repeated keys are dropped."""
        names = ["CDSAPI_KEY"] + [f"CDSAPI_KEY_{index}" for index in range(1, 13)]
        seen = []
        for name in names:
            value = str(getattr(self, name, "") or "").strip()
            if value and value not in seen:
                seen.append(value)
        return seen


settings = Settings()

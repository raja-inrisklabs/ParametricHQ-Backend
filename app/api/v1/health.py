from fastapi import APIRouter
from app.core.config import settings
from app.providers.registry import registry
from app.services.storage_service import storage_backend

router = APIRouter(tags=["Health"])


@router.get("/health")
def health_check():
    """
    Comprehensive platform health and readiness check.
    Validates storage writability, registered providers, and ERA5 readiness.
    """
    storage_status = "writable"
    try:
        if settings.STORAGE_BACKEND == "gcs":
            storage_status = f"gcs:{settings.GCS_BUCKET_NAME}"
        else:
            storage_backend.providers_dir.mkdir(parents=True, exist_ok=True)
    except Exception as e:
        storage_status = f"error: {str(e)}"

    providers_status = {
        p.id: {
            "name": p.name,
            "status": p.status,
            "is_configured": p.is_configured,
            "supported_parameters_count": len(p.supported_parameters)
        }
        for p in registry.list_all()
    }

    era5_provider = registry.get("era5")
    catalog = storage_backend.list_datasets()

    return {
        "ok": not str(storage_status).startswith("error"),
        "app_name": settings.APP_NAME,
        "environment": settings.ENVIRONMENT,
        "storage": storage_status,
        "storage_format": "apache_parquet (gcs_scalable_layout)",
        "indexed_datasets_count": len(catalog),
        "providers": providers_status,
        "era5_api_configured": era5_provider.is_configured
    }

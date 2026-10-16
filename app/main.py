from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core.config import settings
from app.core.logging import setup_logging, logger
from app.core.exceptions import PlatformException, platform_exception_handler
from app.providers.registry import registry
from app.services.storage_service import storage_backend
from app.api.v1.router import api_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup sequence
    setup_logging()
    logger.info(f"Starting {settings.APP_NAME} in '{settings.ENVIRONMENT}' mode...")
    
    if settings.STORAGE_BACKEND == "gcs":
        logger.info(f"Storage uses gs://{settings.GCS_BUCKET_NAME}/provider/{{dataset_name}}/{{year}}/{{month}}")
    else:
        storage_backend.providers_dir.mkdir(parents=True, exist_ok=True)
        logger.info(f"Storage uses local files at {settings.storage_path.resolve()}")

    # Log available providers
    for p in registry.list_all():
        logger.info(f"Loaded Provider: [{p.id}] {p.name} - Status: {p.status} (Supported params: {len(p.supported_parameters)})")

    yield
    # Shutdown sequence
    logger.info(f"Shutting down {settings.APP_NAME}...")


app = FastAPI(
    title=settings.APP_NAME,
    description="Production-grade analytical data platform for multi-provider climate and weather reanalysis (ERA5 / ERA5-Land).",
    version="1.0.0",
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
)

# Exception handlers
app.add_exception_handler(PlatformException, platform_exception_handler)

# CORS Middleware
origins = settings.CORS_ORIGINS
if isinstance(origins, str):
    origins = [origins]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins if "*" not in origins else ["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Versioned API. Callers use /api/v1 only.
app.include_router(api_router, prefix="/api/v1")


@app.get("/")
def root():
    return {
        "service": settings.APP_NAME,
        "version": "1.0.0",
        "docs": "/docs",
        "health": "/api/v1/health",
        "providers": "/api/v1/providers",
        "era5_variables": "/api/v1/providers/era5/variables",
        "datasets": "/api/v1/datasets"
    }
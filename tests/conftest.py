import os
import shutil
import tempfile
import pytest
from pathlib import Path
from fastapi.testclient import TestClient

# Setup test environment variables BEFORE importing app
os.environ["ENVIRONMENT"] = "testing"
os.environ["DEBUG"] = "false"
os.environ["CDSAPI_KEY"] = ""
os.environ["ERA5_FALLBACK_SIMULATION"] = "true"

from app.core.config import settings as _settings
_settings.CDSAPI_KEY = ""
_settings.ERA5_FALLBACK_SIMULATION = True

# Create a temporary storage directory for tests
test_dir = tempfile.mkdtemp(prefix="test_parametric_storage_")
os.environ["STORAGE_DIR"] = test_dir

from app.core.config import settings
from app.services.storage_service import storage_backend, LocalScalableStorageBackend
from app.main import app


@pytest.fixture(scope="session", autouse=True)
def setup_test_environment():
    # Point storage backend to test directory
    storage_backend.root_dir = Path(test_dir)
    storage_backend.providers_dir = Path(test_dir) / "providers"
    storage_backend.providers_dir.mkdir(parents=True, exist_ok=True)
    storage_backend.catalog_file = Path(test_dir) / "catalog.json"
    storage_backend._init_catalog()

    yield

    shutil.rmtree(test_dir, ignore_errors=True)


@pytest.fixture
def client():
    with TestClient(app) as test_client:
        yield test_client

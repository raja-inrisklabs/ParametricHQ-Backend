import hashlib
import io
import json
import re
import tempfile
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple
import pandas as pd

from app.core.config import settings
from app.core.logging import logger
from app.core.exceptions import PlatformException
from app.services.object_store import object_store


class BaseStorageBackend(ABC):
    """
    Abstract interface for object/file storage.
    Enables seamless transition from local disk to Google Cloud Storage (GCS) or S3.
    """

    @abstractmethod
    def save_dataset(
        self,
        provider_id: str,
        parameter_type: str,
        data_key: str,
        df: pd.DataFrame,
        metadata: Dict[str, Any]
    ) -> Tuple[str, int, str]:
        """
        Saves DataFrame to Parquet and records metadata.
        Returns: (storage_path_uri, file_size_bytes, sha256_checksum)
        """
        pass

    @abstractmethod
    def read_dataset(self, data_key: str) -> pd.DataFrame:
        """Loads dataset Parquet file into pandas DataFrame."""
        pass

    @abstractmethod
    def dataset_exists(self, data_key: str) -> bool:
        """Checks if dataset exists in storage."""
        pass

    @abstractmethod
    def get_metadata(self, data_key: str) -> Optional[Dict[str, Any]]:
        """Retrieves metadata JSON for a dataset."""
        pass

    @abstractmethod
    def list_datasets(self, provider_id: Optional[str] = None, parameter_type: Optional[str] = None) -> List[Dict[str, Any]]:
        """Lists all registered datasets with metadata."""
        pass

    @abstractmethod
    def get_file_path_for_download(self, data_key: str) -> Path:
        """Returns local path or temporary path for streaming file downloads."""
        pass


class LocalScalableStorageBackend(BaseStorageBackend):
    """
    Dataset storage. The running API writes to the GCS bucket. Tests write to local files.
    Layout:
        catalog.json
        jobs.json
        provider/{dataset_name}/{year}/{month}/{data_key}.parquet
        provider/{dataset_name}/{year}/{month}/{data_key}.meta.json
    """

    def __init__(self, root_dir: Path = None):
        self.root_dir = root_dir or settings.storage_path
        self.providers_dir = self.root_dir / "provider"
        self.catalog_name = "catalog.json"
        if settings.STORAGE_BACKEND != "gcs":
            self.providers_dir.mkdir(parents=True, exist_ok=True)
            self.catalog_file = self.root_dir / self.catalog_name

    def _init_catalog(self):
        if not object_store.exists(self.catalog_name):
            self._write_catalog({})

    def _read_catalog(self) -> Dict[str, Any]:
        try:
            return object_store.read_json(self.catalog_name)
        except Exception as e:
            logger.warning(f"Error reading catalog.json, reinitializing: {e}")
            return {}

    def _write_catalog(self, catalog: Dict[str, Any]):
        object_store.write_json(self.catalog_name, catalog)

    def _object_name(self, provider_id: str, parameter_type: str, year: str, month: str, filename: str) -> str:
        folder = re.sub(r"[^a-z0-9]+", "_", parameter_type.strip().lower()).strip("_") or "dataset"
        return f"provider/{folder}/{year}/{month}/{filename}"

    def _period(self, metadata: Dict[str, Any]) -> Tuple[str, str]:
        start = str(metadata.get("start_date") or "2025-01-01")
        return start[:4], start[5:7] or "01"

    def _resolve_names(self, data_key: str) -> Tuple[Optional[str], Optional[str]]:
        entry = self._read_catalog().get(data_key)
        if not entry:
            return None, None
        return entry.get("object_name"), entry.get("metadata_object")

    def save_dataset(
        self,
        provider_id: str,
        parameter_type: str,
        data_key: str,
        df: pd.DataFrame,
        metadata: Dict[str, Any]
    ) -> Tuple[str, int, str]:
        year, month = self._period(metadata)
        object_name = self._object_name(provider_id, parameter_type, year, month, f"{data_key}.parquet")
        metadata_object = self._object_name(provider_id, parameter_type, year, month, f"{data_key}.meta.json")

        buffer = io.BytesIO()
        df.to_parquet(buffer, engine="pyarrow", compression="snappy", index=False)
        payload = buffer.getvalue()
        object_store.write_bytes(object_name, payload, "application/vnd.apache.parquet")

        file_size = len(payload)
        checksum = hashlib.sha256(payload).hexdigest()
        meta_payload = {
            **metadata,
            "data_key": data_key,
            "provider_id": provider_id,
            "parameter_type": parameter_type,
            "record_count": len(df),
            "file_size_bytes": file_size,
            "checksum_sha256": checksum,
            "format": "parquet",
            "compression": "snappy",
            "storage_path": object_store.uri(object_name),
            "metadata_path": object_store.uri(metadata_object),
            "gcs_blob_target": object_name,
            "object_name": object_name,
            "metadata_object": metadata_object,
            "year": year,
            "month": month,
            "created_at": pd.Timestamp.now("UTC").isoformat()
        }
        object_store.write_json(metadata_object, meta_payload)

        # 4. Update manifest catalog
        catalog = self._read_catalog()
        catalog[data_key] = meta_payload
        self._write_catalog(catalog)

        logger.info(f"Stored dataset '{data_key}' at {object_store.uri(object_name)} ({file_size} bytes)")
        return object_store.uri(object_name), file_size, checksum

    def read_dataset(self, data_key: str) -> pd.DataFrame:
        meta = self.get_metadata(data_key) or {}
        parts = meta.get("assembled_from") or []
        if parts:
            frames = [self._read_parquet(part) for part in parts]
            return pd.concat(frames, ignore_index=True)
        return self._read_parquet(data_key)

    def _read_parquet(self, data_key: str) -> pd.DataFrame:
        object_name, _ = self._resolve_names(data_key)
        if object_name and object_store.exists(object_name):
            return pd.read_parquet(io.BytesIO(object_store.read_bytes(object_name)), engine="pyarrow")
        entry = self._read_catalog().get(data_key) or {}
        storage_path = entry.get("storage_path")
        if storage_path and Path(storage_path).exists():
            return pd.read_parquet(storage_path, engine="pyarrow")
        raise PlatformException(f"Dataset '{data_key}' does not exist in storage.", status_code=404)

    def dataset_exists(self, data_key: str) -> bool:
        object_name, _ = self._resolve_names(data_key)
        if object_name and object_store.exists(object_name):
            return True
        # A catalog entry written before the object store still points at a local file.
        entry = self._read_catalog().get(data_key) or {}
        storage_path = entry.get("storage_path")
        return bool(storage_path and Path(storage_path).exists())

    def get_metadata(self, data_key: str) -> Optional[Dict[str, Any]]:
        catalog = self._read_catalog()
        if data_key in catalog:
            return catalog[data_key]

        _, metadata_object = self._resolve_names(data_key)
        if metadata_object and object_store.exists(metadata_object):
            return object_store.read_json(metadata_object)
        return None

    def list_datasets(self, provider_id: Optional[str] = None, parameter_type: Optional[str] = None) -> List[Dict[str, Any]]:
        catalog = self._read_catalog()
        results = [item for item in catalog.values() if isinstance(item, dict) and item.get("data_key")]

        if provider_id:
            results = [r for r in results if r.get("provider_id", "").lower() == provider_id.lower()]
        if parameter_type:
            results = [r for r in results if r.get("parameter_type", "").lower() == parameter_type.lower()]

        # Sort by creation date descending
        results.sort(key=lambda x: x.get("created_at", ""), reverse=True)
        return results

    def get_file_path_for_download(self, data_key: str) -> Path:
        object_name, _ = self._resolve_names(data_key)
        if not object_name or not object_store.exists(object_name):
            raise PlatformException(f"Dataset '{data_key}' file not found for download.", status_code=404)
        if settings.STORAGE_BACKEND != "gcs":
            return object_store.root / object_name
        temp = Path(tempfile.gettempdir()) / f"{data_key}.parquet"
        temp.write_bytes(object_store.read_bytes(object_name))
        return temp

    def read_dataset_view(self, data_key: str) -> pd.DataFrame:
        """Read one file, or assemble a request view from the files named in catalog.json."""
        meta = self.get_metadata(data_key) or {}
        parts = meta.get("assembled_from") or []
        if not parts:
            return self.read_dataset(data_key)
        frames = [self.read_dataset(part) for part in parts]
        return pd.concat(frames, ignore_index=True)

    def build_download_url(self, data_key: str) -> str:
        return f"{settings.API_PREFIX}/datasets/{data_key}/download"


storage_backend = LocalScalableStorageBackend()

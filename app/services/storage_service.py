import os
import json
import hashlib
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple
import pandas as pd

from app.core.config import settings
from app.core.logging import logger
from app.core.exceptions import PlatformException


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
    Local filesystem storage formatted to scale directly into Google Cloud Storage (GCS).
    Layout:
        storage/
        ├── catalog.json                                # Central manifest
        └── providers/
            └── {provider_id}/
                └── {parameter_type}/
                    └── {year}/
                        ├── {data_key}.parquet          # Apache Parquet data
                        └── {data_key}.meta.json        # Dataset metadata & schema
    This exact key layout maps 1:1 to GCS blob paths:
        gs://{bucket}/providers/{provider_id}/{parameter_type}/{year}/{data_key}.parquet
    """

    def __init__(self, root_dir: Path = None):
        self.root_dir = root_dir or settings.storage_path
        self.providers_dir = self.root_dir / "providers"
        self.providers_dir.mkdir(parents=True, exist_ok=True)
        self.catalog_file = self.root_dir / "catalog.json"
        self._init_catalog()

    def _init_catalog(self):
        if not self.catalog_file.exists():
            self._write_catalog({})

    def _read_catalog(self) -> Dict[str, Any]:
        if not self.catalog_file.exists():
            return {}
        try:
            with open(self.catalog_file, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            logger.warning(f"Error reading catalog.json, reinitializing: {e}")
            return {}

    def _write_catalog(self, catalog: Dict[str, Any]):
        with open(self.catalog_file, "w", encoding="utf-8") as f:
            json.dump(catalog, f, indent=2)

    def _build_storage_dir(self, provider_id: str, parameter_type: str, year: str) -> Path:
        target_dir = self.providers_dir / provider_id.lower() / parameter_type.lower() / str(year)
        target_dir.mkdir(parents=True, exist_ok=True)
        return target_dir

    def _resolve_paths(self, data_key: str) -> Tuple[Optional[Path], Optional[Path]]:
        catalog = self._read_catalog()
        entry = catalog.get(data_key)
        if not entry:
            # Fallback scan in providers dir
            matches = list(self.providers_dir.glob(f"**/{data_key}.parquet"))
            if matches:
                p_path = matches[0]
                m_path = p_path.with_suffix(".meta.json")
                return p_path, m_path if m_path.exists() else None
            return None, None

        p_path = Path(entry["storage_path"])
        m_path = Path(entry["metadata_path"])
        return p_path, m_path

    def save_dataset(
        self,
        provider_id: str,
        parameter_type: str,
        data_key: str,
        df: pd.DataFrame,
        metadata: Dict[str, Any]
    ) -> Tuple[str, int, str]:
        # Extract year for partitioning
        year = "2025"
        if "start_date" in metadata and metadata["start_date"]:
            year = metadata["start_date"][:4]

        folder = self._build_storage_dir(provider_id, parameter_type, year)
        parquet_path = folder / f"{data_key}.parquet"
        meta_path = folder / f"{data_key}.meta.json"

        # 1. Save Parquet with snappy compression (standard analytical format)
        df.to_parquet(parquet_path, engine="pyarrow", compression="snappy", index=False)

        # 2. Compute file size and sha256 checksum
        file_size = parquet_path.stat().st_size
        hasher = hashlib.sha256()
        with open(parquet_path, "rb") as f:
            while chunk := f.read(65536):
                hasher.update(chunk)
        checksum = hasher.hexdigest()

        # 3. Enrich metadata
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
            "storage_path": str(parquet_path),
            "metadata_path": str(meta_path),
            "gcs_blob_target": f"providers/{provider_id.lower()}/{parameter_type.lower()}/{year}/{data_key}.parquet",
            "created_at": pd.Timestamp.now("UTC").isoformat()
        }

        with open(meta_path, "w", encoding="utf-8") as f:
            json.dump(meta_payload, f, indent=2)

        # 4. Update manifest catalog
        catalog = self._read_catalog()
        catalog[data_key] = meta_payload
        self._write_catalog(catalog)

        logger.info(f"Stored dataset '{data_key}' in scalable format at {parquet_path} ({file_size} bytes)")
        return str(parquet_path), file_size, checksum

    def read_dataset(self, data_key: str) -> pd.DataFrame:
        p_path, _ = self._resolve_paths(data_key)
        if not p_path or not p_path.exists():
            raise PlatformException(f"Dataset '{data_key}' does not exist in storage.", status_code=404)
        return pd.read_parquet(p_path, engine="pyarrow")

    def dataset_exists(self, data_key: str) -> bool:
        p_path, _ = self._resolve_paths(data_key)
        return bool(p_path and p_path.exists())

    def get_metadata(self, data_key: str) -> Optional[Dict[str, Any]]:
        catalog = self._read_catalog()
        if data_key in catalog:
            return catalog[data_key]

        _, m_path = self._resolve_paths(data_key)
        if m_path and m_path.exists():
            with open(m_path, "r", encoding="utf-8") as f:
                return json.load(f)
        return None

    def list_datasets(self, provider_id: Optional[str] = None, parameter_type: Optional[str] = None) -> List[Dict[str, Any]]:
        catalog = self._read_catalog()
        results = list(catalog.values())

        if provider_id:
            results = [r for r in results if r.get("provider_id", "").lower() == provider_id.lower()]
        if parameter_type:
            results = [r for r in results if r.get("parameter_type", "").lower() == parameter_type.lower()]

        # Sort by creation date descending
        results.sort(key=lambda x: x.get("created_at", ""), reverse=True)
        return results

    def get_file_path_for_download(self, data_key: str) -> Path:
        p_path, _ = self._resolve_paths(data_key)
        if not p_path or not p_path.exists():
            raise PlatformException(f"Dataset '{data_key}' file not found for download.", status_code=404)
        return p_path

    def build_download_url(self, data_key: str) -> str:
        return f"{settings.API_PREFIX}/datasets/{data_key}/download"


# Active storage backend instance (Local scalable, ready to be swapped for GCS)
storage_backend = LocalScalableStorageBackend()

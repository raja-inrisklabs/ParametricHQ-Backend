"""Object storage used by the catalog, jobs, and dataset files.

The running API uses the GCS bucket. Tests use the local filesystem so they do not
need cloud credentials.
"""
import json
from pathlib import Path
from typing import Optional

from app.core.config import settings


class ObjectStore:
    def __init__(self) -> None:
        self.backend = settings.STORAGE_BACKEND
        self.root = settings.storage_path
        self.bucket = None
        if self.backend == "gcs":
            from google.cloud import storage

            self.client = storage.Client(project=settings.GOOGLE_CLOUD_PROJECT)
            self.bucket = self.client.bucket(settings.GCS_BUCKET_NAME)

    def read_text(self, name: str) -> Optional[str]:
        if self.bucket is not None:
            blob = self.bucket.blob(name)
            try:
                if not blob.exists():
                    return None
                return blob.download_as_text(encoding="utf-8")
            except Exception:
                return None
        path = self.root / name
        if not path.exists():
            return None
        return path.read_text(encoding="utf-8")

    def write_text(self, name: str, text: str) -> None:
        if self.bucket is not None:
            self.bucket.blob(name).upload_from_string(text, content_type="application/json")
            return
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    def read_json(self, name: str) -> dict:
        text = self.read_text(name)
        if not text:
            return {}
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return {}

    def write_json(self, name: str, payload: dict) -> None:
        self.write_text(name, json.dumps(payload, indent=2))

    def exists(self, name: str) -> bool:
        try:
            if self.bucket is not None:
                return self.bucket.blob(name).exists()
            return (self.root / name).exists()
        except Exception:
            return False

    def write_bytes(self, name: str, data: bytes, content_type: str) -> None:
        if self.bucket is not None:
            self.bucket.blob(name).upload_from_string(data, content_type=content_type)
            return
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)

    def read_bytes(self, name: str) -> bytes:
        if self.bucket is not None:
            blob = self.bucket.blob(name)
            if not blob.exists():
                raise FileNotFoundError(name)
            return blob.download_as_bytes()
        path = self.root / name
        if not path.exists():
            raise FileNotFoundError(name)
        return path.read_bytes()

    def size(self, name: str) -> int:
        if self.bucket is not None:
            blob = self.bucket.blob(name)
            blob.reload()
            return int(blob.size or 0)
        return (self.root / name).stat().st_size

    def uri(self, name: str) -> str:
        if self.bucket is not None:
            return f"gs://{settings.GCS_BUCKET_NAME}/{name}"
        return str((self.root / name).resolve())


object_store = ObjectStore()

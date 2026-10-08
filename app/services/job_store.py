"""File-backed fetch jobs so a long CDS download does not hold the HTTP request."""
import json
import threading
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from app.core.config import settings


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class JobStore:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.path = settings.storage_path / "jobs.json"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not self.path.exists():
            self.path.write_text("{}", encoding="utf-8")

    def _read(self) -> Dict[str, Any]:
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}

    def _write(self, jobs: Dict[str, Any]) -> None:
        import time
        tmp = self.path.with_suffix(".json.tmp")
        payload = json.dumps(jobs, indent=2)
        tmp.write_text(payload, encoding="utf-8")
        for attempt in range(5):
            try:
                tmp.replace(self.path)
                return
            except PermissionError:
                if attempt == 4:
                    self.path.write_text(payload, encoding="utf-8")
                    return
                time.sleep(0.05)

    def get(self, job_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            return self._read().get(job_id)

    def get_active_for_key(self, data_key: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            for job in self._read().values():
                if job.get("data_key") == data_key and job.get("status") in ("accepted", "running"):
                    return job
        return None

    def create(self, job_id: str, data_key: str, provider_id: str) -> Dict[str, Any]:
        job = {
            "job_id": job_id,
            "data_key": data_key,
            "provider_id": provider_id,
            "status": "accepted",
            "detail": "Request accepted. Waiting to fetch from CDS.",
            "created_at": _now(),
            "updated_at": _now(),
            "error": None,
            "result": None,
        }
        with self._lock:
            jobs = self._read()
            jobs[job_id] = job
            self._write(jobs)
        return job

    def update(self, job_id: str, **fields: Any) -> Optional[Dict[str, Any]]:
        with self._lock:
            jobs = self._read()
            job = jobs.get(job_id)
            if not job:
                return None
            job.update(fields)
            job["updated_at"] = _now()
            jobs[job_id] = job
            self._write(jobs)
            return job


job_store = JobStore()

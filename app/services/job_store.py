"""File-backed fetch jobs so a long CDS download does not hold the HTTP request."""
import threading
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from app.services.object_store import object_store


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class JobStore:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.name = "jobs.json"

    def _read(self) -> Dict[str, Any]:
        return object_store.read_json(self.name)

    def _write(self, jobs: Dict[str, Any]) -> None:
        object_store.write_json(self.name, jobs)

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

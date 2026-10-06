import re
import threading
import uuid
from typing import Optional, Dict, Any, Tuple, List

from app.core.logging import logger
from app.core.exceptions import DatasetNotFoundError, ProviderError
from app.schemas.dataset import DatasetRequest, DatasetResponse
from app.providers.registry import registry
from app.services.job_store import job_store
from app.services.storage_service import storage_backend
from app.services.geocoding_service import GeocodingService


class DatasetService:
    """
    Coordinates data fetching across multiple providers (ERA5, ERA5-Land, etc.),
    deduplication, scalable Parquet storage, and metadata cataloging without requiring PostgreSQL.
    """

    @staticmethod
    def generate_data_key(req: DatasetRequest) -> str:
        """
        Creates a deterministic identifier for caching and deduplication.
        Format: {provider}_{param(s)}_{location}_{start_date}_{end_date}_{resolution}_{version}
        """
        loc_clean = re.sub(r"[^a-zA-Z0-9_-]", "_", req.location.strip().lower())
        provider_clean = req.provider_id.strip().lower()
        res_clean = (req.resolution or "0.1deg").replace(".", "_")
        time_clean = (req.temporal_resolution or "hourly").strip().lower()
        ver_clean = (req.version or "v1").lower()

        requested_params = sorted([p.strip().lower() for p in req.requested_parameters])
        if len(requested_params) == 1:
            param_clean = re.sub(r"[^a-zA-Z0-9_-]", "_", requested_params[0])
        else:
            joined = "_".join(re.sub(r"[^a-zA-Z0-9_-]", "_", p) for p in requested_params)
            if len(joined) > 40:
                import hashlib
                hash_slug = hashlib.md5(joined.encode()).hexdigest()[:8]
                param_clean = f"multi_{len(requested_params)}vars_{hash_slug}"
            else:
                param_clean = f"multi_{joined}"

        return f"{provider_clean}_{param_clean}_{loc_clean}_{req.start_date}_{req.end_date}_{res_clean}_{time_clean}_{ver_clean}"

    def _ready_from_cache(self, data_key: str, requested_vars: List[str]) -> DatasetResponse:
        meta = storage_backend.get_metadata(data_key) or {}
        return DatasetResponse(
            status="ready",
            cached=True,
            download_url=storage_backend.build_download_url(data_key),
            data_key=data_key,
            record_count=meta.get("record_count", 0),
            file_size_bytes=meta.get("file_size_bytes", 0),
            parameters=meta.get("parameters", requested_vars),
            summary_stats=meta.get("summary"),
            detail="Served directly from central repository (cache hit).",
        )

    def submit_request(self, req: DatasetRequest) -> Tuple[DatasetResponse, int]:
        """Accept a fetch immediately. CDS work runs in the background."""
        data_key = self.generate_data_key(req)
        requested_vars = req.requested_parameters
        logger.info(f"Accepting request for key='{data_key}' (provider={req.provider_id}, params={requested_vars})")

        if storage_backend.dataset_exists(data_key):
            return self._ready_from_cache(data_key, requested_vars), 200

        active = job_store.get_active_for_key(data_key)
        if active:
            return DatasetResponse(
                status=active["status"],
                job_id=active["job_id"],
                data_key=data_key,
                detail=active.get("detail") or "Request is already running.",
            ), 202

        job_id = uuid.uuid4().hex
        job_store.create(job_id, data_key, req.provider_id)
        threading.Thread(
            target=self._run_job,
            args=(job_id, req),
            name=f"dataset-{job_id[:8]}",
            daemon=True,
        ).start()
        return DatasetResponse(
            status="accepted",
            job_id=job_id,
            data_key=data_key,
            detail="Request accepted. Fetch will start shortly.",
        ), 202

    def get_job(self, job_id: str) -> Tuple[DatasetResponse, int]:
        job = job_store.get(job_id)
        if not job:
            raise DatasetNotFoundError(job_id)

        status = job["status"]
        if status in ("accepted", "running"):
            return DatasetResponse(
                status=status,
                job_id=job_id,
                data_key=job.get("data_key"),
                detail=job.get("detail"),
            ), 202

        if status == "failed":
            return DatasetResponse(
                status="failed",
                job_id=job_id,
                data_key=job.get("data_key"),
                detail=job.get("error") or job.get("detail") or "Fetch failed.",
            ), 200

        result = job.get("result") or {}
        return DatasetResponse(
            status="ready",
            job_id=job_id,
            cached=False,
            download_url=result.get("download_url"),
            data_key=job.get("data_key"),
            record_count=result.get("record_count"),
            file_size_bytes=result.get("file_size_bytes"),
            parameters=result.get("parameters"),
            summary_stats=result.get("summary_stats"),
            detail=job.get("detail") or "Fetched from provider and stored in central repository.",
        ), 200

    def _run_job(self, job_id: str, req: DatasetRequest) -> None:
        data_key = self.generate_data_key(req)
        try:
            job_store.update(
                job_id,
                status="running",
                detail="Fetching from Copernicus CDS.",
            )
            response = self._fetch_and_store(req, data_key)
            job_store.update(
                job_id,
                status="ready",
                detail=response.detail,
                result=response.model_dump(mode="json"),
            )
        except Exception as exc:
            logger.exception(f"Dataset job {job_id} failed")
            job_store.update(
                job_id,
                status="failed",
                detail="Fetch failed.",
                error=str(exc),
            )

    def process_request(self, req: DatasetRequest) -> Tuple[DatasetResponse, int]:
        """
        Synchronous fetch used by tests and callers that can wait.
        HTTP /request uses submit_request so the browser is not held open.
        """
        data_key = self.generate_data_key(req)
        requested_vars = req.requested_parameters
        logger.info(f"Processing request for key='{data_key}' (provider={req.provider_id}, params={requested_vars})")

        if storage_backend.dataset_exists(data_key):
            logger.info(f"Cache HIT for dataset '{data_key}'")
            return self._ready_from_cache(data_key, requested_vars), 200

        logger.info(f"Cache MISS for dataset '{data_key}'. Querying provider '{req.provider_id}'...")
        return self._fetch_and_store(req, data_key), 200

    def _fetch_and_store(self, req: DatasetRequest, data_key: str) -> DatasetResponse:
        requested_vars = req.requested_parameters

        # Resolve location coordinates
        lat, lon = GeocodingService.resolve_coordinates(
            req.location,
            lat_override=req.latitude,
            lon_override=req.longitude
        )

        provider = registry.get(req.provider_id)
        try:
            df = provider.fetch_data(req, latitude=lat, longitude=lon)
        except ProviderError:
            raise
        except Exception as exc:
            raise ProviderError(req.provider_id, str(exc)) from exc

        # Build summary statistics
        summary = {
            "total_records": len(df),
            "start": str(df["timestamp"].min()) if not df.empty else None,
            "end": str(df["timestamp"].max()) if not df.empty else None,
        }

        # If single variable with 'value' column
        if "value" in df.columns:
            summary.update({
                "mean": round(float(df["value"].mean()), 3) if not df.empty else 0.0,
                "min": round(float(df["value"].min()), 3) if not df.empty else 0.0,
                "max": round(float(df["value"].max()), 3) if not df.empty else 0.0,
                "sum": round(float(df["value"].sum()), 3) if not df.empty else 0.0,
                "unit": str(df["unit"].iloc[0]) if not df.empty and "unit" in df.columns else "",
            })

        # Summary for all variable columns in df
        var_summaries = {}
        for col in df.columns:
            if col not in ["timestamp", "latitude", "longitude", "value", "unit", "parameter"]:
                s = df[col].dropna()
                var_summaries[col] = {
                    "mean": round(float(s.mean()), 3) if not s.empty else 0.0,
                    "min": round(float(s.min()), 3) if not s.empty else 0.0,
                    "max": round(float(s.max()), 3) if not s.empty else 0.0,
                    "sum": round(float(s.sum()), 3) if not s.empty else 0.0,
                }
        summary["variable_stats"] = var_summaries

        primary_param = requested_vars[0] if len(requested_vars) == 1 else "multi_parameter"
        metadata = {
            "location": req.location,
            "latitude": lat,
            "longitude": lon,
            "start_date": req.start_date,
            "end_date": req.end_date,
            "resolution": req.resolution or provider.default_resolution,
            "version": req.version or "v1",
            "parameters": requested_vars,
            "summary": summary,
            "provider_name": provider.name,
            "provider_is_configured": provider.is_configured,
        }

        # 3. Store in scalable Parquet format
        storage_path, file_size, checksum = storage_backend.save_dataset(
            provider_id=req.provider_id,
            parameter_type=primary_param,
            data_key=data_key,
            df=df,
            metadata=metadata
        )

        download_url = storage_backend.build_download_url(data_key)

        return DatasetResponse(
            status="ready",
            cached=False,
            download_url=download_url,
            data_key=data_key,
            record_count=len(df),
            file_size_bytes=file_size,
            parameters=requested_vars,
            summary_stats=summary,
            detail="Fetched from provider and stored in central repository."
        )

    def get_dataset_metadata(self, data_key: str) -> Dict[str, Any]:
        meta = storage_backend.get_metadata(data_key)
        if not meta:
            raise DatasetNotFoundError(data_key)
        meta["download_url"] = storage_backend.build_download_url(data_key)
        meta["analysis_url"] = f"/api/datasets/{data_key}/analysis"
        return meta

    def list_datasets(self, provider_id: Optional[str] = None, parameter_type: Optional[str] = None) -> List[Dict[str, Any]]:
        datasets = storage_backend.list_datasets(provider_id=provider_id, parameter_type=parameter_type)
        for d in datasets:
            d["download_url"] = storage_backend.build_download_url(d["data_key"])
            d["analysis_url"] = f"/api/datasets/{d['data_key']}/analysis"
        return datasets


dataset_service = DatasetService()

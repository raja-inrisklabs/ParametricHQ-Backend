import re
import threading
import uuid
from typing import Optional, Dict, Any, Tuple, List
import pandas as pd

from app.core.config import settings
from app.core.logging import logger
from app.core.exceptions import DatasetNotFoundError, ProviderError
from app.schemas.dataset import DatasetRequest, DatasetResponse
from app.providers.datasets import get_dataset
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
        if req.dataset_id:
            dataset_clean = re.sub(r"[^a-zA-Z0-9_-]", "_", req.dataset_id.strip().lower())
            if req.is_point():
                place_clean = "lat{0}_lon{1}".format(
                    *[("%g" % value).replace("-", "m").replace(".", "p") for value in req.snapped_point()]
                )
            else:
                area = req.cds_area()
                place_clean = "n{0}_w{1}_s{2}_e{3}".format(
                    *[("%g" % value).replace("-", "m").replace(".", "p") for value in area]
                )
            selected = sorted(name.strip().lower() for name in req.parameters or [])
            variable_clean = "all" if not selected else "vars_" + "_".join(selected)
            end = req.end_date if req.end_date != req.start_date else req.start_date
            span = req.start_date if end == req.start_date else f"{req.start_date}_{end}"
            return f"{provider_clean}_{dataset_clean}_{place_clean}_{variable_clean}_{span}_hourly"

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

        if req.is_point():
            extracted = self._extract_point_from_stored_grid(req, data_key, self._catalog_variables(req))
            if extracted:
                return extracted, 200

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

    def _catalog_variables(self, req: DatasetRequest) -> List[str]:
        if not req.dataset_id:
            return req.requested_parameters
        catalog = get_dataset(req.dataset_id)
        available = list(catalog.get("hourly_variables") or catalog["variables"])
        selected = [name for name in (req.parameters or []) if name in available]
        return selected or available

    @staticmethod
    def _covers_point(entry: Dict[str, Any], lat: float, lon: float, step: float) -> bool:
        area = entry.get("area") or []
        if len(area) != 4:
            return False
        north, west, south, east = [float(value) for value in area]
        # A single stored cell is not a source box for another point.
        if (north - south) <= step + 0.01 and (east - west) <= step + 0.01:
            return False
        return south <= lat <= north and west <= lon <= east

    @staticmethod
    def _covers_dates(entry: Dict[str, Any], start_date: str, end_date: str) -> bool:
        stored_start = entry.get("start_date")
        stored_end = entry.get("end_date") or stored_start
        return bool(stored_start and stored_end and stored_start <= start_date and end_date <= stored_end)

    def _extract_point_from_stored_grid(
        self,
        req: DatasetRequest,
        data_key: str,
        requested_vars: List[str],
    ) -> Optional[DatasetResponse]:
        """Reuse a stored box by coverage. The point keeps its own file name."""
        lat, lon = req.snapped_point()
        needed = set(requested_vars)
        matches = []
        for entry in storage_backend._read_catalog().values():
            if entry.get("dataset_id") != req.dataset_id:
                continue
            if not self._covers_dates(entry, req.start_date, req.end_date):
                continue
            stored = set(entry.get("parameters") or [])
            if needed and not needed <= stored:
                continue
            if not self._covers_point(entry, lat, lon, req.grid_step()):
                continue
            matches.append(entry)
        if not matches:
            logger.info(f"No stored grid covers {lat}, {lon} for {req.start_date} to {req.end_date}")
            return None

        # Prefer the smallest box that already has the cell, so a tight fetch is reused first.
        matches.sort(key=lambda entry: (entry["area"][0] - entry["area"][2]) * (entry["area"][3] - entry["area"][1]))
        source = matches[0]
        source_key = source.get("data_key")
        try:
            frame = storage_backend.read_dataset(source_key)
        except Exception as exc:
            logger.warning(f"Could not read stored grid {source_key}: {exc}")
            return None

        stamps = pd.to_datetime(frame["timestamp"])
        start = pd.Timestamp(req.start_date)
        end = pd.Timestamp(req.end_date) + pd.Timedelta(days=1)
        frame = frame[(stamps >= start) & (stamps < end)]
        nearest = frame.assign(
            _distance=(frame["latitude"] - lat).abs() + (frame["longitude"] - lon).abs()
        )
        if nearest.empty:
            return None
        best = nearest["_distance"].min()
        # The stored cell must be the requested dataset's own grid cell.
        if best > req.grid_step() / 2 + 0.001:
            logger.info(f"Stored grid {source_key} has no {req.grid_step()} degree cell at {lat}, {lon}")
            return None
        cell = nearest[nearest["_distance"] == best].drop(columns="_distance")
        if needed and "variable" in cell.columns:
            cell = cell[cell["variable"].isin(needed)]
        if cell.empty:
            return None

        cell = cell.copy()
        cell["latitude"] = lat
        cell["longitude"] = lon
        self._store_frame(req, data_key, cell.reset_index(drop=True), requested_vars, lat, lon, source_key)
        response = self._ready_from_cache(data_key, requested_vars)
        response.detail = f"Taken from stored grid {source_key} at {lat}, {lon}."
        logger.info(response.detail)
        return response

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
        if req.dataset_id:
            catalog = get_dataset(req.dataset_id)
            available = set(catalog.get("hourly_variables") or catalog["variables"])
            unknown = [name for name in (req.parameters or []) if name not in available]
            if unknown:
                raise ValueError(f"Unknown variables for {req.dataset_id}: {', '.join(unknown)}")
        requested_vars = self._catalog_variables(req)

        # Resolve location coordinates
        lat, lon = GeocodingService.resolve_coordinates(
            req.location,
            lat_override=req.latitude,
            lon_override=req.longitude
        )
        if req.is_point() and req.dataset_id:
            reused = self._extract_point_from_stored_grid(req, data_key, requested_vars)
            if reused:
                return reused
            lat, lon = req.snapped_point()

        provider = registry.get(req.provider_id)
        try:
            df = provider.fetch_data(req, latitude=lat, longitude=lon)
        except ProviderError:
            raise
        except Exception as exc:
            raise ProviderError(req.provider_id, str(exc)) from exc

        stored = self._store_frame(req, data_key, df, requested_vars, lat, lon)
        stored.cached = False
        stored.detail = "Fetched from provider and stored in central repository."
        return stored

    def _store_frame(
        self,
        req: DatasetRequest,
        data_key: str,
        df: pd.DataFrame,
        requested_vars: List[str],
        lat: float,
        lon: float,
        source_key: Optional[str] = None,
    ) -> DatasetResponse:
        provider = registry.get(req.provider_id)
        summary = {
            "total_records": len(df),
            "start": str(df["timestamp"].min()) if not df.empty else None,
            "end": str(df["timestamp"].max()) if not df.empty else None,
            "source_grid": source_key,
        }
        if "value" in df.columns and not df.empty:
            summary.update({
                "mean": round(float(df["value"].mean()), 3),
                "min": round(float(df["value"].min()), 3),
                "max": round(float(df["value"].max()), 3),
                "sum": round(float(df["value"].sum()), 3),
                "unit": str(df["unit"].iloc[0]) if "unit" in df.columns else "",
            })
        metadata = {
            "location": req.location,
            "latitude": lat,
            "longitude": lon,
            "area": (
                [round(lat + req.grid_step() / 2, 2), round(lon - req.grid_step() / 2, 2), round(lat - req.grid_step() / 2, 2), round(lon + req.grid_step() / 2, 2)]
                if req.is_point()
                else (list(req.cds_area()) if req.dataset_id else None)
            ),
            "grid_points": int(df.groupby(["latitude", "longitude"]).ngroups) if {"latitude", "longitude"} <= set(df.columns) else 1,
            "start_date": req.start_date,
            "end_date": req.end_date,
            "dataset_id": req.dataset_id,
            "resolution": f"{req.grid_step()}deg" if req.dataset_id else (req.resolution or "0.1deg"),
            "temporal_resolution": "hourly",
            "version": req.version or "v1",
            "parameters": requested_vars,
            "summary": summary,
            "provider_name": provider.name,
            "source_grid": source_key,
        }
        storage_backend.save_dataset(
            provider_id=req.provider_id,
            parameter_type=req.dataset_id or requested_vars[0],
            data_key=data_key,
            df=df,
            metadata=metadata,
        )
        return self._ready_from_cache(data_key, requested_vars)

    def get_dataset_metadata(self, data_key: str) -> Dict[str, Any]:
        meta = storage_backend.get_metadata(data_key)
        if not meta:
            raise DatasetNotFoundError(data_key)
        meta["download_url"] = storage_backend.build_download_url(data_key)
        meta["analysis_url"] = f"{settings.API_PREFIX}/datasets/{data_key}/analysis"
        return meta

    def list_datasets(self, provider_id: Optional[str] = None, parameter_type: Optional[str] = None) -> List[Dict[str, Any]]:
        datasets = storage_backend.list_datasets(provider_id=provider_id, parameter_type=parameter_type)
        for d in datasets:
            d["download_url"] = storage_backend.build_download_url(d["data_key"])
            d["analysis_url"] = f"{settings.API_PREFIX}/datasets/{d['data_key']}/analysis"
        return datasets


dataset_service = DatasetService()

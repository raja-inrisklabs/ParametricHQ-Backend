from typing import List, Optional, Dict, Any
from fastapi import APIRouter, Query, Response
from fastapi.responses import FileResponse

from app.core.exceptions import DatasetNotFoundError
from app.schemas.dataset import (
    DatasetRequest,
    DatasetResponse,
    DatasetDataPreview
)
from app.providers.datasets import list_dataset_variables, list_datasets as list_catalog_datasets
from app.services.dataset_service import dataset_service
from app.services.storage_service import storage_backend
from app.services.analysis_service import analysis_service

router = APIRouter(prefix="/datasets", tags=["Datasets"])


@router.get("/catalog")
def list_dataset_catalog(provider_id: Optional[str] = Query(None)):
    """Datasets the user can select: ERA5-Land and ERA5 single levels."""
    return list_catalog_datasets(provider_id)


@router.get("/catalog/{dataset_id}/variables")
def list_catalog_variables(dataset_id: str):
    """Hourly variables the user can select for a dataset."""
    try:
        return list_dataset_variables(dataset_id)
    except KeyError as exc:
        raise DatasetNotFoundError(dataset_id) from exc


@router.post("/request", response_model=DatasetResponse)
def request_dataset(
    req: DatasetRequest,
    response: Response
):
    """
    Request a climate/environmental parameter dataset.
    - Cache hit returns the stored dataset immediately (HTTP 200, status=ready).
    - Cache miss accepts the job (HTTP 202, status=accepted) and fetches CDS in the background.
      Poll GET /datasets/jobs/{job_id} while status is accepted or running.
    """
    res, status_code = dataset_service.submit_request(req)
    response.status_code = status_code
    return res


@router.get("/jobs/{job_id}", response_model=DatasetResponse)
def get_dataset_job(job_id: str, response: Response):
    """
    Poll a background fetch.
    HTTP 202 while status is accepted or running. HTTP 200 when ready or failed.
    """
    res, status_code = dataset_service.get_job(job_id)
    response.status_code = status_code
    return res


@router.get("", response_model=List[Dict[str, Any]])
def list_datasets(
    provider_id: Optional[str] = Query(None, description="Filter by provider ID"),
    parameter_type: Optional[str] = Query(None, description="Filter by parameter type")
):
    """
    List all stored datasets in the platform repository.
    """
    return dataset_service.list_datasets(provider_id=provider_id, parameter_type=parameter_type)


@router.get("/{data_key}", response_model=Dict[str, Any])
def get_dataset_metadata(data_key: str):
    """
    Get metadata for a specific dataset by key.
    """
    return dataset_service.get_dataset_metadata(data_key)


@router.get("/{data_key}/download")
def download_dataset(data_key: str):
    """
    Download the raw analytical Apache Parquet file for a dataset.
    """
    file_path = storage_backend.get_file_path_for_download(data_key)
    filename = f"{data_key}.parquet"
    return FileResponse(
        path=str(file_path),
        media_type="application/vnd.apache.parquet",
        filename=filename,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'}
    )


@router.get("/{data_key}/data", response_model=DatasetDataPreview)
def preview_dataset_data(
    data_key: str,
    limit: int = Query(100, ge=1, le=1000)
):
    """
    Preview time-series rows and summary statistics from the stored Parquet file.
    """
    df = storage_backend.read_dataset_view(data_key)
    meta = storage_backend.get_metadata(data_key) or {}

    var_cols = [
        c for c in df.columns
        if c not in ["timestamp", "dt", "latitude", "longitude", "dataset", "value", "unit", "parameter"]
    ]
    if "variable" in df.columns and "value" in df.columns:
        var_cols = ["value"]
    elif not var_cols and "value" in df.columns:
        var_cols = ["value"]

    summary = {
        "total_records": len(df),
        "columns": list(df.columns),
    }
    if "value" in df.columns:
        summary.update({
            "mean_value": round(float(df["value"].mean()), 3) if not df.empty else 0.0,
            "min_value": round(float(df["value"].min()), 3) if not df.empty else 0.0,
            "max_value": round(float(df["value"].max()), 3) if not df.empty else 0.0,
            "total_sum": round(float(df["value"].sum()), 3) if not df.empty else 0.0,
        })

    units = meta.get("units", {})
    if not units and "unit" in df.columns and not df.empty:
        units = {var_cols[0]: str(df["unit"].iloc[0])}

    sample_records = df.head(limit).to_dict(orient="records")

    return DatasetDataPreview(
        data_key=data_key,
        parameter_type=meta.get("parameter_type", "multi_parameter"),
        parameters=meta.get("parameters", var_cols),
        units=units,
        total_records=len(df),
        sample_records=sample_records,
        summary=summary,
    )


@router.get("/{data_key}/analysis")
def analyze_dataset(data_key: str) -> Dict[str, Any]:
    """
    Generate deep statistical and temporal analysis for a fetched dataset.
    Includes percentiles, monthly aggregations, extreme events, and distribution bins.
    """
    return analysis_service.analyze_dataset(data_key)

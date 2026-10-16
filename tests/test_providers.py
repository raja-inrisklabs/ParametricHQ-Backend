import logging

import pandas as pd
import pytest
from app.providers.registry import registry
from app.providers.era5 import ERA5Provider, _CdsStatusLog
from app.schemas.dataset import DatasetRequest
from app.core.exceptions import ProviderNotFoundError, ProviderError
from app.core.logging import _QuietJobPollFilter


def test_provider_registry_contains_era5():
    assert registry.has("era5")
    era5 = registry.get("era5")
    assert isinstance(era5, ERA5Provider)
    # Check key ERA5-Land variables
    assert "total_precipitation" in era5.supported_parameters
    assert "2m_temperature" in era5.supported_parameters
    assert "volumetric_soil_water_layer_1" in era5.supported_parameters
    assert "10m_wind_speed" in era5.supported_parameters
    # Check friendly aliases
    assert "rainfall" in era5.supported_parameters
    assert "soil_moisture" in era5.supported_parameters


def test_same_year_range_is_one_cds_call():
    dates = list(pd.date_range("2024-02-01", "2024-08-15", freq="D").to_pydatetime())
    chunks = ERA5Provider._year_chunks(dates)
    assert len(chunks) == 1
    assert chunks[0][0].date().isoformat() == "2024-02-01"
    assert chunks[0][-1].date().isoformat() == "2024-08-15"


def test_cross_year_range_is_one_call_per_year():
    dates = list(pd.date_range("2023-02-01", "2024-02-01", freq="D").to_pydatetime())
    chunks = ERA5Provider._year_chunks(dates)
    assert [chunk[0].year for chunk in chunks] == [2023, 2024]
    assert chunks[0][-1].date().isoformat() == "2023-12-31"
    assert chunks[1][0].date().isoformat() == "2024-01-01"
    assert chunks[1][-1].date().isoformat() == "2024-02-01"


def test_era5_variable_catalog():
    era5 = registry.get("era5")
    catalog = era5.get_variable_catalog()
    assert "variables" in catalog
    assert "aliases" in catalog
    assert len(catalog["variables"]) >= 15
    assert "volumetric_soil_water_layer_1" in catalog["variables"]
    assert catalog["variables"]["volumetric_soil_water_layer_1"]["unit"] == "m3/m3"


def test_era5_data_generation_rainfall():
    era5 = registry.get("era5")
    req = DatasetRequest(
        provider_id="era5",
        parameter_type="rainfall",
        location="gujarat/ahmedabad",
        start_date="2025-06-01",
        end_date="2025-06-30",
        resolution="0.1deg",
        version="v1"
    )
    df = era5.fetch_data(req, latitude=23.0225, longitude=72.5714)
    
    assert len(df) == 30
    assert "timestamp" in df.columns
    assert "value" in df.columns
    assert "unit" in df.columns
    assert df["unit"].iloc[0] == "mm"
    assert df["parameter"].iloc[0] == "total_precipitation"
    assert (df["value"] >= 0).all()


def test_era5_soil_moisture_layers():
    era5 = registry.get("era5")
    req = DatasetRequest(
        provider_id="era5",
        parameter_type="volumetric_soil_water_layer_1",
        location="gujarat/ahmedabad",
        start_date="2025-01-01",
        end_date="2025-01-15",
    )
    df = era5.fetch_data(req, latitude=23.0225, longitude=72.5714)
    assert len(df) == 15
    assert df["unit"].iloc[0] == "m3/m3"
    # Volumetric water content should be within physical range (0.05 to 0.50 m3/m3)
    assert (df["value"] > 0.0).all() and (df["value"] < 0.6).all()


def test_providers_api_endpoints(client):
    res_list = client.get("/api/v1/providers")
    assert res_list.status_code == 200
    providers = res_list.json()
    assert any(p["id"] == "era5" for p in providers)

    res_vars = client.get("/api/v1/providers/era5/variables")
    assert res_vars.status_code == 200
    catalog = res_vars.json()
    assert "variables" in catalog
    assert "total_evaporation" in catalog["variables"]


def test_cds_status_is_logged_once_per_state(caplog):
    status_log = _CdsStatusLog()
    with caplog.at_level(logging.INFO, logger="parametric_platform"):
        for _ in range(5):
            status_log(logging.INFO, "Request is accepted")
        status_log(logging.INFO, "Request is running")
        status_log(logging.INFO, "Request is running")
        status_log(logging.INFO, "Request is successful")

    messages = [record.message for record in caplog.records]
    assert messages == [
        "Request is accepted",
        "Request is running",
        "Request is successful",
    ]


def test_job_poll_access_lines_are_quiet():
    poll_filter = _QuietJobPollFilter()
    accepted = logging.LogRecord(
        "uvicorn.access", logging.INFO, __file__, 1,
        '127.0.0.1:1 - "GET /api/v1/datasets/jobs/abc HTTP/1.1" 202',
        (), None,
    )
    finished = logging.LogRecord(
        "uvicorn.access", logging.INFO, __file__, 1,
        '127.0.0.1:1 - "GET /api/v1/datasets/jobs/abc HTTP/1.1" 200',
        (), None,
    )
    request = logging.LogRecord(
        "uvicorn.access", logging.INFO, __file__, 1,
        '127.0.0.1:1 - "POST /api/v1/datasets/request HTTP/1.1" 202',
        (), None,
    )
    assert poll_filter.filter(accepted) is False
    assert poll_filter.filter(finished) is True
    assert poll_filter.filter(request) is True

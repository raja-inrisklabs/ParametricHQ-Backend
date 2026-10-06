def _wait_for_job(client, payload):
    accepted = client.post("/api/datasets/request", json=payload)
    if accepted.status_code == 200:
        return accepted
    assert accepted.status_code == 202
    body = accepted.json()
    assert body["job_id"]
    final = accepted
    for _ in range(80):
        final = client.get(f"/api/datasets/jobs/{body['job_id']}")
        status = final.json().get("status")
        if status in ("ready", "failed"):
            return final
        __import__("time").sleep(0.05)
    return final


def test_request_is_accepted_then_polled_until_ready(client):
    payload = {
        "provider_id": "era5",
        "parameter_type": "total_precipitation",
        "location": "gujarat/ahmedabad",
        "start_date": "2024-01-01",
        "end_date": "2024-01-02",
        "resolution": "0.1deg",
        "version": "v1",
    }

    accepted = client.post("/api/datasets/request", json=payload)
    assert accepted.status_code == 202
    body = accepted.json()
    assert body["status"] == "accepted"
    assert body["job_id"]

    seen_running = False
    final = None
    for _ in range(40):
        polled = client.get(f"/api/datasets/jobs/{body['job_id']}")
        final = polled.json()
        if final["status"] == "running":
            seen_running = True
            assert polled.status_code == 202
        if final["status"] in ("ready", "failed"):
            break
        __import__("time").sleep(0.05)

    assert seen_running or final["status"] == "ready"
    assert final["status"] == "ready"
    assert final["data_key"]
    assert final["download_url"]


def test_dataset_request_caching_and_storage(client):
    payload = {
        "provider_id": "era5",
        "parameter_type": "total_precipitation",
        "location": "gujarat/ahmedabad",
        "start_date": "2025-07-01",
        "end_date": "2025-07-31",
        "resolution": "0.1deg",
        "version": "v1"
    }

    # 1. First Request: accepted, then ready once the background fetch finishes
    res1 = _wait_for_job(client, payload)
    assert res1.status_code == 200
    data1 = res1.json()
    assert data1["status"] == "ready"
    assert data1["cached"] is False
    assert data1["record_count"] == 31
    assert data1["download_url"] is not None
    assert data1["data_key"] is not None
    data_key = data1["data_key"]

    # 2. Second Request: Cache HIT -> Reused from central repository
    res2 = client.post("/api/datasets/request", json=payload)
    assert res2.status_code == 200
    data2 = res2.json()
    assert data2["status"] == "ready"
    assert data2["cached"] is True
    assert data2["data_key"] == data_key

    # 3. List datasets
    res_list = client.get("/api/datasets")
    assert res_list.status_code == 200
    items = res_list.json()
    assert any(item["data_key"] == data_key for item in items)

    # 4. Get dataset metadata
    res_meta = client.get(f"/api/datasets/{data_key}")
    assert res_meta.status_code == 200
    meta = res_meta.json()
    assert meta["data_key"] == data_key
    assert meta["record_count"] == 31
    assert "gcs_blob_target" in meta

    # 5. Download Parquet file
    res_dl = client.get(f"/api/datasets/{data_key}/download")
    assert res_dl.status_code == 200
    assert res_dl.headers["content-type"] == "application/vnd.apache.parquet"
    assert len(res_dl.content) > 0

    # 6. Preview Parquet dataset JSON rows
    res_preview = client.get(f"/api/datasets/{data_key}/data?limit=10")
    assert res_preview.status_code == 200
    preview = res_preview.json()
    assert preview["data_key"] == data_key
    assert len(preview["sample_records"]) == 10
    assert "summary" in preview
    assert preview["summary"]["total_records"] == 31


def test_multi_parameter_dataset_request(client):
    payload = {
        "provider_id": "era5",
        "parameters": [
            "2m_temperature",
            "total_precipitation",
            "volumetric_soil_water_layer_1",
            "10m_wind_speed"
        ],
        "location": "gujarat/ahmedabad",
        "start_date": "2025-06-01",
        "end_date": "2025-06-30",
        "resolution": "0.1deg",
        "version": "v1"
    }

    # 1. Request multiple parameters simultaneously
    res = _wait_for_job(client, payload)
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "ready"
    assert data["record_count"] == 30
    assert len(data["parameters"]) == 4
    data_key = data["data_key"]

    # 2. Preview data and verify all columns are present
    res_prev = client.get(f"/api/datasets/{data_key}/data?limit=5")
    assert res_prev.status_code == 200
    prev = res_prev.json()
    first_row = prev["sample_records"][0]
    assert "2m_temperature" in first_row
    assert "total_precipitation" in first_row
    assert "volumetric_soil_water_layer_1" in first_row
    assert "10m_wind_speed" in first_row

    # 3. Analyze multi-parameter dataset (correlation matrix and per-variable stats)
    res_analysis = client.get(f"/api/datasets/{data_key}/analysis")
    assert res_analysis.status_code == 200
    analysis = res_analysis.json()
    assert "variables_analysis" in analysis
    assert "2m_temperature" in analysis["variables_analysis"]
    assert "total_precipitation" in analysis["variables_analysis"]
    assert "correlation_matrix" in analysis
    assert "2m_temperature" in analysis["correlation_matrix"]

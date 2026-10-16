def _wait_for_job(client, payload):
    accepted = client.post("/api/v1/datasets/request", json=payload)
    if accepted.status_code == 200:
        return accepted
    assert accepted.status_code == 202
    body = accepted.json()
    assert body["job_id"]
    final = accepted
    for _ in range(80):
        final = client.get(f"/api/v1/datasets/jobs/{body['job_id']}")
        status = final.json().get("status")
        if status in ("ready", "failed"):
            return final
        __import__("time").sleep(0.05)
    return final


def test_catalog_lists_land_and_single_levels(client):
    catalog = client.get("/api/v1/datasets/catalog")
    assert catalog.status_code == 200
    rows = {item["id"]: item for item in catalog.json()}
    assert set(rows) >= {"reanalysis-era5-land", "reanalysis-era5-single-levels"}
    assert rows["reanalysis-era5-land"]["resolution"] == "0.1deg"
    assert rows["reanalysis-era5-single-levels"]["resolution"] == "0.25deg"
    assert rows["reanalysis-era5-single-levels"]["variable_count"] > rows["reanalysis-era5-land"]["variable_count"]

    variables = client.get("/api/v1/datasets/catalog/reanalysis-era5-single-levels/variables")
    assert variables.status_code == 200
    names = {item["name"] for item in variables.json()}
    assert {"2m_temperature", "mean_sea_level_pressure", "100m_u_component_of_wind"} <= names
    assert "lake_cover" not in names


def test_single_levels_request_is_separate_from_land(client):
    payload = {
        "provider_id": "era5",
        "dataset_id": "reanalysis-era5-single-levels",
        "location": "sample-area",
        "start_date": "2024-02-01",
        "end_date": "2024-02-01",
        "parameters": ["2m_temperature", "mean_sea_level_pressure"],
    }
    ready = _wait_for_job(client, payload)
    assert ready.status_code == 200
    body = ready.json()
    assert body["status"] == "ready"
    assert "reanalysis-era5-single-levels" in body["data_key"]
    assert body["record_count"] == 11 * 11 * 24 * 2
    assert body["parameters"] == ["2m_temperature", "mean_sea_level_pressure"]

    land = {
        **payload,
        "dataset_id": "reanalysis-era5-land",
        "parameters": ["2m_temperature"],
    }
    land_ready = _wait_for_job(client, land)
    assert land_ready.json()["data_key"] != body["data_key"]


def test_single_levels_point_snaps_to_quarter_degree(client):
    payload = {
        "provider_id": "era5",
        "dataset_id": "reanalysis-era5-single-levels",
        "location": "23.04,72.58",
        "start_date": "2024-07-01",
        "end_date": "2024-07-01",
        "parameters": ["2m_temperature"],
        "latitude": 23.04,
        "longitude": 72.58,
    }
    ready = _wait_for_job(client, payload)
    assert ready.status_code == 200
    body = ready.json()
    assert "lat23_lon72p5" in body["data_key"]
    assert body["record_count"] == 24


def test_era5_hourly_one_day_stores_all_variables(client):
    payload = {
        "provider_id": "era5",
        "dataset_id": "reanalysis-era5-land",
        "location": "sample-area",
        "start_date": "2024-02-01",
        "end_date": "2024-02-01",
    }
    ready = _wait_for_job(client, payload)
    assert ready.status_code == 200
    body = ready.json()
    assert body["status"] == "ready"
    assert body["cached"] is False
    # Tests simulate a 1 degree grid. A live CDS file keeps the 0.1 degree grid.
    assert body["record_count"] == 11 * 11 * 24 * 50
    assert len(body["parameters"]) == 50

    data_key = body["data_key"]
    preview = client.get(f"/api/v1/datasets/{data_key}/data?limit=5")
    assert preview.status_code == 200
    row = preview.json()["sample_records"][0]
    assert set(row) >= {"timestamp", "latitude", "longitude", "dataset", "variable", "value"}
    assert row["dataset"] == "ERA5-Land"
    meta = client.get(f"/api/v1/datasets/{data_key}")
    assert meta.json()["dataset_name"] == "ERA5-Land"
    assert meta.json()["object_name"].startswith("provider/era5_land/2024/02/")
    assert meta.json()["month_objects"][0]["object_name"].startswith("provider/era5_land/2024/02/")

    again = client.post("/api/v1/datasets/request", json=payload)
    assert again.status_code == 200
    assert again.json()["cached"] is True
    assert again.json()["data_key"] == data_key


def test_area_bounds_are_part_of_the_stored_key(client):
    payload = {
        "provider_id": "era5",
        "dataset_id": "reanalysis-era5-land",
        "location": "custom-box",
        "start_date": "2024-03-02",
        "end_date": "2024-03-02",
        "north": 24,
        "west": 68,
        "south": 20,
        "east": 74,
    }
    ready = _wait_for_job(client, payload)
    assert ready.status_code == 200
    body = ready.json()
    assert "n24_w68_s20_e74" in body["data_key"]
    assert "_all_" in body["data_key"]
    meta = client.get(f"/api/v1/datasets/{body['data_key']}")
    assert meta.json()["area"] == [24.0, 68.0, 20.0, 74.0]
    assert meta.json()["grid_points"] == 5 * 7


def test_selected_variables_and_range_are_part_of_the_key(client):
    payload = {
        "provider_id": "era5",
        "dataset_id": "reanalysis-era5-land",
        "location": "sample-area",
        "start_date": "2024-05-01",
        "end_date": "2024-05-02",
        "parameters": ["2m_temperature", "total_precipitation"],
    }
    ready = _wait_for_job(client, payload)
    assert ready.status_code == 200
    body = ready.json()
    assert "vars_2m_temperature_total_precipitation" in body["data_key"]
    assert "2024-05-01_2024-05-02" in body["data_key"]
    assert body["record_count"] == 11 * 11 * 48 * 2
    meta = client.get(f"/api/v1/datasets/{body['data_key']}").json()
    assert meta["month_objects"][0]["object_name"].startswith("provider/era5_land/2024/05/")

    crossing = {
        **payload,
        "start_date": "2024-01-31",
        "end_date": "2024-02-01",
    }
    crossing_ready = _wait_for_job(client, crossing)
    assert crossing_ready.status_code == 200
    crossing_meta = client.get(f"/api/v1/datasets/{crossing_ready.json()['data_key']}").json()
    months = {(part["year"], part["month"]) for part in crossing_meta["month_objects"]}
    assert months == {("2024", "01"), ("2024", "02")}
    assert any("/2024/01/" in part["object_name"] for part in crossing_meta["month_objects"])
    assert any("/2024/02/" in part["object_name"] for part in crossing_meta["month_objects"])


def test_point_request_uses_one_cell(client):
    payload = {
        "provider_id": "era5",
        "dataset_id": "reanalysis-era5-land",
        "location": "23.03,72.58",
        "start_date": "2024-04-02",
        "end_date": "2024-04-02",
        "latitude": 23.03,
        "longitude": 72.58,
    }
    ready = _wait_for_job(client, payload)
    assert ready.status_code == 200
    body = ready.json()
    assert "lat23_lon72p6" in body["data_key"]
    assert body["record_count"] == 24 * 50


def test_point_inside_a_stored_box_is_taken_from_that_grid(client):
    box = {
        "provider_id": "era5",
        "dataset_id": "reanalysis-era5-land",
        "location": "sample-area",
        "start_date": "2024-06-03",
        "end_date": "2024-06-03",
        "parameters": ["2m_temperature"],
        "north": 24,
        "west": 70,
        "south": 22,
        "east": 74,
    }
    stored = _wait_for_job(client, box)
    assert stored.status_code == 200

    point = {
        "provider_id": "era5",
        "dataset_id": "reanalysis-era5-land",
        "location": "23,72",
        "start_date": "2024-06-03",
        "end_date": "2024-06-03",
        "parameters": ["2m_temperature"],
        "latitude": 23.0,
        "longitude": 72.0,
    }
    ready = client.post("/api/v1/datasets/request", json=point)
    assert ready.status_code == 200
    body = ready.json()
    assert body["cached"] is True
    assert "lat23_lon72_" in body["data_key"]
    assert "n24_w70_s22_e74" not in body["data_key"]
    assert "monthly files" in body["detail"]
    preview = client.get(f"/api/v1/datasets/{body['data_key']}/data?limit=5")
    row = preview.json()["sample_records"][0]
    assert row["latitude"] == 23.0
    assert row["longitude"] == 72.0


def test_missing_variable_is_fetched_into_its_own_file(client):
    box = {
        "provider_id": "era5",
        "dataset_id": "reanalysis-era5-land",
        "location": "sample-area",
        "start_date": "2024-02-01",
        "end_date": "2024-02-01",
        "parameters": ["2m_temperature"],
        "north": 24,
        "west": 70,
        "south": 22,
        "east": 74,
    }
    stored = _wait_for_job(client, box)
    assert stored.status_code == 200
    box_key = stored.json()["data_key"]
    box_rows = client.get(f"/api/v1/datasets/{box_key}/data?limit=1000").json()["total_records"]

    point = {
        "provider_id": "era5",
        "dataset_id": "reanalysis-era5-land",
        "location": "23.04,72.48",
        "start_date": "2024-02-01",
        "end_date": "2024-02-01",
        "parameters": ["2m_temperature", "total_precipitation"],
        "latitude": 23.04,
        "longitude": 72.48,
    }
    ready = _wait_for_job(client, point)
    assert ready.status_code == 200
    body = ready.json()
    assert "lat23_lon72p5" in body["data_key"]
    assert "Fetched total_precipitation" in body["detail"]
    assert "Reused 2m_temperature" in body["detail"]
    assert body["record_count"] == 48

    from app.services.storage_service import storage_backend
    stored_rows = storage_backend.read_dataset(body["data_key"])
    assert set(stored_rows["variable"]) == {"2m_temperature", "total_precipitation"}
    assert set(stored_rows["latitude"]) == {23.0}
    assert set(stored_rows["longitude"]) == {72.5}
    assert (stored_rows["variable"] == "2m_temperature").sum() == 24
    assert (stored_rows["variable"] == "total_precipitation").sum() == 24

    # The original box file is unchanged. The new variable lives in its own point file.
    assert client.get(f"/api/v1/datasets/{box_key}/data?limit=1").json()["total_records"] == box_rows
    meta = client.get(f"/api/v1/datasets/{body['data_key']}").json()
    assert meta["variable_sources"]["2m_temperature"] == box_key
    fetched_key = meta["variable_sources"]["total_precipitation"]
    assert fetched_key != box_key
    fetched_meta = client.get(f"/api/v1/datasets/{fetched_key}").json()
    assert fetched_meta["parameters"] == ["total_precipitation"]

    # A later request for one more variable fetches only that variable.
    wind = {**point, "parameters": ["2m_temperature", "total_precipitation", "10m_u_component_of_wind"]}
    again = _wait_for_job(client, wind)
    assert again.status_code == 200
    assert "Fetched 10m_u_component_of_wind" in again.json()["detail"]
    assert "total_precipitation" not in again.json()["detail"].split("Fetched", 1)[1]


def test_month_inside_a_stored_year_is_taken_from_monthly_files(client):
    year = {
        "provider_id": "era5",
        "dataset_id": "reanalysis-era5-land",
        "location": "23,72",
        "start_date": "2024-02-01",
        "end_date": "2024-03-02",
        "parameters": ["2m_temperature"],
        "latitude": 23.0,
        "longitude": 72.0,
    }
    stored = _wait_for_job(client, year)
    assert stored.status_code == 200

    month = {**year, "start_date": "2024-03-01", "end_date": "2024-03-02"}
    ready = client.post("/api/v1/datasets/request", json=month)
    assert ready.status_code == 200
    body = ready.json()
    assert body["cached"] is True
    assert "monthly files" in body["detail"]
    assert body["record_count"] == 24 * 2
    assert "2024-03-01_2024-03-02" in body["data_key"]

    from app.services.storage_service import storage_backend
    catalog = storage_backend._read_catalog()
    march = [
        item for item in catalog["_coverage"].values()
        if item["month"] == "03"
        and item["variable"] == "2m_temperature"
        and item["area_id"].startswith("lat23_lon72")
    ]
    assert march
    assert march[0]["object_name"].startswith("provider/era5_land/2024/03/2m_temperature/lat23_lon72")
    assert "2024-03-01" in march[0]["days"]
    assert march[0]["complete"] is False


def test_point_inside_a_stored_area_uses_coverage(client):
    box = {
        "provider_id": "era5",
        "dataset_id": "reanalysis-era5-land",
        "location": "sample-area",
        "start_date": "2024-07-01",
        "end_date": "2024-07-01",
        "parameters": ["2m_temperature"],
        "north": 24,
        "west": 70,
        "south": 22,
        "east": 74,
    }
    stored = _wait_for_job(client, box)
    assert stored.status_code == 200

    point = {
        "provider_id": "era5",
        "dataset_id": "reanalysis-era5-land",
        "location": "23.04,72.48",
        "start_date": "2024-07-01",
        "end_date": "2024-07-01",
        "parameters": ["2m_temperature"],
        "latitude": 23.04,
        "longitude": 72.48,
    }
    ready = client.post("/api/v1/datasets/request", json=point)
    assert ready.status_code == 200
    body = ready.json()
    assert body["cached"] is True
    assert "monthly files" in body["detail"]
    assert body["record_count"] == 24


def test_point_outside_a_stored_box_is_not_taken_from_it(client):
    box = {
        "provider_id": "era5",
        "dataset_id": "reanalysis-era5-land",
        "location": "sample-area",
        "start_date": "2024-06-04",
        "end_date": "2024-06-04",
        "parameters": ["2m_temperature"],
        "north": 24,
        "west": 70,
        "south": 22,
        "east": 74,
    }
    stored = _wait_for_job(client, box)
    assert stored.status_code == 200

    point = {
        "provider_id": "era5",
        "dataset_id": "reanalysis-era5-land",
        "location": "15,15",
        "start_date": "2024-06-04",
        "end_date": "2024-06-04",
        "parameters": ["2m_temperature"],
        "latitude": 15,
        "longitude": 15,
    }
    ready = client.post("/api/v1/datasets/request", json=point)
    assert ready.status_code == 202


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

    accepted = client.post("/api/v1/datasets/request", json=payload)
    assert accepted.status_code == 202
    body = accepted.json()
    assert body["status"] == "accepted"
    assert body["job_id"]

    seen_running = False
    final = None
    for _ in range(40):
        polled = client.get(f"/api/v1/datasets/jobs/{body['job_id']}")
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
    res2 = client.post("/api/v1/datasets/request", json=payload)
    assert res2.status_code == 200
    data2 = res2.json()
    assert data2["status"] == "ready"
    assert data2["cached"] is True
    assert data2["data_key"] == data_key

    # 3. List datasets
    res_list = client.get("/api/v1/datasets")
    assert res_list.status_code == 200
    items = res_list.json()
    assert any(item["data_key"] == data_key for item in items)

    # 4. Get dataset metadata
    res_meta = client.get(f"/api/v1/datasets/{data_key}")
    assert res_meta.status_code == 200
    meta = res_meta.json()
    assert meta["data_key"] == data_key
    assert meta["record_count"] == 31
    assert "gcs_blob_target" in meta

    # 5. Download Parquet file
    res_dl = client.get(f"/api/v1/datasets/{data_key}/download")
    assert res_dl.status_code == 200
    assert res_dl.headers["content-type"] == "application/vnd.apache.parquet"
    assert len(res_dl.content) > 0

    # 6. Preview Parquet dataset JSON rows
    res_preview = client.get(f"/api/v1/datasets/{data_key}/data?limit=10")
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
    res_prev = client.get(f"/api/v1/datasets/{data_key}/data?limit=5")
    assert res_prev.status_code == 200
    prev = res_prev.json()
    first_row = prev["sample_records"][0]
    assert "2m_temperature" in first_row
    assert "total_precipitation" in first_row
    assert "volumetric_soil_water_layer_1" in first_row
    assert "10m_wind_speed" in first_row

    # 3. Analyze multi-parameter dataset (correlation matrix and per-variable stats)
    res_analysis = client.get(f"/api/v1/datasets/{data_key}/analysis")
    assert res_analysis.status_code == 200
    analysis = res_analysis.json()
    assert "variables_analysis" in analysis
    assert "2m_temperature" in analysis["variables_analysis"]
    assert "total_precipitation" in analysis["variables_analysis"]
    assert "correlation_matrix" in analysis
    assert "2m_temperature" in analysis["correlation_matrix"]

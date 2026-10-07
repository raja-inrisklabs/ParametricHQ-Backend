def _wait_for_job(client, payload):
    accepted = client.post("/api/v1/datasets/request", json=payload)
    if accepted.status_code == 200:
        return accepted
    assert accepted.status_code == 202
    body = accepted.json()
    final = accepted
    for _ in range(80):
        final = client.get(f"/api/v1/datasets/jobs/{body['job_id']}")
        if final.json().get("status") in ("ready", "failed"):
            return final
        __import__("time").sleep(0.05)
    return final


def test_dataset_analysis_endpoint(client):
    # Setup dataset
    setup_req = {
        "provider_id": "era5",
        "parameter_type": "2m_temperature",
        "location": "gujarat/ahmedabad",
        "start_date": "2025-05-01",
        "end_date": "2025-07-31",
        "temporal_resolution": "daily",
    }
    setup_res = _wait_for_job(client, setup_req)
    assert setup_res.status_code == 200
    data_key = setup_res.json()["data_key"]

    # Call analysis endpoint
    res_analysis = client.get(f"/api/v1/datasets/{data_key}/analysis")
    assert res_analysis.status_code == 200
    data = res_analysis.json()

    # Validate analysis payload structure
    assert data["data_key"] == data_key
    assert data["parameter"] == "2m_temperature"
    assert data["unit"] == "degC"

    # Descriptive statistics
    stats = data["statistics"]
    assert stats["count"] == 92
    assert "mean" in stats
    assert "median" in stats
    assert "min" in stats
    assert "max" in stats
    assert "p25" in stats
    assert "p75" in stats
    assert stats["min"] <= stats["mean"] <= stats["max"]

    # Temporal analysis
    temporal = data["temporal"]
    assert temporal["total_records"] == 92
    assert temporal["completeness_percentage"] == 100.0

    # Monthly breakdown
    monthly = data["monthly_breakdown"]
    assert len(monthly) == 3  # May, June, July
    assert monthly[0]["period"] == "2025-05"

    # Peak events
    assert len(data["top_peak_events"]) <= 5
    assert len(data["top_lowest_events"]) <= 5

    # Distribution histogram
    bins = data["distribution_bins"]
    assert len(bins) == 10
    total_pct = sum(b["percentage"] for b in bins)
    assert 99.0 <= total_pct <= 101.0

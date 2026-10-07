def test_root_endpoint(client):
    response = client.get("/")
    assert response.status_code == 200
    data = response.json()
    assert data["service"] == "Parametric Data Platform"
    assert "era5_variables" in data
    assert "docs" in data


def test_health_check_endpoint(client):
    response = client.get("/api/v1/health")
    assert response.status_code == 200
    data = response.json()
    assert data["ok"] is True
    assert data["storage"] == "writable"
    assert "era5" in data["providers"]
    assert data["providers"]["era5"]["supported_parameters_count"] > 10

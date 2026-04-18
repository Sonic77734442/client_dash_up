from fastapi.testclient import TestClient

from app.main import app


client = TestClient(app)


def reset_state():
    assert client.post("/_testing/use-inmemory-stores").status_code == 200


def test_healthz_and_readyz_ok_with_request_id_header():
    reset_state()

    h = client.get("/healthz")
    assert h.status_code == 200
    assert h.json()["status"] == "ok"
    assert h.headers.get("X-Request-Id")

    r = client.get("/readyz")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ready"
    assert body["checks"]["sqlite"] is True


def test_metrics_endpoint_exposes_counters():
    reset_state()
    assert client.get("/healthz").status_code == 200
    assert client.get("/health").status_code == 200

    m = client.get("/metrics")
    assert m.status_code == 200
    text = m.text
    assert "http_requests_total" in text
    assert "http_request_duration_seconds_sum" in text
    assert "app_uptime_seconds" in text

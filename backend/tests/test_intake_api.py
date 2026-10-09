from fastapi.testclient import TestClient

from pagggle.app import create_app
from pagggle.config import Settings
from pagggle.store import Store


def test_intake_preview_confirm_retry_and_project_isolation(tmp_path):
    store = Store(tmp_path / "test.sqlite3")
    settings = Settings(BASE_URL="https://provider.example/v1", model="test", api_key="test")
    a = store.create_project("A", "https://example.com")["id"]
    b = store.create_project("B", "https://example.org")["id"]
    payload = {"text": "custom gasket\nCUSTOM gasket\nEPDM 2mm", "request_id": "test-request-id"}
    with TestClient(create_app(settings, store)) as client:
        preview = client.post(f"/api/projects/{a}/demands/preview", json=payload)
        assert preview.status_code == 200
        assert preview.json()["summary"]["valid"] == 2
        assert client.get(f"/api/projects/{a}/demands").json() == []
        first = client.post(f"/api/projects/{a}/demands/import", json=payload)
        retry = client.post(f"/api/projects/{a}/demands/import", json=payload)
        assert first.status_code == 201 and first.json() == retry.json()
        assert len(client.get(f"/api/projects/{a}/demands").json()) == 3
        assert client.get(f"/api/projects/{b}/demands").json() == []

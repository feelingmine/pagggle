import time

import pytest
from fastapi.testclient import TestClient

from pagggle.app import create_app
from pagggle.config import Settings
from pagggle.store import Store


@pytest.fixture
def setup(tmp_path):
    store = Store(tmp_path / "test.sqlite3")
    settings = Settings(BASE_URL="https://provider.example/v1", model="test", api_key="private-test-key")
    with TestClient(create_app(settings, store)) as client:
        yield client, store


def new_project(client, name="A"):
    response = client.post("/api/projects", json={"name": name, "site_url": "https://example.com"})
    assert response.status_code == 201
    return response.json()["id"]


def add_source(client, project_id):
    return client.post(f"/api/projects/{project_id}/sources", json={"title": "Products", "body": "We manufacture custom silicone gaskets."}).json()


def payload(source):
    return {"facts": [{"category": "product", "statement": "生产硅胶密封垫", "status": "stated", "scope": "密封垫", "citations": [{"source_id": source["id"], "quote": source["body"]}]}], "gaps": []}


def test_full_manual_review_and_revision_flow(setup):
    client, store = setup
    project_id = new_project(client)
    source = add_source(client, project_id)
    base = f"/api/projects/{project_id}"
    data = payload(source)
    result = client.post(base + "/profiles", json={"payload": data, "expected_version": 0, "reason": "原始资料"})
    assert result.status_code == 201
    approved = client.post(base + "/profiles/confirm", json={"expected_version": 1, "reason": "产品负责人已核对"})
    assert approved.json()["status"] == "confirmed"
    data["facts"][0]["scope"] = "工业用途"
    edited = client.post(base + "/profiles", json={"payload": data, "expected_version": 1, "reason": "修订适用范围"})
    assert edited.json()["status"] == "draft" and edited.json()["version"] == 2
    stale = client.post(base + "/profiles/confirm", json={"expected_version": 1, "reason": "stale"})
    assert stale.status_code == 409
    assert len(client.get(base).json()["profiles"]) == 2
    with store.connect() as db:
        names = [r[0] for r in db.execute("SELECT name FROM events WHERE project_id=?", (project_id,))]
    assert "site_understanding_confirmed" in names


def test_cross_project_sources_and_job_ids_rejected(setup):
    client, store = setup
    a, b = new_project(client), new_project(client, "B")
    source = add_source(client, a)
    response = client.post(f"/api/projects/{b}/profiles", json={"payload": payload(source), "expected_version": 0, "reason": "wrong project"})
    assert response.status_code == 409
    assert client.get(f"/api/projects/{b}").json()["sources"] == []
    job = store.create_job(a, "crawl")
    assert client.post(f"/api/projects/{b}/jobs/{job}/cancel", json={}).status_code == 404
    assert not store.cancelled(a, job)


def test_config_not_served_and_cross_origin_blocked(setup):
    client, _ = setup
    for path in ("/config.json", "/assets/config.json", "/assets/../config.json", "/data/pagggle.sqlite3"):
        assert client.get(path).status_code == 404
    assert "private-test-key" not in client.get("/api/health").text
    assert client.get("/", headers={"Host": "attacker.example"}).status_code == 400
    assert client.post("/api/projects", json={}, headers={"Origin": "https://attacker.example"}).status_code == 403
    assert client.post("/api/projects", content="{}", headers={"Content-Type": "text/plain"}).status_code == 415
    assert client.get("/").status_code == 200
    assert client.get("/assets/app.js").status_code == 200


def test_active_jobs_recovery_and_cancel_preserve_profiles(setup):
    client, store = setup
    project = new_project(client)
    job = store.create_job(project, "understand")
    with pytest.raises(ValueError):
        store.create_job(project, "crawl")
    store.update_job(project, job, status="cancelled")
    with pytest.raises(ValueError):
        store.save_profile(project, {}, "cancelled output", 0, job_id=job)
    retry = store.create_job(project, "understand")
    store.recover_jobs()
    assert store.jobs(project)[0]["id"] == retry
    assert store.jobs(project)[0]["status"] == "failed"
    assert store.profiles(project) == []


def test_model_job_persists_validated_profile_and_usage(monkeypatch, setup):
    client, store = setup
    project = new_project(client)
    source = add_source(client, project)
    monkeypatch.setattr("pagggle.app.generate_understanding", lambda settings, sources, **kwargs: (payload(sources[0]), {"total_tokens": 123, "cost": None}))
    response = client.post(f"/api/projects/{project}/jobs", json={"kind": "understand"})
    assert response.status_code == 202
    for _ in range(100):
        detail = client.get(f"/api/projects/{project}").json()
        if detail["jobs"][0]["status"] in {"failed", "succeeded"}:
            break
        time.sleep(.01)
    assert detail["jobs"][0]["status"] == "succeeded"
    assert detail["profiles"][0]["payload"]["facts"][0]["citations"][0]["source_id"] == source["id"]
    assert detail["model_runs"][0]["usage"]["total_tokens"] == 123


def test_single_fact_review_preserves_context_and_rejects_stale_version(setup):
    client, store = setup
    p = new_project(client)
    source = add_source(client, p)
    store.save_profile(p, payload(source), "original", 0)
    route = f"/api/projects/{p}/profiles/review"
    response = client.post(route, json={"expected_version": 1, "fact_index": 0, "decision": "confirmed", "statement": "企业确认仅生产工业用密封垫", "reason": "负责人核对供货范围"})
    assert response.status_code == 201
    assert response.json()["payload"]["facts"][0]["status"] == "confirmed"
    assert response.json()["payload"]["facts"][0]["citations"][0]["source_id"] == source["id"]
    assert response.json()["status"] == "draft"
    stale = client.post(route, json={"expected_version": 1, "fact_index": 0, "decision": "pending", "reason": "stale"})
    assert stale.status_code == 409
    assert store.profiles(p)[1]["payload"]["facts"][0]["status"] == "stated"


def test_first_use_discovers_links_without_reading_or_understanding(monkeypatch, setup):
    client, store = setup
    p = new_project(client)
    def discover(store, project_id, *args):
        assert args[-1] is True
        store.add_source(project_id, title="Products", kind="page", status="uncovered", url="https://example.com/")
        return {"sitemaps_read": 1}
    monkeypatch.setattr("pagggle.app.crawl_site", discover)
    monkeypatch.setattr("pagggle.app.generate_understanding", lambda *args, **kwargs: pytest.fail("Discovery must not call the model"))
    response = client.post(f"/api/projects/{p}/jobs", json={"kind": "analyze"})
    assert response.status_code == 202
    for _ in range(100):
        snapshot = store.snapshot(p)
        if snapshot["jobs"][0]["status"] in {"succeeded", "failed"}: break
        time.sleep(.01)
    assert snapshot["jobs"][0]["status"] == "succeeded"
    assert snapshot["jobs"][0]["kind"] == "discover"
    assert snapshot["profiles"] == []
    assert snapshot["sources"][0]["body"] == ""

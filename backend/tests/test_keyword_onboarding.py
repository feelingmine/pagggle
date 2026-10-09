import pytest
from fastapi.testclient import TestClient

from pagggle.app import create_app
from pagggle.config import Settings
from pagggle.intake import import_records
from pagggle.store import Store


def import_keywords(store, project, text="gasket\nseal\ngasket", request_id="initial-keywords"):
    return import_records(store, project, dict(kind="keyword", format="lines", text=text, request_id=request_id), commit=True)


def save_run(store, project, ids=None, status="succeeded"):
    ids = ids if ids is not None else [r["id"] for r in store.demands(project) if r["kind"] == "keyword" and r["status"] == "pending"]
    job = store.create_job(project, "clusters", {"demand_ids": ids})
    store.update_job(project, job, status="running")
    groups = [{"target_page_id": "group-" + id, "members": [{"demand_id": id}]} for id in ids]
    store.save_clusters(project, job, {"groups": groups, "summary": {"keywords": len(ids)}})
    store.update_job(project, job, status=status)
    return store.cluster_runs(project)[0]


@pytest.fixture
def setup(tmp_path):
    store = Store(tmp_path / "test.sqlite3")
    a = store.create_project("A", "https://a.example")["id"]
    b = store.create_project("B", "https://b.example")["id"]
    settings = Settings(BASE_URL="https://provider.example/v1", model="test", api_key="")
    return store, a, b, settings


def test_complete_persists_selected_groups_and_unlocks_only_own_project(setup):
    store, a, b, settings = setup
    for p in [a, b]:
        import_keywords(store, p)
    run = save_run(store, a)
    selection = {"run_id": run["id"], "selected_group_ids": [run["payload"]["groups"][0]["target_page_id"]]}
    with TestClient(create_app(settings, store)) as client:
        assert client.get(f"/api/projects/{a}").json()["keyword_onboarding"] is None
        for kind in ["customer_question", "product_change"]:
            payload = {"kind": kind, "text": "Can you work from samples?\nWe have no drawing.", "request_id": "incremental-" + kind}
            for endpoint in ["preview", "import"]:
                assert client.post(f"/api/projects/{a}/demands/{endpoint}", json=payload).status_code == 409
        completed = client.post(f"/api/projects/{a}/keyword-onboarding/complete", json=selection)
        assert completed.status_code == 200
        assert client.post(f"/api/projects/{a}/keyword-onboarding/complete", json=selection).json() == completed.json()
        assert client.get(f"/api/projects/{a}").json()["keyword_onboarding"] == completed.json()
        assert client.get(f"/api/projects/{b}").json()["keyword_onboarding"] is None
        assert Store(store.path).keyword_onboarding(a) == completed.json()
        for kind in ["customer_question", "product_change"]:
            payload = {"kind": kind, "text": "Can you work from samples?\nWe have no drawing.", "request_id": "incremental-" + kind}
            assert client.post(f"/api/projects/{b}/demands/import", json=payload).status_code == 409
            response = client.post(f"/api/projects/{a}/demands/import", json=payload)
            assert response.status_code == 201
            assert response.json()["summary"]["input"] == 1
            assert response.json()["records"][0]["original"] == payload["text"]
            assert response.json()["records"][0]["volume"] is None
        assert client.post(f"/api/projects/{a}/keyword-onboarding/complete", json={**selection, "selected_group_ids": []}).status_code == 409
    with store.connect() as db:
        assert db.execute("SELECT COUNT(*) FROM events WHERE project_id=? AND name='keyword_onboarding_completed'", (a,)).fetchone()[0] == 1


@pytest.mark.parametrize("condition", ["partial", "new-input", "old-version", "active", "cancelled", "failed", "empty"])
def test_incomplete_or_stale_results_cannot_finish(setup, condition):
    store, a, _, settings = setup
    import_keywords(store, a)
    ids = [r["id"] for r in store.demands(a) if r["status"] == "pending"]
    run = save_run(store, a, ids[:1] if condition == "partial" else [] if condition == "empty" else ids,
        status=condition if condition in {"cancelled", "failed"} else "succeeded")
    if condition == "new-input":
        import_keywords(store, a, "new material", "second-keywords")
    if condition == "old-version":
        save_run(store, a)
    with TestClient(create_app(settings, store)) as client:
        if condition == "active":
            store.create_job(a, "clusters", {"demand_ids": ids})
        response = client.post(f"/api/projects/{a}/keyword-onboarding/complete", json={"run_id": run["id"], "selected_group_ids": []})
        assert response.status_code == 409
        assert store.keyword_onboarding(a) is None


def test_foreign_run_groups_and_duplicates_rejected_and_empty_choice_supported(setup):
    store, a, b, settings = setup
    import_keywords(store, a)
    import_keywords(store, b)
    own, foreign = save_run(store, a), save_run(store, b)
    group = own["payload"]["groups"][0]["target_page_id"]
    with TestClient(create_app(settings, store)) as client:
        path = f"/api/projects/{a}/keyword-onboarding/complete"
        assert client.post(path, json={"run_id": foreign["id"], "selected_group_ids": []}).status_code == 404
        for selection in [[foreign["payload"]["groups"][0]["target_page_id"]], [group, group]]:
            assert client.post(path, json={"run_id": own["id"], "selected_group_ids": selection}).status_code == 409
        assert client.post(path, json={"run_id": own["id"]}).status_code == 422
        assert client.post(path, json={"run_id": own["id"], "selected_group_ids": []}).status_code == 200
        assert client.post('/api/projects/missing/keyword-onboarding/complete', json={"run_id": own["id"], "selected_group_ids": []}).status_code == 404


def test_scope_defaults_only_fill_missing_csv_values(setup):
    store, a, _, settings = setup
    payload = {"format": "csv", "text": "keyword,market,language\ngasket,欧洲,de\nseal,,", "market": "中东", "language": "en", "request_id": "scope-defaults"}
    with TestClient(create_app(settings, store)) as client:
        preview = client.post(f"/api/projects/{a}/demands/preview", json=payload).json()
        assert [(r["market"], r["language"]) for r in preview["records"]] == [("欧洲", "de"), ("中东", "en")]

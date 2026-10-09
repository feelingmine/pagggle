"""Replay actual extraction from the fixed website; never seed synthetic projects."""

import json
import os
import sqlite3
import threading
import time
from urllib.parse import urlsplit

import httpx
import pytest
from fastapi.testclient import TestClient

from pagggle.app import create_app
from pagggle.config import ROOT, load_settings
from pagggle.seed_keywords import SeedKeywords, generate_seed_keywords, seed_context, validate_seed_keywords
from pagggle.store import Store

pytestmark = pytest.mark.skipif(os.environ.get("PAGGGLE_VERIFY_FIXED_DATA") != "1", reason="Requires the private fixed website and a saved real extraction")


@pytest.fixture
def fixed(tmp_path):
    path = tmp_path / "website.sqlite3"
    with sqlite3.connect(f"file:{ROOT / 'data/pagggle.sqlite3'}?mode=ro", uri=True) as source, sqlite3.connect(path) as target:
        source.backup(target)
    store = Store(path)
    project = next(p for p in store.list_projects() if p["site_url"] == "https://siliconeproductsmanufacturer.com/")
    snapshot = store.snapshot(project["id"])
    assert snapshot["seed_keyword_runs"], "Run the real extraction first; no fabricated result is substituted"
    run = snapshot["seed_keyword_runs"][0]
    profile = next(p for p in snapshot["profiles"] if p["version"] == run["profile_version"])
    return store, project, profile, snapshot["sources"], run["payload"]


def test_public_context_and_saved_evidence(fixed):
    _, project, profile, sources, payload = fixed
    facts = seed_context(profile, sources, project["site_url"])
    assert facts
    assert all(set(f) == {"index", "category", "citations"} for f in facts)
    assert all(set(c) == {"url", "quote"} for f in facts for c in f["citations"])
    assert all(urlsplit(c["url"]).hostname == "siliconeproductsmanufacturer.com" for f in facts for c in f["citations"])
    assert seed_context(profile, [], project["site_url"]) == []
    validate_seed_keywords(SeedKeywords.model_validate(payload), profile)
    assert all(i in {f["index"] for f in facts} for term in payload["keywords"] for i in term["fact_indices"])


def test_provider_replay_sends_only_public_quotes(fixed, monkeypatch):
    _, project, profile, sources, payload = fixed
    settings, real_client = load_settings(), httpx.Client
    def handler(request):
        body = json.loads(request.content)
        assert json.loads(body["messages"][1]["content"]) == {"facts": seed_context(profile, sources, project["site_url"])}
        assert "Authorization" not in body
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(payload)}}]})
    monkeypatch.setattr("pagggle.seed_keywords.httpx.Client", lambda **kw: real_client(transport=httpx.MockTransport(handler), **kw))
    actual, usage = generate_seed_keywords(settings, profile, sources, project["site_url"])
    assert actual == payload
    assert usage["profile_version"] == profile["version"]
    with pytest.raises(ValueError, match="引用"):
        generate_seed_keywords(settings, profile, [], project["site_url"])


def test_http_replay_preserves_existing_data_and_checks_profile(fixed, monkeypatch):
    store, project, profile, _, payload = fixed
    before = store.snapshot(project["id"])
    demands, clusters = store.demands(project["id"]), store.cluster_runs(project["id"])
    monkeypatch.setattr("pagggle.app.generate_seed_keywords", lambda *args, **kwargs: (payload, {"cost": None}))
    with TestClient(create_app(load_settings(), store)) as client:
        route = f"/api/projects/{project['id']}"
        assert client.post(route + "/jobs", json={"kind": "seed_keywords", "profile_version": profile["version"] - 1}).status_code == 409
        assert client.post("/api/projects/missing/jobs", json={"kind": "seed_keywords", "profile_version": profile["version"]}).status_code == 404
        result = client.post(route + "/jobs", json={"kind": "seed_keywords", "profile_version": profile["version"]})
        assert result.status_code == 202
        for _ in range(200):
            job = next(j for j in store.jobs(project["id"]) if j["id"] == result.json()["id"])
            if job["status"] not in {"queued", "running"}:
                break
            time.sleep(.01)
        assert job["status"] == "succeeded"
        after = client.get(route).json()
    assert after["seed_keyword_runs"][0]["payload"] == payload
    assert after["seed_keyword_runs"][1:] == before["seed_keyword_runs"]
    for key in ("sources", "profiles", "keyword_onboarding"):
        assert after[key] == before[key]
    assert store.demands(project["id"]) == demands
    assert store.cluster_runs(project["id"]) == clusters


def test_cancelled_extraction_preserves_results(fixed, monkeypatch):
    store, project, profile, _, payload = fixed
    before = store.snapshot(project["id"])
    entered, release = threading.Event(), threading.Event()
    def deferred(*args, **kwargs):
        entered.set()
        assert release.wait(5)
        return payload, {"cost": None}
    monkeypatch.setattr("pagggle.app.generate_seed_keywords", deferred)
    with TestClient(create_app(load_settings(), store)) as client:
        route = f"/api/projects/{project['id']}"
        job = client.post(route + "/jobs", json={"kind": "seed_keywords", "profile_version": profile["version"]}).json()["id"]
        try:
            assert entered.wait(5)
            assert client.post(route + "/jobs", json={"kind": "seed_keywords", "profile_version": profile["version"]}).status_code == 409
            assert client.post(route + f"/jobs/{job}/cancel", json={}).status_code == 200
        finally:
            release.set()
    after = store.snapshot(project["id"])
    assert after["seed_keyword_runs"] == before["seed_keyword_runs"]
    assert after["model_runs"] == before["model_runs"]


@pytest.mark.parametrize("always_invalid", [False, True])
def test_quality_repair_is_bounded_using_prior_actual_output(fixed, monkeypatch, always_invalid):
    store, project, profile, sources, payload = fixed
    prior = None
    for run in store.snapshot(project["id"])["seed_keyword_runs"][1:]:
        try:
            validate_seed_keywords(SeedKeywords.model_validate(run["payload"]), profile)
        except ValueError:
            prior = run["payload"]
            break
    assert prior is not None, "Requires an actual earlier rejected-format result for replay"
    calls = []
    real_client = httpx.Client
    def handler(request):
        calls.append(json.loads(request.content))
        result = prior if len(calls) == 1 or always_invalid else payload
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(result)}}]})
    monkeypatch.setattr("pagggle.seed_keywords.httpx.Client", lambda **kw: real_client(transport=httpx.MockTransport(handler), **kw))
    if always_invalid:
        with pytest.raises(ValueError, match="一次修正"):
            generate_seed_keywords(load_settings(), profile, sources, project["site_url"])
    else:
        actual, usage = generate_seed_keywords(load_settings(), profile, sources, project["site_url"])
        assert actual == payload and usage["attempts"] == 2
    assert len(calls) == 2


def test_edit_seed_persists_without_changing_original_or_other_data(fixed):
    store, project, profile, _, payload = fixed
    before = store.snapshot(project["id"])
    run = before["seed_keyword_runs"][0]
    term = payload["keywords"][0]
    changes = {key: term[key] for key in ("keyword", "category", "kind")}
    demands, clusters = store.demands(project["id"]), store.cluster_runs(project["id"])
    with TestClient(create_app(load_settings(), store)) as client:
        route = f"/api/projects/{project['id']}/seed-keywords/{run['id']}/terms/0"
        unchanged = client.post(route, json={"expected_revision": run["revision"], **changes})
        assert unchanged.status_code == 200 and not unchanged.json()["changed"]
        # Case-only edit of the real word, in a private database copy; no invented term.
        changes["keyword"] = term["keyword"].swapcase()
        saved = client.post(route, json={"expected_revision": run["revision"], **changes})
        assert saved.status_code == 200 and saved.json()["changed"]
        assert saved.json()["revision"] == run["revision"] + 1
        assert client.post(route, json={"expected_revision": run["revision"], **changes}).status_code == 409
    after = Store(store.path).snapshot(project["id"])
    current = after["seed_keyword_runs"][0]
    assert current["original_payload"] == run["original_payload"]
    assert current["payload"]["keywords"][0] == {**term, **changes}
    assert current["payload"]["keywords"][1:] == payload["keywords"][1:]
    for key in ("sources", "profiles", "keyword_onboarding", "jobs", "model_runs"):
        assert after[key] == before[key]
    assert store.demands(project["id"]) == demands and store.cluster_runs(project["id"]) == clusters
    with store.connect() as db:
        original = json.loads(db.execute("SELECT payload FROM seed_keyword_runs WHERE project_id=? AND id=?", (project["id"], run["id"])).fetchone()[0])
    assert original == run["original_payload"]


def test_edit_seed_rejects_duplicates_invalid_scope_and_old_results(fixed):
    store, project, profile, _, payload = fixed
    before = store.snapshot(project["id"])
    run = before["seed_keyword_runs"][0]
    term = payload["keywords"][0]
    request = {"expected_revision": run["revision"], **{k: term[k] for k in ("keyword", "category", "kind")}}
    with TestClient(create_app(load_settings(), store)) as client:
        route = f"/api/projects/{project['id']}/seed-keywords/{run['id']}/terms/0"
        assert client.post(route, json={**request, "keyword": payload["keywords"][1]["keyword"]}).status_code == 409
        assert client.post(route, json={**request, "keyword": " "}).status_code == 422
        assert client.post(route, json={**request, "fact_indices": term["fact_indices"]}).status_code == 422
        assert client.post(f"/api/projects/missing/seed-keywords/{run['id']}/terms/0", json=request).status_code == 404
        assert client.post(route.replace("/terms/0", f"/terms/{len(payload['keywords'])}"), json=request).status_code == 404
        old = before["seed_keyword_runs"][1]
        assert client.post(route.replace(run["id"], old["id"]), json=request).status_code == 409
        job = store.create_job(project["id"], "seed_keywords", {"profile_version": profile["version"]})
        assert client.post(route, json=request).status_code == 409
        store.update_job(project["id"], job, status="cancelled")
        store.save_profile(project["id"], profile["payload"], profile["reason"], profile["version"])
        assert client.post(route, json=request).status_code == 409
    assert store.snapshot(project["id"])["seed_keyword_runs"] == before["seed_keyword_runs"]


def test_manual_seed_revision_survives_a_new_extraction(fixed):
    store, project, profile, _, payload = fixed
    run = store.snapshot(project["id"])["seed_keyword_runs"][0]
    changes = {key: payload["keywords"][0][key] for key in ("keyword", "category", "kind")}
    changes["keyword"] = changes["keyword"].swapcase()
    store.edit_seed_keyword(project["id"], run["id"], 0, run["revision"], changes)
    revised = store.snapshot(project["id"])["seed_keyword_runs"][0]
    job = store.create_job(project["id"], "seed_keywords", {"profile_version": profile["version"]})
    store.update_job(project["id"], job, status="running")
    store.save_seed_keywords(project["id"], job, profile["version"], payload, load_settings().model, {})
    store.update_job(project["id"], job, status="succeeded")
    runs = store.snapshot(project["id"])["seed_keyword_runs"]
    assert runs[0]["payload"] == payload and runs[0]["revision"] == 0
    assert next(r for r in runs if r["id"] == revised["id"]) == revised

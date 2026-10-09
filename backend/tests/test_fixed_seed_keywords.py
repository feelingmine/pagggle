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

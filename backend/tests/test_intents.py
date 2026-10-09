import copy
import json
import time

import httpx
import pytest
from fastapi.testclient import TestClient

from pagggle.app import create_app
from pagggle.config import Settings
from pagggle.intake import import_records
from pagggle.intents import generate_intents, validate_results
from pagggle.store import Store


def interpretation(record):
    return {"input_id": record["id"], "status": "ready", "reason": "规格查询", "business_fit": {"status": "unknown", "reason": "适用范围未知"}, "candidates": [{
        "object": "gasket", "scenario": None, "explicit_conditions": [{"text": "2mm", "quote": "2mm"}],
        "main_task": "check_spec", "customer_question": "有哪些材料和尺寸？", "decision_required": "判断规格是否适用", "answers_needed": ["材料与厚度"],
        "evidence": ["gasket"], "inferred_fields": ["main_task", "customer_question", "decision_required", "answers_needed"], "uncertainties": [],
    }]}


def test_partial_validation_accounts_for_missing_duplicate_and_invalid_evidence():
    records = [{"id": x, "original": "2mm gasket"} for x in "abcd"]
    a, b, c, d = [interpretation(r) for r in records]
    c["candidates"][0]["explicit_conditions"][0]["quote"] = "FDA certified"
    result = validate_results({"results": [a, b, b, c]}, records)
    assert [r["status"] for r in result] == ["ready", "failed", "failed", "failed"]
    assert {r["input_id"] for r in result} == set("abcd")
    with pytest.raises(ValueError, match="未知输入 ID"):
        validate_results({"results": [dict(a, input_id="other-project")]}, records)


def test_multiple_intents_and_uncertainty_remain_reviewable():
    r = {"id": "a", "original": "2mm gasket"}
    item = interpretation(r)
    item["candidates"].append(copy.deepcopy(item["candidates"][0]))
    result = validate_results({"results": [item]}, [r])[0]
    assert result["status"] == "needs_review"
    assert len(result["payload"]["candidates"]) == 2
    item["candidates"] = item["candidates"][:1]
    item["candidates"][0]["uncertainties"] = ["材料未知"]
    assert validate_results({"results": [item]}, [r])[0]["status"] == "needs_review"


@pytest.fixture
def setup(tmp_path):
    store = Store(tmp_path / "test.sqlite3")
    settings = Settings(BASE_URL="https://provider.example/v1", model="test", api_key="private-test-key", intent_batch_size=2)
    project = store.create_project("Synthetic", "https://example.com")["id"]
    profile = store.save_profile(project, {"facts": [{"category": "product", "statement": "Industrial gaskets", "status": "confirmed", "scope": "industrial", "citations": []}], "gaps": []}, "test fixture", 0)
    records = import_records(store, project, {"kind": "keyword", "format": "lines", "text": "2mm gasket\n2mm silicone gasket", "request_id": "intent-test"}, commit=True)["records"]
    return store, settings, project, profile, records


def wait_job(client, base):
    for _ in range(200):
        snapshot = client.get(base).json()
        if snapshot["jobs"][0]["status"] in {"failed", "succeeded", "cancelled"}:
            return snapshot
        time.sleep(.01)
    pytest.fail("job did not finish")


def test_job_prerequisite_partial_retry_and_project_isolation(setup, monkeypatch):
    store, settings, project, profile, records = setup
    other = store.create_project("Other", "https://example.org")["id"]
    calls = []
    def generate(settings, batch, profile):
        calls.append([r["id"] for r in batch])
        results = [interpretation(r) for r in batch]
        if len(calls) == 1:
            results = results[:1]
        return validate_results({"results": results}, batch), {"total_tokens": 100, "cost": None}
    monkeypatch.setattr("pagggle.app.generate_intents", generate)
    base = f"/api/projects/{project}"
    with TestClient(create_app(settings, store)) as client:
        assert client.post(base + "/jobs", json={"kind": "intents"}).status_code == 409
        store.confirm_profile(project, 1, "test approval")
        assert client.post(base + "/jobs", json={"kind": "intents"}).status_code == 202
        assert wait_job(client, base)["jobs"][0]["status"] == "failed"
        first = store.intents(project)
        good = next(r for r in first if r["status"] == "ready")
        failed = next(r for r in first if r["status"] == "failed")
        client.post(base + "/jobs", json={"kind": "intents"})
        assert wait_job(client, base)["jobs"][0]["status"] == "succeeded"
        assert calls[1] == [failed["demand_id"]]
        assert next(r for r in store.intents(project) if r["demand_id"] == good["demand_id"])["id"] == good["id"]
        assert client.get(f"/api/projects/{other}/intents").json() == []
        assert client.post(base + "/jobs", json={"kind": "intents"}).status_code == 409
        assert sum(r["status"] == "pending" for r in store.demands(project)) == 2


def test_cancel_version_change_and_cross_project_do_not_write(setup):
    store, settings, project, profile, records = setup
    store.confirm_profile(project, 1, "approved")
    job = store.create_job(project, "intents")
    store.update_job(project, job, status="running")
    result = validate_results({"results": [interpretation(records[0])]}, records[:1])
    with pytest.raises(ValueError, match="不属于"):
        store.save_intents(project, job, 1, [{**result[0], "input_id": "foreign-id"}], "test", {})
    store.save_profile(project, profile["payload"], "changed", 1)
    with pytest.raises(ValueError, match="已变化"):
        store.save_intents(project, job, 1, result, "test", {})
    store.update_job(project, job, status="cancelled")
    with pytest.raises(ValueError, match="已取消"):
        store.save_intents(project, job, 1, result, "test", {})
    assert store.intents(project) == []


def test_model_request_omits_metrics_and_secrets_from_prompt(setup, monkeypatch):
    store, settings, project, profile, records = setup
    def handler(request):
        data = json.loads(request.content)
        prompt = json.loads(data["messages"][1]["content"])
        assert set(prompt["inputs"][0]) == {"id", "original", "kind", "market", "language"}
        assert "private-test-key" not in data["messages"][0]["content"] + data["messages"][1]["content"]
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps({"results": [interpretation(r) for r in records]})}}], "usage": {"total_tokens": 100}})
    original = httpx.Client
    monkeypatch.setattr("pagggle.intents.httpx.Client", lambda **kwargs: original(transport=httpx.MockTransport(handler), **kwargs))
    result, usage = generate_intents(settings, records, profile)
    assert all(r["status"] == "ready" for r in result)
    assert usage["cost"] is None


def test_fake_ip_dns_has_actionable_error(monkeypatch):
    from pagggle.crawl import CrawlError, public_address
    monkeypatch.setattr("socket.getaddrinfo", lambda *a, **kw: [(2, 1, 6, "", ("198.18.1.144", 443))])
    with pytest.raises(CrawlError, match="Fake-IP"):
        public_address("example.com", 443)

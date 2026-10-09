import json

import httpx
import pytest

from pagggle.config import Settings
from pagggle.understanding import Understanding, generate_understanding, validate_evidence


@pytest.fixture
def settings():
    return Settings(BASE_URL="https://provider.example/v1", model="configured-model", api_key="private-test-key")


@pytest.fixture
def source():
    return {"id": "source-a", "project_id": "project-a", "title": "Products", "status": "read", "kind": "page", "body": "We manufacture silicone gaskets. Samples are accepted."}


def profile(source_id="source-a", quote="We manufacture silicone gaskets.", status="stated"):
    return {"facts": [{"category": "product", "statement": "生产硅胶密封垫", "status": status, "scope": "硅胶密封垫", "citations": [{"source_id": source_id, "quote": quote}]}], "gaps": ["目标市场未明确"]}


@pytest.mark.parametrize("payload", [profile(source_id="other-project-source"), profile(quote="FDA certified"), profile(status="confirmed")])
def test_invalid_model_evidence_is_rejected(source, payload):
    with pytest.raises(ValueError):
        validate_evidence(Understanding.model_validate(payload), [source], from_model=True)


def test_failed_sources_cannot_prove_facts(source):
    source["status"] = "failed"
    with pytest.raises(ValueError):
        validate_evidence(Understanding.model_validate(profile()), [source])


def mock_model(monkeypatch, handler):
    real_client = httpx.Client
    monkeypatch.setattr("pagggle.understanding.httpx.Client", lambda **kwargs: real_client(transport=httpx.MockTransport(handler), **kwargs))


def test_model_uses_only_supplied_project_context_and_auth_header(monkeypatch, settings, source):
    def handler(request):
        assert str(request.url) == "https://provider.example/v1/chat/completions"
        assert request.headers["authorization"] == "Bearer private-test-key"
        body = json.loads(request.content)
        assert body["model"] == "configured-model"
        assert "private-test-key" not in request.content.decode()
        context = json.loads(body["messages"][1]["content"])
        assert [s["id"] for s in context] == ["source-a"]
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(profile())}}], "usage": {"total_tokens": 300}})
    mock_model(monkeypatch, handler)
    payload, usage = generate_understanding(settings, [source])
    assert payload["facts"][0]["status"] == "stated"
    assert usage["total_tokens"] == 300 and usage["cost"] is None


@pytest.mark.parametrize("response", [httpx.Response(401, text="private-test-key"), httpx.Response(200, json={"choices": [{"message": {"content": '{"api_key":"private-test-key"}'}}]})])
def test_provider_errors_do_not_leak_response_or_credentials(monkeypatch, settings, source, response):
    mock_model(monkeypatch, lambda request: response)
    with pytest.raises(ValueError) as error:
        generate_understanding(settings, [source])
    assert "private-test-key" not in str(error.value)


def test_thinking_configuration_and_truncation_error(monkeypatch, settings, source):
    settings.model_thinking = "disabled"
    def handler(request):
        assert json.loads(request.content)["thinking"] == {"type": "disabled"}
        return httpx.Response(200, json={"choices": [{"finish_reason": "length", "message": {"content": ""}}]})
    mock_model(monkeypatch, handler)
    with pytest.raises(ValueError, match="预算已耗尽"):
        generate_understanding(settings, [source])

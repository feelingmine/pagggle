import json

import pytest

from pagggle.config import load_settings
from pagggle.store import Store


def test_configuration_never_exposes_secrets(tmp_path):
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"BASE_URL": "https://example.com/v1", "model": "test", "api_key": "private-test-value"}))
    settings = load_settings(path)
    assert "private-test-value" not in repr(settings)
    path.write_text('{"api_key":"private-test-value", "bad":true}')
    with pytest.raises(RuntimeError) as error:
        load_settings(path)
    assert "private-test-value" not in str(error.value)


def test_sources_are_project_scoped_and_persistent(tmp_path):
    path = tmp_path / "test.sqlite3"
    store = Store(path)
    a = store.create_project("A", "https://example.com")
    b = store.create_project("B", "https://example.org")
    source = store.add_source(a["id"], title="private", body="customer drawing")
    assert Store(path).sources(a["id"])[0]["id"] == source["id"]
    assert store.sources(b["id"]) == []
    with pytest.raises(LookupError):
        store.sources("missing-project")


def test_retry_preserves_source_id_and_read_snapshot(tmp_path):
    store = Store(tmp_path / "test.sqlite3")
    p = store.create_project("A", "https://example.com")["id"]
    failed = store.add_source(p, title="page", url="https://example.com", status="failed", error="timeout")
    read = store.add_source(p, title="page", url="https://example.com", body="original")
    retry = store.add_source(p, title="page", url="https://example.com", body="replacement")
    assert failed["id"] == read["id"] == retry["id"]
    assert retry["body"] == "original"
    assert len(store.sources(p)) == 1


def test_profile_version_conflict_and_history(tmp_path):
    store = Store(tmp_path / "test.sqlite3")
    p = store.create_project("A", "https://example.com")["id"]
    first = store.save_profile(p, {"facts": []}, "first", 0, "confirmed")
    second = store.save_profile(p, {"facts": ["edited"]}, "correction", 1)
    assert first["version"] == 1 and second["version"] == 2
    assert second["status"] == "draft"
    with pytest.raises(ValueError):
        store.save_profile(p, {}, "stale edit", 1)
    assert store.profiles(p)[1]["payload"] == {"facts": []}

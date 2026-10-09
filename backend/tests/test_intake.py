import pytest

from pagggle.intake import import_records
from pagggle.store import Store


@pytest.fixture
def project(tmp_path):
    store = Store(tmp_path / "test.sqlite3")
    p = store.create_project("A", "https://example.com")["id"]
    return store, p


def request(text, **overrides):
    return {"kind": "keyword", "format": "lines", "text": text, "request_id": "test-request-1", **overrides}


def test_preview_accounts_for_every_record_without_writing(project):
    store, p = project
    data = request("custom silicone gasket\nCUSTOM silicone gasket\nEPDM 2mm gasket")
    preview = import_records(store, p, data)
    assert preview["summary"] == {"input": 3, "valid": 2, "duplicate": 1, "failed": 0}
    assert preview["records"][2]["original"] == "EPDM 2mm gasket"
    assert preview["records"][2]["volume"] is None
    assert store.demands(p) == []


def test_csv_unknown_zero_failure_and_conditions_preserved(project):
    store, p = project
    data = request("keyword,volume,kd,market,language,source,data_date\nEPDM 2mm gasket,0,,US,en,manual,2026-10-08\ncustom gasket,,15,DE,en,manual,2026-10-08\nfood gasket,-1,200,US,en,manual,invalid", format="csv")
    result = import_records(store, p, data, commit=True)
    assert result["summary"] == {"input": 3, "valid": 2, "duplicate": 0, "failed": 1}
    rows = result["records"]
    assert rows[0]["volume"] == 0 and rows[0]["kd"] is None
    assert rows[1]["volume"] is None and rows[1]["kd"] == 15
    assert "food gasket" == rows[2]["original"] and rows[2]["status"] == "failed"
    assert len(store.demands(p)) == 3


def test_retry_is_idempotent_and_request_conflict_is_rejected(project):
    store, p = project
    data = request("custom gasket")
    first = import_records(store, p, data, commit=True)
    retry = import_records(store, p, data, commit=True)
    assert first == retry and len(store.demands(p)) == 1
    with pytest.raises(ValueError):
        import_records(store, p, request("different input"), commit=True)
    duplicate = import_records(store, p, request("custom gasket", request_id="test-request-2"), commit=True)
    assert duplicate["records"][0]["duplicate_of"] == first["records"][0]["id"]


def test_dedupe_is_project_and_metric_scoped(project):
    store, p = project
    b = store.create_project("B", "https://other.example")["id"]
    data = request("gasket")
    import_records(store, p, data, commit=True)
    assert import_records(store, b, data)["summary"]["valid"] == 1
    assert import_records(store, p, request("keyword,volume\ngasket,100\ngasket,200", format="csv"))["summary"]["valid"] == 2


def test_customer_question_is_one_record_without_fake_keyword(project):
    store, p = project
    data = request("Can you work from samples?\nWe do not have a drawing.", kind="customer_question")
    result = import_records(store, p, data, commit=True)
    assert result["summary"]["input"] == 1
    assert result["records"][0]["kind"] == "customer_question"
    assert result["records"][0]["volume"] is None


@pytest.mark.parametrize("text", ["unknown\nvalue", "keyword,keyword\none,two", 'keyword,volume\n"broken,20'])
def test_malformed_csv_rejected(project, text):
    store, p = project
    with pytest.raises(ValueError):
        import_records(store, p, request(text, format="csv"))


def test_csv_missing_columns_and_nonfinite_metrics_accounted_as_failures(project):
    store, p = project
    data = request("keyword,volume,kd\ngasket,NaN,20\nseal,10\nseal,Infinity,10", format="csv")
    assert import_records(store, p, data)["summary"] == {"input": 3, "valid": 0, "duplicate": 0, "failed": 3}

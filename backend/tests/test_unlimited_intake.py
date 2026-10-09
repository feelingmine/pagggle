import base64
import csv
import io

import pytest
from fastapi.testclient import TestClient
from openpyxl import Workbook
from openpyxl.styles import Font

from pagggle.app import create_app
from pagggle.config import Settings
from pagggle.keyword_data import read_table
from pagggle.store import Store


def wide_table(format):
    rows = [["Keyword", "Search Volume"] + [f"Extra {i}" for i in range(58)]]
    rows += [[f"gasket {i}", "10"] + [f"row {i} column {j}" for j in range(58)] for i in range(1005)]
    rows += [rows[1], ["bad metric", "-1"] + [""] * 58]
    if format == "csv":
        output = io.StringIO()
        csv.writer(output).writerows(rows)
        return output.getvalue()
    workbook = Workbook()
    for row in rows:
        workbook.active.append(row)
    # Formatting beyond actual data must not create artificial empty headers.
    workbook.active.cell(1, 80).font = Font(bold=True)
    output = io.BytesIO()
    workbook.save(output)
    return base64.b64encode(output.getvalue()).decode()


@pytest.mark.parametrize("format", ["csv", "xlsx"])
def test_wide_large_import_accounts_for_all_rows_and_preserves_extra_columns(tmp_path, format):
    store = Store(tmp_path / "test.sqlite3")
    project = store.create_project("Large import", "https://example.com")["id"]
    other = store.create_project("Other", "https://other.example")["id"]
    settings = Settings(BASE_URL="https://provider.example/v1", model="test", api_key="")
    payload = {"format": format, "text": wide_table(format), "request_id": "large-import-" + format}
    expected = {"input": 1007, "valid": 1005, "duplicate": 1, "failed": 1}
    with TestClient(create_app(settings, store)) as client:
        preview = client.post(f"/api/projects/{project}/demands/preview", json=payload)
        assert preview.status_code == 200
        assert preview.json()["summary"] == expected
        assert store.demands(project) == []
        saved = client.post(f"/api/projects/{project}/demands/import", json=payload)
        assert saved.status_code == 201
        assert saved.json()["summary"] == expected
        assert saved.json()["records"][1004]["raw"]["Extra 57"] == "row 1004 column 57"
        assert client.post(f"/api/projects/{project}/demands/import", json=payload).json() == saved.json()
    assert len(store.demands(project)) == 1007
    assert store.demands(other) == []


def test_intake_over_old_request_size_limit_preserves_final_record(tmp_path):
    store = Store(tmp_path / "test.sqlite3")
    project = store.create_project("Large input", "https://example.com")["id"]
    settings = Settings(BASE_URL="https://provider.example/v1", model="test", api_key="")
    keywords = [f"gasket {i} " + "x" * 790 for i in range(4000)]
    payload = {"text": "\n".join(keywords), "request_id": "over-size-limit"}
    assert len(payload["text"]) > 3_000_000
    with TestClient(create_app(settings, store)) as client:
        response = client.post(f"/api/projects/{project}/demands/preview", json=payload)
        assert response.status_code == 200
        assert response.json()["summary"]["valid"] == 4000
        assert response.json()["records"][-1]["original"] == keywords[-1]
        # The exception is limited to intake; local origin checks still apply.
        assert client.post(f"/api/projects/{project}/demands/preview", json=payload, headers={"Origin": "https://other.example"}).status_code == 403
        assert client.post(f"/api/projects/{project}/sources", json={"title": "large", "body": payload["text"]}).status_code == 413


def test_wide_csv_without_keyword_header_still_rejected():
    with pytest.raises(ValueError, match="Keyword"):
        read_table(",".join(f"Extra {i}" for i in range(60)) + "\n" + ",".join("value" for _ in range(60)), "csv")

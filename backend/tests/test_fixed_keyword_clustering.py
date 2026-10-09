"""Opt-in regression using the user's unchanged XLSX and local encoder/config."""

import base64
import copy
import hashlib
import os

import pytest

from pagggle.clustering import cluster_keywords
from pagggle.config import ROOT, load_settings
from pagggle.intake import parse_records


@pytest.mark.skipif(os.environ.get("PAGGGLE_VERIFY_FIXED_DATA") != "1", reason="Requires the private fixed dataset and local model")
def test_fixed_keywords_preserve_semantic_groups_without_serp():
    source = ROOT / "backend/tests/data/silicone-supplier_all-keywords_us_2026-10-09.xlsx"
    content = source.read_bytes()
    assert hashlib.sha256(content).hexdigest() == "afacb52eb0d9b3d31a294e29750ab5be9c960da493b0678e3c7b59da5b23ab4d"
    records = parse_records(dict(kind="keyword", format="xlsx", text=base64.b64encode(content).decode(), market="us", language="en"))
    for record in records:
        record["id"] = record["fingerprint"]
    assert len(records) == 4869
    assert all(r["status"] == "pending" and r["serp"] is None for r in records)
    before = copy.deepcopy(records)

    result = cluster_keywords(load_settings(), records)
    groups = result["groups"]
    ids = [m["demand_id"] for g in groups for m in g["members"]]
    assert len(ids) == len(set(ids)) == len(records)
    assert set(ids) == {r["id"] for r in records}
    assert result["summary"]["groups"] == result["summary"]["silos"] < len(records)
    assert result["summary"]["serp_comparisons"] == 0
    assert any(len(g["members"]) > 1 for g in groups)
    # Guard against the previous 4,637-word catch-all as well as singleton splitting.
    assert max(len(g["members"]) for g in groups) < len(records) / 2
    for group in groups:
        assert group["basis"] == "semantic_only"
        assert group["status"] == "needs_evidence"
        assert group["pair_evidence"] == [] and not group["chain_overlap"]
        assert sum(m["keyword_role"] == "Primary" for m in group["members"]) == 1
    assert records == before

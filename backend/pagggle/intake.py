"""Accountable demand intake; no inferred search metrics or intentions."""

import hashlib
import json
import math
import re
from collections import Counter
from datetime import date

from .store import encode, now, uid
from .keyword_data import parse_serp, read_table


def fingerprint(record):
    fields = {key: record[key] for key in ("kind", "normalized", "market", "language", "volume", "kd", "source", "data_date")}
    if record.get("serp"):
        fields.update({key: record.get(key) for key in ("serp", "serp_date", "serp_source", "device")})
    return hashlib.sha256(json.dumps(fields, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def parse_records(data):
    if data["format"] in {"csv", "xlsx"}:
        raw_records = read_table(data["text"], data["format"])
    else:
        raw_records = [{"keyword": line.strip()} for line in data["text"].splitlines() if line.strip()]
        if data["kind"] != "keyword":
            raw_records = [{"keyword": data["text"].strip()}] if data["text"].strip() else []
    if not 1 <= len(raw_records) <= 1000:
        raise ValueError("每次需要 1–1000 条非空需求，请分批导入")
    records = []
    for index, raw in enumerate(raw_records, 1):
        text = str(raw.get("keyword") or "").strip()
        record = {"row": index, "kind": data["kind"], "original": text, "normalized": re.sub(r"\s+", " ", text).casefold(),
            "market": str(raw.get("market") or data.get("market") or "").strip() or None,
            "language": str(raw.get("language") or data.get("language") or "").strip() or None,
            "volume": None, "kd": None, "source": str(raw.get("source") or "用户导入").strip(),
            "data_date": raw.get("data_date") or None, "status": "pending", "error": None, "raw": raw,
            "serp": None, "serp_date": raw.get("serp_date") or raw.get("data_date") or None,
            "serp_source": raw.get("serp_source") or raw.get("source") or None,
            "device": str(raw.get("device") or "").strip().lower() or None}
        errors = []
        try:
            record["serp"] = parse_serp(raw.get("serp"))
        except ValueError as error:
            errors.append(str(error))
        if record["device"] not in {None, "desktop", "mobile", "tablet"}:
            errors.append("device 须为 desktop、mobile、tablet 或留空")
        if not text or len(text) > (1000 if data["kind"] == "keyword" else 10000):
            errors.append("需求内容为空或超出长度限制")
        if None in raw or any(v is None for v in raw.values()):
            errors.append("CSV 列数与表头不一致")
        for key in ("volume", "kd"):
            value = raw.get(key)
            if value is not None and str(value).strip():
                try:
                    number = float(value)
                    if not math.isfinite(number) or number < 0 or (key == "kd" and number > 100) or (key == "volume" and not number.is_integer()):
                        raise ValueError
                    record[key] = int(number) if key == "volume" else number
                except (ValueError, TypeError):
                    errors.append(f"{key} 指标无效；缺失值应留空")
        for key in ("data_date", "serp_date"):
            if record[key]:
                try:
                    date.fromisoformat(record[key])
                except ValueError:
                    errors.append(f"{key} 须为 YYYY-MM-DD")
        if errors:
            record.update(status="failed", error="；".join(errors))
        record["fingerprint"] = fingerprint(record)
        records.append(record)
    return records


def summarize(records):
    counts = Counter(record["status"] for record in records)
    return {"input": len(records), "valid": counts["pending"], "duplicate": counts["duplicate"], "failed": counts["failed"]}


def import_records(store, project_id, data, commit=False):
    store.project(project_id)
    records = parse_records(data)
    content_hash = hashlib.sha256(encode({k: v for k, v in data.items() if k != "request_id"}).encode()).hexdigest()
    with store.connect() as db:
        db.execute("BEGIN IMMEDIATE" if commit else "BEGIN")
        if commit:
            existing = db.execute("SELECT * FROM intake_batches WHERE project_id=? AND request_id=?", (project_id, data["request_id"])).fetchone()
            if existing:
                if existing["content_hash"] != content_hash:
                    raise ValueError("此导入标识已用于其他内容，请重新预览")
                return json.loads(existing["result"])
        known = {r["fingerprint"]: r["id"] for r in db.execute("SELECT id,fingerprint FROM demands WHERE project_id=? AND status='pending'", (project_id,))}
        batch_id = uid()
        for record in records:
            record["id"] = uid()
            record["duplicate_of"] = None
            if record["status"] == "pending":
                if record["fingerprint"] in known:
                    record.update(status="duplicate", duplicate_of=known[record["fingerprint"]])
                else:
                    known[record["fingerprint"]] = record["id"]
        result = {"batch_id": batch_id if commit else None, "summary": summarize(records), "records": records}
        if commit:
            timestamp = now()
            db.execute("INSERT INTO intake_batches VALUES (?,?,?,?,?,?,?,1)", (batch_id, project_id, data["request_id"], content_hash, encode(result), timestamp, timestamp))
            for record in records:
                db.execute("INSERT INTO demands VALUES (?,?,?,?,?,?,?,?,?,1)", (record["id"], project_id, batch_id, record["kind"], record["fingerprint"], record["status"], encode(record), timestamp, timestamp))
            store.event(db, project_id, "keyword_imported" if data["kind"] == "keyword" else "demand_added", {"batch_id": batch_id, **result["summary"]})
        return result

"""Two-stage offline candidates from the hybrid engine design, not approved pages."""

import hashlib
import threading
import time
from collections import defaultdict
from functools import lru_cache

from .config import ROOT


MODEL_LOCK = threading.Lock()
CONTEXT_FIELDS = ("market", "language", "serp_source", "serp_date", "device")


def stable_id(prefix, ids):
    return prefix + hashlib.sha256("\n".join(sorted(ids)).encode()).hexdigest()[:16]


def rank_key(record):
    volume, kd = record.get("volume"), record.get("kd")
    return (volume is None, -(volume or 0), kd is None, kd if kd is not None else 101, record["normalized"], record["id"])


@lru_cache(maxsize=1)
def load_encoder(model, cache):
    from sentence_transformers import SentenceTransformer

    try:
        return SentenceTransformer(model, cache_folder=str(ROOT / cache), device="cpu", local_files_only=True, trust_remote_code=False)
    except Exception:
        raise ValueError("本地聚类模型未就绪，请先运行 scripts/prepare-clustering-model.py；未将关键词发送到外部服务") from None


def encode_keywords(settings, texts, cancelled, progress):
    import numpy as np

    with MODEL_LOCK:
        if cancelled():
            raise ValueError("聚类已取消")
        progress("正在加载本地语义模型，关键词不发送到模型 API")
        model = load_encoder(settings.clustering_model, settings.clustering_model_cache)
        # Do not silently truncate long keyword strings at the encoder token limit.
        for text in texts:
            if len(model.tokenizer.encode(text, truncation=False)) > model.max_seq_length:
                raise ValueError("存在超出语义模型长度的关键词，请拆分长段文字；未截断输入")
        batches = []
        for offset in range(0, len(texts), 64):
            if cancelled():
                raise ValueError("聚类已取消")
            progress(f"语义编码 {offset + 1}–{min(offset + 64, len(texts))} / {len(texts)}")
            batches.append(model.encode(texts[offset:offset + 64], normalize_embeddings=True, show_progress_bar=False))
        return np.vstack(batches)


def cluster_keywords(settings, records, *, cancelled=lambda: False, progress=lambda message: None, encoder=None):
    import numpy as np
    from sklearn.cluster import AgglomerativeClustering

    started = time.perf_counter()
    if not records or len({r["id"] for r in records}) != len(records):
        raise ValueError("需要非空且 ID 唯一的关键词输入")
    if any(r["kind"] != "keyword" or r["status"] != "pending" for r in records):
        raise ValueError("聚类只接受有效关键词，重复及失败记录保留在导入清单")
    rows = sorted(records, key=lambda r: (r["normalized"], r["id"]))
    texts = [r["original"] for r in rows]
    vectors = np.asarray(encoder(texts) if encoder else encode_keywords(settings, texts, cancelled, progress), dtype=float)
    if vectors.ndim != 2 or vectors.shape[0] != len(rows) or not np.isfinite(vectors).all() or np.any(np.linalg.norm(vectors, axis=1) == 0):
        raise ValueError("语义向量无效，未保存聚类结果")
    if cancelled():
        raise ValueError("聚类已取消")
    progress("阶段 1/2：按余弦距离生成语义候选组")
    labels = AgglomerativeClustering(n_clusters=None, distance_threshold=settings.clustering_distance_threshold, metric="cosine", linkage="average").fit_predict(vectors) if len(rows) > 1 else [0]
    silos = defaultdict(list)
    for record, label in zip(rows, labels):
        silos[int(label)].append(record)
    groups, comparisons, chain_count = [], 0, 0
    threshold = settings.clustering_serp_threshold
    for silo in silos.values():
        silo_id = stable_id("silo_", [r["id"] for r in silo])
        # Different SERP markets/dates/devices are never compared as one snapshot.
        contexts = defaultdict(list)
        for record in silo:
            contexts[tuple(record.get(k) for k in CONTEXT_FIELDS)].append(record)
        for members in contexts.values():
            pages = []
            for record in members:
                if cancelled():
                    raise ValueError("聚类已取消")
                progress(f"阶段 2/2：核对组内 SERP，已生成 {len(groups)} 个候选组")
                serp = set(record.get("serp") or [])
                matched = None
                for page in pages:
                    comparisons += 1
                    if len(serp & page["pool"]) >= threshold:
                        matched = page
                        break
                if matched is None:
                    matched = {"members": [], "pool": set()}
                    pages.append(matched)
                matched["members"].append(record)
                matched["pool"].update(serp)
            for page in pages:
                ordered = sorted(page["members"], key=rank_key)
                pair_evidence, chain = [], False
                for i, left in enumerate(ordered):
                    if cancelled():
                        raise ValueError("聚类已取消")
                    for right in ordered[i + 1:]:
                        shared = sorted(set(left.get("serp") or []) & set(right.get("serp") or []))
                        if len(shared) < threshold:
                            chain = True
                        pair_evidence.append({"left_id": left["id"], "right_id": right["id"], "shared_urls": shared, "overlap": len(shared)})
                missing = [r["id"] for r in ordered if not r.get("serp") or len(r["serp"]) < threshold or any(not r.get(k) for k in CONTEXT_FIELDS)]
                reasons = []
                if missing:
                    reasons.append("SERP 缺失、结果不足或市场/语言/来源/日期/设备不完整；不能据此确认同页或分开建页")
                if chain:
                    reasons.append("并集匹配产生链式关联：并非每对成员都有足够重合，必须人工审核，不能直接合成一页")
                    chain_count += 1
                if not reasons:
                    reasons.append("SERP 重合仅为候选依据，主要客户任务与同页覆盖仍需审核")
                groups.append({
                    "semantic_silo_id": silo_id,
                    "target_page_id": stable_id("candidate_", [r["id"] for r in ordered]),
                    "status": "needs_evidence" if missing else "needs_review",
                    "chain_overlap": chain, "reasons": reasons, "pair_evidence": pair_evidence,
                    "context": {k: ordered[0].get(k) for k in CONTEXT_FIELDS},
                    "primary_provisional": any(r.get("volume") is None or r.get("kd") is None for r in ordered),
                    "members": [{"demand_id": r["id"], "keyword": r["original"], "volume": r.get("volume"), "kd": r.get("kd"), "serp": r.get("serp"), "keyword_role": "Primary" if i == 0 else "Secondary"} for i, r in enumerate(ordered)],
                })
    if cancelled():
        raise ValueError("聚类已取消")
    groups.sort(key=lambda g: g["target_page_id"])
    return {"groups": groups, "settings": {
        "model": settings.clustering_model, "distance_threshold": settings.clustering_distance_threshold,
        "serp_match_threshold": threshold, "metric": "cosine", "linkage": "average", "representation": "keyword_only",
    }, "summary": {"keywords": len(rows), "silos": len(silos), "groups": len(groups), "needs_evidence": sum(g["status"] == "needs_evidence" for g in groups), "chain_groups": chain_count, "serp_comparisons": comparisons}, "elapsed_seconds": round(time.perf_counter() - started, 3)}

"""Download public model files only; never load customer records or API secrets."""

import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
config = json.loads((ROOT / "config.json").read_text())
model_name = config.get("clustering_model", "sentence-transformers/all-MiniLM-L6-v2")
cache = ROOT / config.get("clustering_model_cache", "data/models")
cache.mkdir(parents=True, exist_ok=True)
cache.chmod(0o700)
os.environ["HF_HUB_DISABLE_TELEMETRY"] = "1"
os.environ["HF_HUB_DISABLE_XET"] = "1"

from sentence_transformers import SentenceTransformer

model = SentenceTransformer(model_name, cache_folder=str(cache), device="cpu", trust_remote_code=False)
print(json.dumps({"model": model_name, "dimensions": model.get_embedding_dimension(), "ready": True}))

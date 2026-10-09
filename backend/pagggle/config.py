from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator

ROOT = Path(__file__).resolve().parents[2]


class ContentWorkflowSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    seed_file: str
    keyword_files: list[str]
    competitor_urls: list[str]
    own_evidence_urls: list[str] = Field(default_factory=list)
    own_scope: Literal["selected", "all_discovered"] = "selected"
    evidence_concurrency: int = Field(default=4, ge=1, le=8)
    market: str = "us"
    language: str = "en"


class Settings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    BASE_URL: str
    model: str = Field(min_length=1)
    api_key: SecretStr
    database_path: str = "data/pagggle.sqlite3"
    host: str = "127.0.0.1"
    port: int = Field(default=8000, ge=1024, le=65535)
    crawl_max_pages: int | None = Field(default=None, ge=1)
    crawl_dns: Literal["system", "cloudflare"] = "system"
    crawl_backend: Literal["direct", "firecrawl"] = "direct"
    firecrawl_api_key: SecretStr = SecretStr("")
    dataforseo_login: SecretStr = SecretStr("")
    dataforseo_password: SecretStr = SecretStr("")
    scrape_timeout_seconds: int = Field(default=60, ge=10, le=180)
    request_timeout_seconds: int = Field(default=30, ge=1, le=120)
    model_timeout_seconds: int = Field(default=120, ge=1, le=300)
    model_max_tokens: int = Field(default=6000, ge=256, le=16000)
    model_thinking: Literal["enabled", "disabled"] | None = None
    model_input_chars: int = Field(default=60000, ge=1000, le=200000)
    intent_batch_size: int = Field(default=5, ge=1, le=20)
    clustering_model: str = "sentence-transformers/all-MiniLM-L6-v2"
    clustering_model_cache: str = "data/models"
    clustering_distance_threshold: float = Field(default=0.65, gt=0, le=2, allow_inf_nan=False)
    clustering_serp_threshold: int = Field(default=3, ge=1, le=10)
    content_workflows: dict[str, ContentWorkflowSettings] = Field(default_factory=dict)
    strategy_skill_files: dict[str, str] = Field(default_factory=dict)
    strategy_concurrency: int = Field(default=10, ge=1, le=64)
    strategy_keyword_batch_size: int = Field(default=600, ge=20, le=1000)

    @field_validator("BASE_URL")
    @classmethod
    def validate_endpoint(cls, value):
        from urllib.parse import urlsplit

        parsed = urlsplit(value)
        if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError("Model endpoint must be an HTTPS URL without credentials or query")
        return value.rstrip("/")

    @field_validator("host")
    @classmethod
    def local_only(cls, value):
        if value != "127.0.0.1":
            raise ValueError("This local pilot must bind to 127.0.0.1")
        return value


def load_settings(path: Path | None = None) -> Settings:
    try:
        return Settings.model_validate_json((path or ROOT / "config.json").read_text())
    except (OSError, ValueError):
        # Validation errors may contain raw input, including credentials.
        raise RuntimeError("配置无效：请对照 config.example.json 检查本地 config.json。") from None

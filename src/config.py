from __future__ import annotations

from pathlib import Path
from typing import List, Optional

import yaml
from pydantic import BaseModel, Field, field_validator

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "config.yaml"
ALLOWED_PLAN = "Direct Growth"
EXPECTED_ALLOWLIST_SIZE = 5


class ProjectConfig(BaseModel):
    name: str
    amc: str
    mode: str


class SchemeSource(BaseModel):
    scheme_id: str
    category: str
    scheme_name: str
    url: str

    @field_validator("scheme_id")
    @classmethod
    def scheme_id_is_slug(cls, value: str) -> str:
        if not value or not value.replace("_", "").isalnum():
            raise ValueError(f"scheme_id must be an alphanumeric slug, got {value!r}")
        return value


class SourcesConfig(BaseModel):
    allowlist: List[SchemeSource]
    user_agent: str
    timeout_s: int = 20
    max_retries: int = 3

    @field_validator("allowlist")
    @classmethod
    def allowlist_is_sane(
        cls, value: List[SchemeSource]
    ) -> List[SchemeSource]:
        if len(value) != EXPECTED_ALLOWLIST_SIZE:
            raise ValueError(
                f"allowlist must hold exactly {EXPECTED_ALLOWLIST_SIZE} entries, "
                f"got {len(value)}"
            )
        scheme_ids = [entry.scheme_id for entry in value]
        if len(set(scheme_ids)) != len(scheme_ids):
            raise ValueError(f"duplicate scheme_id in allowlist: {scheme_ids}")
        urls = [entry.url for entry in value]
        if len(set(urls)) != len(urls):
            raise ValueError(f"duplicate url in allowlist: {urls}")
        return value

    def by_scheme_id(self, scheme_id: str) -> SchemeSource:
        for entry in self.allowlist:
            if entry.scheme_id == scheme_id:
                return entry
        raise KeyError(scheme_id)

    def by_url(self, url: str) -> SchemeSource:
        for entry in self.allowlist:
            if entry.url == url:
                return entry
        raise KeyError(url)

    @property
    def scheme_ids(self) -> List[str]:
        return [entry.scheme_id for entry in self.allowlist]

    @property
    def urls(self) -> List[str]:
        return [entry.url for entry in self.allowlist]


class LoadConfig(BaseModel):
    drop_selectors: List[str]
    min_text_chars: int = 1500


class ChunkConfig(BaseModel):
    strategy: str = "auto"
    semantic_available: bool = True
    size_tokens: int = 200
    overlap_tokens: int = 40
    separators: List[str]
    never_split: List[str]
    holdings_mode: str = "sector_summary"

    @field_validator("overlap_tokens")
    @classmethod
    def overlap_below_size(cls, value: int, info) -> int:
        size = info.data.get("size_tokens")
        if size is not None and value >= size:
            raise ValueError(f"overlap_tokens {value} must be below size_tokens {size}")
        return value


class EmbedConfig(BaseModel):
    model_id: str
    dim: int
    max_seq_length: int
    prefix_template: str
    normalize: bool = True
    batch_size: int = 32
    assert_no_truncation: bool = True

    def render_prefix(self, scheme_name: str, category: str, text: str) -> str:
        return self.prefix_template.format(
            scheme_name=scheme_name, category=category, text=text
        )


class StoreConfig(BaseModel):
    backend: str = "chromadb"
    path: str
    collection: str
    metric: str = "cosine"


class RetrieveConfig(BaseModel):
    top_k: int = 6
    mmr_lambda: float = 0.3
    oversample_factor: int = 3
    sim_floor: float = 0.30

    @property
    def candidate_k(self) -> int:
        return self.top_k * self.oversample_factor


class GenerateConfig(BaseModel):
    max_sentences: int = 3
    temperature: float = 0.0
    max_tokens: int = 220
    citations: str = "exactly_one"
    retry_on_validation_failure: int = 1
    require_scheme_and_plan_in_answer: bool = True


class GuardsConfig(BaseModel):
    pii_patterns_enabled: bool = True
    redact_pii_from_logs: bool = True
    max_query_chars: int = 500


class AppConfig(BaseModel):
    project: ProjectConfig
    sources: SourcesConfig
    load: LoadConfig
    chunk: ChunkConfig
    embed: EmbedConfig
    store: StoreConfig
    retrieve: RetrieveConfig
    generate: GenerateConfig
    guards: GuardsConfig
    config_path: Optional[str] = None


_CACHE: dict = {}


def load_config(path: Optional[Path] = None) -> AppConfig:
    resolved = Path(path) if path is not None else DEFAULT_CONFIG_PATH
    key = str(resolved.resolve())
    if key in _CACHE:
        return _CACHE[key]
    if not resolved.exists():
        raise FileNotFoundError(f"config not found: {resolved}")
    with resolved.open("r", encoding="utf-8") as handle:
        raw = yaml.safe_load(handle)
    config = AppConfig.model_validate({**raw, "config_path": key})
    _CACHE[key] = config
    return config


def clear_config_cache() -> None:
    _CACHE.clear()


__all__ = [
    "ALLOWED_PLAN",
    "AppConfig",
    "ChunkConfig",
    "DEFAULT_CONFIG_PATH",
    "EmbedConfig",
    "GenerateConfig",
    "GuardsConfig",
    "LoadConfig",
    "PROJECT_ROOT",
    "ProjectConfig",
    "RetrieveConfig",
    "SchemeSource",
    "SourcesConfig",
    "StoreConfig",
    "clear_config_cache",
    "load_config",
]

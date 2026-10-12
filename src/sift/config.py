"""Runtime settings from environment variables (and an optional .env file). See .env.example."""

from __future__ import annotations

import datetime as dt
from functools import lru_cache
from pathlib import Path
from typing import Annotated, Literal

from pydantic import AliasChoices, Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

LLMProviderKind = Literal["fake", "openrouter", "qwen", "openai", "anthropic"]
StrategyKind = Literal["auto", "learned", "direct"]

# Free OpenRouter models chosen from the smoke test before the real run (results/smoke.json).
DEFAULT_FREE_MODEL = "nvidia/nemotron-3-super-120b-a12b:free"
DEFAULT_FREE_FALLBACKS = ["nvidia/nemotron-3-ultra-550b-a55b:free"]
# Qwen Cloud (Alibaba Model Studio / DashScope), OpenAI-compatible. The international pay-as-you-go endpoint is the
# default; the Token Plan endpoint is https://token-plan.ap-southeast-1.maas.aliyuncs.com/compatible-mode/v1 and
# mainland China is https://dashscope.aliyuncs.com/compatible-mode/v1.
DEFAULT_QWEN_BASE_URL = "https://dashscope-intl.aliyuncs.com/compatible-mode/v1"
DEFAULT_QWEN_MODEL = "qwen3.7-plus"
DEFAULT_MODELS: dict[str, str] = {
    "fake": "fake-rules",
    "openrouter": DEFAULT_FREE_MODEL,
    "qwen": DEFAULT_QWEN_MODEL,
    "openai": "gpt-5-mini",
    "anthropic": "claude-sonnet-5",
}
USER_AGENT = "SiftBot/0.1 (+https://github.com/gelevanog/ai-web-scraper; polite structured-data crawler)"


def _split(value: object) -> object:
    if isinstance(value, str):
        return [item.strip() for item in value.split(",") if item.strip()]
    return value


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="SIFT_", extra="ignore")

    # ---- storage
    database_url: str = "sqlite:///.cache/sift.db"
    """SQLAlchemy URL. SQLite for a laptop, PostgreSQL in Docker (postgresql+psycopg://...)."""
    seed_demo: bool = True
    """On API startup: generate the synthetic web when missing and queue the demo job when the database is empty."""

    # ---- synthetic web
    synthetic_web_dir: Path = Path("data/synthetic_web")
    """Where `sift seed` writes the generated sites (gitignored): <dir>/<v1|v2>/<domain>/..."""
    synthetic_web_url: str = ""
    """If set (e.g. http://synthetic-web), *.example URLs are fetched from this static server (Host header routing);
    otherwise they are served from disk in-process. Real domains are always fetched over the network."""
    synthetic_layout: Literal["v1", "v2"] = "v1"
    """Which layout of the synthetic web is served (v2 = the redesign used for the robustness evaluation)."""
    demo_seed: int = 7
    today: dt.date | None = dt.date(2026, 10, 1)
    """The date the crawler treats as today for relative dates ("3 days ago"); fixed so the synthetic web's ground
    truth is reproducible. Empty = the real date."""

    # ---- crawling and politeness
    user_agent: str = USER_AGENT
    respect_robots: bool = True
    """Never turned off by Sift itself; a client crawling their own site may switch it off deliberately."""
    min_seconds_per_domain: float = 1.0
    """At most one request per this many seconds to the same host (robots.txt Crawl-delay wins when larger)."""
    synthetic_min_seconds_per_domain: float = 0.0
    """The same limit for the synthetic web served from disk (no real server to protect)."""
    max_pages: int = 60
    max_depth: int = 4
    fetch_timeout_seconds: float = 20.0
    fetch_retries: int = 2
    max_page_bytes: int = 3_000_000
    browser: Literal["auto", "always", "never"] = "auto"
    """auto = Playwright only for pages that need JavaScript (empty shell, infinite scroll, load-more buttons)."""
    browser_executable: str = ""
    """Path to Chrome/Chromium for Playwright; empty = Playwright's own Chromium (`playwright install chromium`)."""
    browser_max_scrolls: int = 12

    # ---- extraction
    strategy: StrategyKind = "auto"
    learn_sample_pages: int = 3
    learn_max_attempts: int = 3
    heal: bool = True
    heal_max_pages: int = 3
    injection_guard: bool = True
    pii_guard: bool = True
    fx_file: Path = Path("configs/fx.yaml")
    anomaly_factor: float = 20.0
    """A numeric value more than this many times the median of its field (or less than 1/this) is flagged."""

    # ---- LLM
    llm_provider: LLMProviderKind = "fake"
    llm_model: str = ""
    """Empty = the provider's default (DEFAULT_MODELS)."""
    llm_fallback_models: Annotated[list[str], NoDecode] = Field(default_factory=list)
    llm_timeout_seconds: float = 240.0
    llm_max_tokens: int = 16000
    """A ceiling, not a target: free reasoning models spend thousands of tokens thinking before the JSON."""
    llm_temperature: float = 0.0
    llm_reasoning_effort: str = "low"
    require_free_models: bool = True
    """Refuse any OpenRouter model id that does not end in ":free" (requests, fallbacks and the model that answered)."""

    openrouter_api_key: str | None = Field(default=None, validation_alias=AliasChoices("OPENROUTER_API_KEY"))
    openrouter_base_url: str = Field(
        default="https://openrouter.ai/api/v1", validation_alias=AliasChoices("OPENROUTER_BASE_URL")
    )
    qwen_api_key: str | None = Field(default=None, validation_alias=AliasChoices("QWEN_API_KEY", "DASHSCOPE_API_KEY"))
    qwen_base_url: str = DEFAULT_QWEN_BASE_URL
    qwen_enable_thinking: bool | None = None
    openai_api_key: str | None = Field(default=None, validation_alias=AliasChoices("OPENAI_API_KEY"))
    openai_base_url: str = "https://api.openai.com/v1"
    anthropic_api_key: str | None = Field(default=None, validation_alias=AliasChoices("ANTHROPIC_API_KEY"))
    anthropic_effort: str = "low"

    # ---- budget for real API calls
    llm_cache: bool = True
    llm_cache_dir: Path = Path(".cache/llm")
    llm_ledger: Path | None = Path("results/calls.jsonl")
    llm_max_calls: int = 495
    llm_min_seconds_between_requests: float = 3.0
    llm_max_retries: int = 3

    # ---- outputs
    export_dir: Path = Path("data/exports")
    webhook_url: str = ""
    webhook_secret: str = ""
    """Optional HMAC-SHA256 key: the webhook request carries X-Sift-Signature: sha256=<hex>."""

    # ---- API and worker
    api_token: str = ""
    """If set, every /api request needs `Authorization: Bearer <token>` (the dashboard sends NEXT_PUBLIC_API_TOKEN)."""
    cors_origins: Annotated[list[str], NoDecode] = Field(default_factory=lambda: ["http://localhost:3000"])
    results_dir: Path = Path("results")
    worker_poll_seconds: float = 1.0
    worker_concurrency: int = 2

    @field_validator("llm_fallback_models", "cors_origins", mode="before")
    @classmethod
    def _split_lists(cls, value: object) -> object:
        return _split(value)

    @field_validator("today", "llm_ledger", "qwen_enable_thinking", mode="before")
    @classmethod
    def _empty_is_none(cls, value: object) -> object:
        return None if value == "" else value

    def crawl_today(self) -> dt.date:
        return self.today or dt.date.today()


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()

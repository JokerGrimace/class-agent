from pathlib import Path
from typing import Any, Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="OPENCLAW_",
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
    )

    data_dir: Path = Path("data")
    sessions_dir: Path = Path("data/sessions")
    workspace_dir: Path = Path("data/workspace")

    llm_provider: Literal["openai", "ollama"] = "openai"

    openai_api_key: str = ""
    openai_model: str = ""
    openai_base_url: str = ""

    ollama_base_url: str = ""
    ollama_model: str = ""

    tool_timeout_ms: int = 120000
    max_concurrent_tools: int = 3
    max_turns: int = 10
    strict_plan_max_turns: int = 30

    web_search_provider: Literal["auto", "brave", "searxng", "jina", "disabled"] = "auto"
    brave_api_key: str = ""
    searxng_base_url: str = ""
    jina_api_key: str = ""
    jina_search_base_url: str = ""
    web_search_timeout_seconds: int = 15
    web_fetch_timeout_seconds: int = 20
    web_search_max_results: int = 5
    web_fetch_max_chars: int = 12000

    # Context window settings
    # Override to cap the context window (0 = no override, use model default)
    context_tokens: int = 0
    # Default context window when model info is unavailable
    default_context_tokens: int = 128_000
    # Minimum context window required (models below this are rejected)
    context_window_hard_min_tokens: int = 16_000
    # Warn when context window is below this
    context_window_warn_below_tokens: int = 32_000

    timezone: str = "UTC"
    docs_path: str = ""

    mysql_url: str = ""
    mysql_username: str = ""
    mysql_password: str = ""
    session_auth_bypass_for_testing: bool = False
    session_auth_test_token: str = ""
    session_auth_test_user_id: str = ""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.sessions_dir = self.data_dir / "sessions"

    def ensure_dirs(self) -> None:
        self.sessions_dir.mkdir(parents=True, exist_ok=True)
        self.workspace_dir.mkdir(parents=True, exist_ok=True)
        self.converted_markdown_dir.mkdir(parents=True, exist_ok=True)

    # iClass API
    iclass_api_base_url: str = ""
    iclass_api_token: str = ""
    iclass_api_routes: dict[str, dict[str, Any]] = {}
    iclass_api_timeout_seconds: int = 30

    # MinIO
    MINIO_ENDPOINT:str = ""
    MINIO_ACCESS_KEY:str = ""
    MINIO_SECRET_KEY:str = ""
    MINIO_BUCKET:str = ""
    MINIO_SECURE:str = ""

    # Redis
    redis_url: str = ""
    redis_port:int = 0
    redis_password:str = ""
    redis_db:int = 0
    file_content_cache_ttl_seconds: int = 300


settings = Settings()

settings.ensure_dirs()

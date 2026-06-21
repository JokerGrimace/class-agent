from pathlib import Path
from typing import Literal, Optional

from pydantic import model_validator
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

    llm_provider: Literal["openai", "ollama", "e2e_task_system"] = "openai"

    # 生产模型凭证由用户级服务动态提供，不保留统一共享 API Key。
    openai_api_key: str = ""
    openai_model: str = ""
    openai_base_url: str = ""

    # 用户级模型凭证只通过 new-api 内部接口解析，Agent 不直接访问其数据库。
    new_api_internal_base_url: str = ""
    new_api_internal_token: str = ""
    new_api_api_key_resolve_timeout_seconds: int = 10
    new_api_api_key_cache_ttl_seconds: int = 300
    new_api_api_key_cache_max_entries: int = 2048

    ollama_base_url: str = ""
    ollama_model: str = ""

    tool_timeout_ms: int = 120000
    max_concurrent_tools: int = 3
    max_turns: int = 99
    strict_plan_max_turns: int = 30
    workflow_run_max_parallel_tasks: int = 5
    workflow_subagent_timeout_seconds: int = 300
    workflow_subagent_max_turns: int = 99

    # 搜索仅使用公开页面引擎；保留 auto 兼容现有部署，移除不再启用的 API provider 配置。
    web_search_provider: Literal["auto", "open_websearch", "disabled"] = "auto"
    # 前两个入口不可用时继续请求百度、搜狗公开搜索页，不依赖额外 API key。
    open_websearch_engines: str = "duckduckgo,bing,baidu,sogou"
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

    timezone: str = "Asia/Shanghai"
    docs_path: str = ""

    mysql_url: str = ""
    mysql_username: str = ""
    mysql_password: str = ""
    mysql_readonly_url: str = ""
    mysql_readonly_username: str = ""
    mysql_readonly_password: str = ""
    # 本机联调必须显式开启并自行配置随机凭证；默认部署不提供测试身份入口。
    session_auth_bypass_for_testing: bool = False
    session_auth_test_token: str = ""
    session_auth_test_user_id: str = ""
    session_auth_test_user_role: str = ""

    # 在启动时拒绝不完整的测试配置，避免开启开关后退化为空凭证或弱口令认证。
    @model_validator(mode="after")
    def validate_local_auth(self):
        if self.session_auth_bypass_for_testing:
            if len(self.session_auth_test_token.strip()) < 32:
                raise ValueError("Local test authentication requires a random token of at least 32 characters")
            if not self.session_auth_test_user_id.strip() or not self.session_auth_test_user_role.strip():
                raise ValueError("Local test authentication requires a user id and role")
        return self

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.sessions_dir = self.data_dir / "sessions"

    def ensure_dirs(self) -> None:
        self.sessions_dir.mkdir(parents=True, exist_ok=True)
        self.workspace_dir.mkdir(parents=True, exist_ok=True)
        self.converted_markdown_dir.mkdir(parents=True, exist_ok=True)

    # iClass API
    iclass_api_base_url: str = ""
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
    # 文件上传解析结果在 Redis 中保留 30 分钟，供后续对话读取，避免缓存长期占用空间。
    file_content_cache_ttl_seconds: int = 1800

    converted_markdown_dir: Path = Path("data/converted_markdown")
    docling_artifacts_path: Optional[Path] = None

settings = Settings()

settings.ensure_dirs()

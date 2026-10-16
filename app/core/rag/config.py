from __future__ import annotations

import os
import math
from pathlib import Path
from typing import Any

import yaml
from dotenv import dotenv_values


# RAG 配置和本地数据都以当前预留模块目录为根，避免写入项目的 data/ 或 app/data/。
RAG_ROOT = Path(__file__).resolve().parent
DEFAULT_CONFIG_PATH = RAG_ROOT / "RAGAS.yaml"

# 本地 RAG 凭据单独保存在模块私有 .env 中，读取时不修改 Agent 主配置或进程环境。
_local_environment = dotenv_values(RAG_ROOT / ".env", encoding="utf-8")


def _resolve_config_path(file_path: str | os.PathLike[str] | None = None) -> Path:
    """解析配置路径，并允许部署环境显式覆盖默认文件。"""
    configured_path = file_path or _environment_value("ICLASS_RAG_CONFIG") or DEFAULT_CONFIG_PATH
    return Path(configured_path).expanduser().resolve()


def load_config(file_path: str | os.PathLike[str] | None = None) -> dict[str, Any]:
    """读取并校验 YAML 根节点，避免错误配置延迟到模型初始化阶段才暴露。"""
    config_path = _resolve_config_path(file_path)
    if not config_path.is_file():
        raise FileNotFoundError(f"RAG config file not found: {config_path}")

    with config_path.open("r", encoding="utf-8") as file:
        loaded_config = yaml.safe_load(file) or {}
    if not isinstance(loaded_config, dict):
        raise ValueError(f"RAG config root must be a mapping: {config_path}")
    return loaded_config


def _environment_value(name: str, fallback: Any = None) -> Any:
    """按进程环境、模块私有配置、YAML 的顺序读取，空值继续回退。"""
    value = os.getenv(name)
    if value not in (None, ""):
        return value
    local_value = _local_environment.get(name)
    return local_value if local_value not in (None, "") else fallback


def _project_path(value: Any, default_relative_path: str) -> str:
    """把相对数据目录固定到 RAG 包根目录，避免依赖服务启动时的工作目录。"""
    configured_path = Path(str(value or default_relative_path)).expanduser()
    if not configured_path.is_absolute():
        configured_path = RAG_ROOT / configured_path
    return str(configured_path.resolve())


# 模块级配置保持旧代码的属性访问方式，同时为缺失字段提供明确默认值。
config = load_config()

mode = config.get("project", {}).get("mode", "rag")

# 联网搜索凭据与模型凭据职责分离；模型凭据由项目用户级 Adapter 提供。
tavily_api_key = _environment_value(
    "TAVILY_API_KEY",
    config.get("api_key", {}).get("tavily_api_key"),
)

llm_model = config.get("llm", {}).get("llm_model", "api_key")
llm_model_name = config.get("llm", {}).get("llm_model_name", "")
temperature = config.get("llm", {}).get("temperature", 0)

prompt_template = config.get("prompt", {}).get("prompt_template", "general")

retriever_config = config.get("retriever", {})
vector_store_type = retriever_config.get("vector_store_type", "elastic")
vector_store_name = retriever_config.get("vector_store_name", "")
subject_vector_store_name = retriever_config.get("subject_vector_store_name")
teacher_vector_store_name = retriever_config.get("teacher_vector_store_name")
retriever_mode = retriever_config.get("retriever_mode", "hybrid_search")
retriever_top_n = int(retriever_config.get("retriever_top_n", 3))
iterative_number = int(retriever_config.get("iterative_number", 3))
embeddings_name = retriever_config.get("embeddings_name", "BAAI/bge-small-zh-v1.5")
embeddings_device = retriever_config.get("embeddings_device", "cpu")
is_online = bool(retriever_config.get("is_online", False))
online_threshold = float(retriever_config.get("online_threshold", 0.5))
max_online_results = int(retriever_config.get("max_online_results", 3))

query_rewrite_config = config.get("query_rewrite", {})
# API 模型缺少对应凭据时自动关闭问题重写，保证基础检索仍可用。
configured_query_rewrite = bool(query_rewrite_config.get("enabled", True))
query_rewrite_enabled = configured_query_rewrite
rewrite_history_min_rounds = int(query_rewrite_config.get("history_min_rounds", 2))
rewrite_history_max_rounds = int(query_rewrite_config.get("history_max_rounds", 4))

rerank_config = config.get("rerank", {})
is_rerank = bool(rerank_config.get("is_rerank", True))
rerank_model = rerank_config.get("rerank_model", "BAAI/bge-reranker-base")
rerank_device = rerank_config.get("rerank_device", "cpu")
threshold_or_top_n = rerank_config.get("threshold_or_top_n", "top_n")
rerank_top_n = int(rerank_config.get("rerank_top_n", 5))
rerank_threshold = float(rerank_config.get("rerank_threshold", 0.55))
content_similarity_threshold = float(
    rerank_config.get("content_similarity_threshold", 0.95)
)

# 管理知识的排序系数是显式配置；个人系数必须更高，学院相关性仍可能胜过低相关个人内容。
managed_personal_weight = float(_environment_value("ICLASS_RAG_PERSONAL_WEIGHT", 1.0))
managed_official_weight = float(_environment_value("ICLASS_RAG_OFFICIAL_WEIGHT", 0.6))
if any(not math.isfinite(weight) for weight in (managed_personal_weight, managed_official_weight)) or not managed_personal_weight > managed_official_weight >= 0:
    raise ValueError("RAG management source weights must satisfy personal > official >= 0 and be finite")

history_config = config.get("history", {})
max_history_length = int(history_config.get("max_history_length", 4))
is_memory = bool(history_config.get("is_memory", True))

pdf_process_config = config.get("pdf_process", {})
mineru_patterns = pdf_process_config.get("mineru_patterns", [])
pdf_extract_model = pdf_process_config.get("pdf_extract_model")
layoutreader_model = pdf_process_config.get("layoutreader_model")
mineru_json_url = pdf_process_config.get("mineru_json_url")
config_file_name = pdf_process_config.get("config_file_name")
minerU_home_dir = _environment_value(
    "ICLASS_RAG_MINERU_HOME",
    pdf_process_config.get("minerU_home_dir"),
)
mineru_config = _environment_value(
    "ICLASS_RAG_MINERU_CONFIG",
    pdf_process_config.get("mineru_config"),
)

html_embedding = config.get("html_process", {}).get(
    "html_embedding",
    embeddings_name,
)

modelscope_cache_dir = _environment_value(
    "ICLASS_RAG_MODELSCOPE_CACHE_DIR",
    config.get("cache", {}).get("modelscope_cache_dir"),
)

# 连接信息支持环境注入，使部署包不需要保存主机凭据。
elastic_config = config.get("elastic_search_config", {})
es_url = _environment_value(
    "ICLASS_RAG_ES_URL",
    elastic_config.get("es_url", ""),
)
es_user = _environment_value("ICLASS_RAG_ES_USER", elastic_config.get("es_user"))
es_password = _environment_value(
    "ICLASS_RAG_ES_PASSWORD",
    elastic_config.get("es_password"),
)

chroma_url = _project_path(
    config.get("chroma_config", {}).get("database_url"),
    "data/chroma",
)
faiss_url = _project_path(
    config.get("faiss_config", {}).get("database_url"),
    "data/faiss",
)

normal_database = config.get("database_config", {}).get(
    "normal_database",
    "ElasticsearchStore",
)

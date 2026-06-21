import hashlib
import json
from dataclasses import dataclass
from typing import Any, Optional

import redis

from app.config import settings


@dataclass(frozen=True)
class CachedFile:
    file_cache_key: str
    file_id: str
    file_name: str
    file_type: str
    html_content: str


class RedisFileContentCache:
    def __init__(self, redis_client: Optional[Any] = None):
        self.redis = redis_client or redis.Redis(
            host=settings.redis_url,
            port=settings.redis_port,
            password=settings.redis_password,
            db=settings.redis_db,
            decode_responses=True,
        )

    @staticmethod
    def build_file_id(file_name: str, html_content: str) -> str:
        digest = hashlib.sha256(f"{file_name}\0{html_content}".encode("utf-8")).hexdigest()
        return digest[:32]

    @staticmethod
    def build_key(file_id: str) -> str:
        return f"iclass:file-content:{file_id}"

    def put(
        self,
        *,
        file_name: str,
        file_type: str,
        html_content: str,
        ttl_seconds: Optional[int] = None,
    ) -> CachedFile:
        file_id = self.build_file_id(file_name, html_content)
        file_cache_key = self.build_key(file_id)
        payload = {
            "file_id": file_id,
            "file_name": file_name,
            "file_type": file_type,
            "html_content": html_content,
        }
        self.redis.set(
            file_cache_key,
            json.dumps(payload, ensure_ascii=False),
            ex=ttl_seconds or settings.file_content_cache_ttl_seconds,
        )
        return CachedFile(
            file_cache_key=file_cache_key,
            file_id=file_id,
            file_name=file_name,
            file_type=file_type,
            html_content=html_content,
        )

    def get(self, file_cache_key: str) -> Optional[CachedFile]:
        raw = self.redis.get(file_cache_key)
        if not raw:
            return None
        payload = json.loads(raw)
        return CachedFile(
            file_cache_key=file_cache_key,
            file_id=str(payload.get("file_id") or ""),
            file_name=str(payload.get("file_name") or ""),
            file_type=str(payload.get("file_type") or ""),
            html_content=str(payload.get("html_content") or ""),
        )


_file_content_cache: Optional[RedisFileContentCache] = None


def get_file_content_cache() -> RedisFileContentCache:
    global _file_content_cache
    if _file_content_cache is None:
        _file_content_cache = RedisFileContentCache()
    return _file_content_cache

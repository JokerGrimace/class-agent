from __future__ import annotations

from urllib.parse import urlsplit

from elasticsearch import Elasticsearch, NotFoundError

from .schemas import KnowledgeChunk, KnowledgeChunkPage, KnowledgeScope


# 纯读取/清理只依赖 ES 客户端，不能因查看片段而初始化解析器或 embedding。
def create_management_es_client():
    from . import config

    url = config.es_url if config.es_url.startswith(("http://", "https://")) else f"http://{config.es_url}"
    options = dict(hosts=[url], verify_certs=False, ssl_show_warn=False, request_timeout=30)
    if bool(config.es_user) != bool(config.es_password):
        raise RuntimeError("RAG Elasticsearch credentials are incomplete")
    if config.es_user:
        options["basic_auth"] = (config.es_user, config.es_password)
    return Elasticsearch(**options)


# 请求不能选择索引，个人/官方目标都来自现有部署配置。
def management_index(scope: KnowledgeScope) -> str:
    from . import config

    index = config.teacher_vector_store_name if scope.binding_type == "PERSONAL" else config.subject_vector_store_name
    if config.vector_store_type != "elastic" or not index:
        raise RuntimeError("RAG management Elasticsearch index is not configured")
    return str(index)


class KnowledgeES:
    def __init__(self, client, index: str):
        self.client = client
        self.index = index

    # 所有操作必须用绑定范围组成精确 term，不能仅凭文件 ID 查询或删除。
    @staticmethod
    def filters(scope: KnowledgeScope, version: int | None = None) -> list[dict]:
        filters = [{"term": {"metadata.id.keyword": scope.file_id}},
                   {"term": {"metadata.subject.keyword": scope.subject_code}}]
        if scope.binding_type == "PERSONAL":
            filters.append({"term": {"metadata.teacher.keyword": scope.teacher_code}})
        else:
            filters.append({"term": {"metadata.official_library_id": scope.library_id}})
            filters.append({"term": {"metadata.department_id": scope.department_id}})
        if version is not None:
            filters.append({"term": {"metadata.task_version": version}})
        return filters

    # 当前解析器的 images.path 是物理路径；没有受控公开地址时直接隐藏，不拼装下载地址。
    @staticmethod
    def public_images(metadata: dict) -> list[dict[str, str]]:
        from .images import images_from_metadata
        images = []
        for image in images_from_metadata(metadata):
            url = image.get("url")
            if not isinstance(url, str):
                continue
            parsed = urlsplit(url)
            if parsed.scheme != "https" or not parsed.netloc or parsed.username or parsed.password:
                continue
            # 只有服务端明确许可的图像来源可公开，未配置时不透出任何任意地址。
            from .config import _environment_value
            allowed = str(_environment_value("ICLASS_RAG_IMAGE_ORIGINS", "")).split(",")
            if f"https://{parsed.netloc}" in allowed:
                images.append({"url": url, "description": str(image["alt"])})
        return images

    def chunks(self, scope: KnowledgeScope, version: int, page: int, page_size: int):
        response = self.client.search(
            index=self.index, query={"bool": {"filter": self.filters(scope, version)}},
            from_=(page - 1) * page_size, size=page_size, track_total_hits=True,
            sort=[{"metadata.chunk_order": "asc"}],
            source_includes=["text", "metadata.chunk_id", "metadata.chunk_order", "metadata.images", "metadata.related_images"],
        )
        # ES 可返回 HTTP 200 的部分结果；超时或分片失败时不能给页面一个完整成功的假象。
        if response.get("timed_out") or response.get("_shards", {}).get("failed", 0):
            raise RuntimeError("RAG chunk search was incomplete")
        items = []
        for hit in response["hits"]["hits"]:
            source = hit["_source"]
            metadata = source["metadata"]
            items.append(KnowledgeChunk(chunk_id=metadata["chunk_id"], chunk_order=metadata["chunk_order"],
                                        text=source["text"], images=self.public_images(metadata)))
        return KnowledgeChunkPage(file_id=scope.file_id, active_version=version, page=page,
                                  page_size=page_size, total=response["hits"]["total"]["value"], items=items)

    # 删除零条成功；超时、分片失败及版本冲突绝不能伪装成空库。
    def remove(self, scope: KnowledgeScope, version: int | None = None, except_version: int | None = None) -> int:
        boolean = {"filter": self.filters(scope, version)}
        if except_version is not None:
            boolean["must_not"] = [{"term": {"metadata.task_version": except_version}}]
        try:
            response = self.client.delete_by_query(index=self.index, query={"bool": boolean},
                                                   wait_for_completion=True, refresh=True)
        except NotFoundError:
            # 未创建的新索引没有可清理片段，不影响建立可信文件的幂等移出。
            return 0
        if response.get("failures") or response.get("timed_out") or response.get("version_conflicts", 0):
            raise RuntimeError("RAG chunk cleanup failed")
        return int(response["deleted"])

    def count(self, scope: KnowledgeScope, version: int) -> int:
        response = self.client.count(index=self.index, query={"bool": {"filter": self.filters(scope, version)}})
        if response.get("_shards", {}).get("failed", 0):
            raise RuntimeError("RAG chunk count was incomplete")
        return int(response["count"])

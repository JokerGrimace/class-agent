import itertools
import copy
import logging
import math  # TODO：用于计算跨消息源内容余弦相似度。
import re
import time
from pathlib import Path

import jieba
import jieba.analyse
from elasticsearch import Elasticsearch, NotFoundError
from langchain_huggingface import HuggingFaceEmbeddings
from .search_policy import (
    build_retrieval_payload,
    should_expose_knowledge_base_results,
    should_skip_local_retrieval,
)
from .rerank import ReRank
from .query_rewrite import rewrite_query_tool  # TODO：接入可复用的问题重写工具。
from .images import images_from_metadata
from .retrieval_scope import managed_access_filter, get_managed_retrieval_grants
from . import config
from langchain_community.tools.tavily_search import TavilySearchResults

logger = logging.getLogger(__name__)

class Retriever:
    def __init__(self,
                 vector_store_type,
                 embeddings_name="BAAI/bge-small-zh-v1.5",
                 embeddings_device='cpu',
                 retriever_mode='hybrid_search', # 检索模式：hybrid_search（混合检索）/iterative_search（迭代检索）
                 is_online=False,
                 online_threshold=0.7,
                 max_online_results=5,
                 vector_store_name=config.vector_store_name,
                 text_field="text",
                 vector_field="vector"):
        """
        初始化检索器
        :param vector_store_type: elastic/faiss/chroma
        :param embeddings_name: embeddings模型名称
        :param embeddings_device: embeddings模型的运行设备
        :param vector_store_name: 向量数据库的名字
        :param text_field: 向量数据库存放文本的field
        :param vector_field: 向量数据库存放向量的field
        """

        self.embeddings = HuggingFaceEmbeddings(
            model_name=embeddings_name,
            model_kwargs={'device': embeddings_device},
            encode_kwargs={"batch_size": 32,
                           'normalize_embeddings': True},
        )
        self.vector_store_type = vector_store_type
        self.vector_store_name = vector_store_name
        # 三个向量数据库的初始化
        if vector_store_type == 'elastic':
            es_host = config.es_url
            if not es_host.startswith(("http://", "https://")):
                es_host = f"http://{es_host}"
            # 允许无认证的开发实例；只在用户名和密码同时存在时发送 Basic Auth。
            elasticsearch_options = {
                "hosts": [es_host],
                "verify_certs": False,
                "ssl_show_warn": False,
            }
            if config.es_user and config.es_password:
                elasticsearch_options["basic_auth"] = (
                    config.es_user,
                    config.es_password,
                )
            elif config.es_user or config.es_password:
                raise RuntimeError(
                    "ICLASS_RAG_ES_USER and ICLASS_RAG_ES_PASSWORD must be configured together"
                )
            self.vector_store = Elasticsearch(
                **elasticsearch_options,
            )
        elif vector_store_type == 'chroma':
            # Chroma 是可选后端，仅在配置选中时导入并打开对应持久化目录。
            import chromadb

            persistent_client = chromadb.PersistentClient(config.chroma_url)
            self.vector_store = persistent_client.get_or_create_collection(vector_store_name)
        elif vector_store_type == 'faiss':
            # FAISS 是可选后端，延迟导入可避免 Elasticsearch 部署被无关依赖阻断。
            from langchain_community.vectorstores import FAISS

            self.vector_store = FAISS.load_local(config.faiss_url, self.embeddings,
                                           index_name=vector_store_name, allow_dangerous_deserialization=True)
        else:
            raise ValueError(f"Unsupported vector_store_type: {vector_store_type}")
        self.is_online = is_online
        self.online_threshold = online_threshold
        self.max_online_results = max_online_results
        self.retriever_mode = retriever_mode
        self.text_field = text_field
        self.vector_field = vector_field
        # TODO：问题重写模块仅属于 RAG 链路，关闭时直接使用原问题检索。
        self.query_rewriter = (
            rewrite_query_tool  # TODO：RAG 保存标准工具实例，后续统一通过 invoke 调用。
            if getattr(config, "query_rewrite_enabled", True)
            else None
        )
        # TODO：以上为 Retriever 新增问题重写器初始化。

    @staticmethod
    def normalize_metadata_filters(metadata_filters):
        if not metadata_filters:
            return {}
        return {
            key: value
            for key, value in metadata_filters.items()
            if value not in (None, "", [])
        }

    def build_elastic_filters(self, doc_ids=None, metadata_filters=None):     #BM25
        filters = []
        if doc_ids:
            filters.append({"terms": {"metadata.id.keyword": doc_ids}})
        for key, value in self.normalize_metadata_filters(metadata_filters).items():
            filters.append({"term": {f"metadata.{key}.keyword": value}})
        return filters

    def build_vector_store_filter(self, doc_ids=None, metadata_filters=None):   #向量
        normalized_filters = self.normalize_metadata_filters(metadata_filters)
        if not doc_ids and not normalized_filters:
            return None

        clauses = []
        if doc_ids:
            clauses.append({"id": {"$in": doc_ids}})
        for key, value in normalized_filters.items():
            clauses.append({key: value})

        if len(clauses) == 1:
            return clauses[0]
        return {"$and": clauses}

    @staticmethod
    def build_subject_metadata_filters(metadata_filters):
        return {
            key: value
            for key, value in Retriever.normalize_metadata_filters(metadata_filters).items()
            if key not in {"course", "teacher"}
        }

    @staticmethod
    def tag_knowledge_base_results(results, knowledge_base_type):
        tagged_results = []
        for item in results or []:
            if isinstance(item, dict):            #如果是字典，保留原有字段并添加knowledgeBaseType
                tagged_results.append({
                    **item,
                    "knowledgeBaseType": knowledge_base_type,
                })
            else:
                tagged_results.append({           #如果是字符串，包装成字典并添加knowledgeBaseType
                    "content": str(item),
                    "knowledgeBaseType": knowledge_base_type,
                })
        return tagged_results

    def subject_search(self, query_text, metadata_filters=None, top_n=5):
        subject_vector_store_name = getattr(config, "subject_vector_store_name", None)
        if not subject_vector_store_name:
            return []
        try:
            return self.hybrid_search(
                query_text,
                [],                               #不限制文档ID，搜索整个subject库
                [subject_vector_store_name],
                metadata_filters=self.build_subject_metadata_filters(metadata_filters),  #对subject库的metadata过滤
                top_n=top_n,
            )
        except NotFoundError:
            logger.warning(
                f"Subject knowledge base index not found, skipping subject search. index={subject_vector_store_name}"
            )
            return []

    def preview_scope(self, database_names=None, doc_ids=None, metadata_filters=None):  #预览查询范围，返回匹配和未匹配的文件列表，帮助用户确认查询范围是否正确
        metadata_filters = self.normalize_metadata_filters(metadata_filters)

        if self.vector_store_type != "elastic":
            return {
                "matchedFiles": [],
                "blockedFiles": [],
            }

        database_names = database_names or []
        elastic_filters = self.build_elastic_filters(doc_ids, metadata_filters)  #构建Elasticsearch过滤条件
        if database_names:
            target_index = ",".join(database_names)
        elif doc_ids:
            target_index = "*"
        else:
            target_index = self.vector_store_name

        all_hits = self.execute_search(
            {
                "query": {"match_all": {}},
                "_source": ["metadata"],
            },
            index_name=target_index,
        )
        matched_hits = self.execute_search(
            {
                "query": {
                    "bool": {
                        "must": [{"match_all": {}}],
                        **({"filter": elastic_filters} if elastic_filters else {}),
                    }
                },
                "_source": ["metadata"],
            },
            index_name=target_index,
        )

        def unique_files(search_results):  #从搜索结果中提取唯一的文件列表，避免重复
            files = {}
            for hit in search_results.body["hits"]["hits"]:
                metadata = dict((hit.get("_source") or {}).get("metadata") or {})
                file_id = metadata.get("id")
                if not file_id or file_id in files:
                    continue
                files[file_id] = {
                    "fileId": file_id,
                    "fileName": metadata.get("fileName"),
                    "subject": metadata.get("subject"),
                    "course": metadata.get("course"),
                    "teacher": metadata.get("teacher"),
                    "databaseName": metadata.get("databaseName"),
                }
            return list(files.values())

        matched_files = unique_files(matched_hits)
        matched_ids = {item["fileId"] for item in matched_files}
        blocked_files = [
            item for item in unique_files(all_hits)
            if item["fileId"] not in matched_ids
        ]
        return {
            "matchedFiles": matched_files,
            "blockedFiles": blocked_files,
        }

    # TODO：保留旧版 original + subject + 联网链路作为历史参考，新链路不再执行该实现。
    """
    # TODO：以上旧版 search 链路已整段注释，仅作历史对照。
    def search(self, query):  #主搜索函数，处理查询逻辑，调用不同的搜索方法，并根据配置决定是否进行rerank和是否暴露知识库结果
        rag_search_result = []
        exposed_rag_search_result = []
        online_results = []
        max_rerank_score = 0.0
        rerank_docs = []
        search_mode = query.get("search_mode")
        if search_mode not in {"local", "web", "hybrid"}:
            explicit_online = query.get("use_online")
            if explicit_online is True:
                search_mode = "hybrid"
            elif explicit_online is False:
                search_mode = "local"
            else:
                search_mode = "hybrid" if self.is_online else "local"
        use_online = search_mode in {"web", "hybrid"}
        has_doc_scope = bool(query.get("doc_ids"))   #判断查询是否有文档ID范围限制
        has_database_scope = bool(query.get("database_names"))  #判断查询是否有知识库范围限制
        metadata_filters = self.normalize_metadata_filters(query.get("metadata_filters")) #判断查询是否有元数据过滤条件
        has_metadata_scope = bool(metadata_filters)    #判断查询是否有元数据范围限制
        skip_local_retrieval = should_skip_local_retrieval(   #根据查询范围和搜索模式判断是否跳过本地检索，直接进行在线搜索
            search_mode=search_mode,
            database_names=query.get("database_names"),
            doc_ids=query.get("doc_ids"),
            metadata_filters=metadata_filters,
        )
        if search_mode != "web" and not skip_local_retrieval:  #如果搜索模式不是web-only，并且不需要跳过本地检索，则进行本地检索
            if self.retriever_mode == 'hybrid_search':
                rag_search_result = self.hybrid_search(
                    query["input"],
                    query["doc_ids"],
                    query.get("database_names", []),
                    metadata_filters=metadata_filters,
                    top_n=config.retriever_top_n
                )
            elif self.retriever_mode == 'iterative_search':
                rag_search_result = self.iterative_search(
                    query["input"],
                    query["doc_ids"],
                    query.get("database_names", []),
                    metadata_filters=metadata_filters,
                    max_iterations=config.iterative_number,
                    top_n=config.retriever_top_n
                )
            subject_search_result = self.subject_search(    #专门针对subject库的搜索，构建更严格的过滤条件以提高准确率
                query["input"],
                metadata_filters=metadata_filters,
                top_n=config.retriever_top_n,
            )
            rag_search_result = (                           #合并subject库搜索结果和原始检索结果
                self.tag_knowledge_base_results(subject_search_result, "subject") +
                self.tag_knowledge_base_results(rag_search_result, "original")
            )
        elif skip_local_retrieval:
            logger.info("Skipping local retrieval because no knowledge base scope was selected.")

        if search_mode != "web" and config.is_rerank:     #如果搜索模式不是web-only，并且配置中启用了rerank，则进行rerank
            if not rag_search_result:
                logger.info("No local retrieval results, skipping rerank.")
            else:
                max_rerank_score = ReRank.max_rerank_score(query["input"], rag_search_result)
                logger.info(f"Max rerank score: {max_rerank_score:.4f}")
                rerank_docs = ReRank.rerank(query["input"], rag_search_result)
                logger.info("Rerank results:")
                for doc, score in rerank_docs:
                    logger.info(f"Document: {doc}, Score: {score:.2f}")
                rag_search_result = [doc for doc, score in rerank_docs]
        else:
            logger.info("Rerank disabled, returning retrieval results directly.")

        exposed_rag_search_result = (               #根据是否启用rerank和是否满足rerank阈值来决定最终暴露给LLM的知识库结果，构建详细的日志记录以帮助调试和理解结果过滤过程
            rag_search_result
            if should_expose_knowledge_base_results(
                results=rag_search_result,
                rerank_enabled=config.is_rerank,
                max_rerank_score=max_rerank_score,
                rerank_threshold=config.rerank_threshold,
            )
            else []
        )
        if rerank_docs and exposed_rag_search_result:    #如果有rerank结果，并且最终暴露的知识库结果不为空，则构建详细的日志记录，记录每个文档的原始分数、归一化分数、是否通过阈值等信息，帮助理解哪些文档被保留或过滤掉了
            exposed_rag_search_result = [
                ({
                    **doc,
                    "content": str(doc.get("content", "")),
                    "score": round(float(score), 4),
                } if isinstance(doc, dict) else {
                    "content": str(doc),
                    "score": round(float(score), 4),
                })
                for doc, score in rerank_docs
                if round(float(score), 4) >= float(config.rerank_threshold)
            ]

        if rag_search_result and not exposed_rag_search_result: #如果原始检索结果不为空，但最终暴露的结果被完全过滤掉了，则记录日志说明因为rerank分数未达到阈值而隐藏了所有本地检索结果，帮助用户理解为什么没有知识库结果被返回
            logger.info(
                "Hiding local retrieval results because rerank score is below threshold. "
                f"local_result_count={len(rag_search_result)}, "
                f"max_rerank_score={max_rerank_score:.4f}, "
                f"threshold={config.rerank_threshold:.4f}"
            )

        if search_mode == "web": #如果搜索模式是web-only，则完全跳过本地检索，直接进行在线搜索，并记录日志说明当前搜索模式是web-only，正在跳过本地检索，帮助用户理解为什么没有知识库结果被返回
            logger.info("Search mode is web-only, skipping local retrieval.")
            online_results = self.online_search(query["input"]) if use_online else []
            return build_retrieval_payload([], online_results)

        if not use_online:
            return build_retrieval_payload(
                exposed_rag_search_result,
                [],
                knowledge_base_score_threshold=config.rerank_threshold if config.is_rerank else None,
            )

        # Scoped knowledge-base queries:
        # Prefer local retrieval for scoped knowledge-base queries.
        # Only use online search as a fallback when local retrieval is empty
        # or hidden by the rerank threshold.
        if has_doc_scope or has_database_scope or has_metadata_scope: #如果查询有明确的范围限制（文档ID、知识库名称或元数据过滤），则优先使用本地检索结果，只有当本地检索结果为空或者被rerank阈值过滤掉了才使用在线搜索作为备选方案，并记录详细的日志说明当前查询是有范围限制的，正在优先使用本地检索结果，并且如果需要fallback到在线搜索，也记录相关信息，帮助用户理解查询处理流程和结果来源
            should_fallback_online = (
                not exposed_rag_search_result or
                (config.is_rerank and max_rerank_score < config.rerank_threshold)
            )
            if should_fallback_online:
                logger.info(
                    f"Falling back to online search for scoped query. local_result_count={len(exposed_rag_search_result)}, "
                    f"max_rerank_score={max_rerank_score:.4f}, threshold={config.rerank_threshold:.4f}"
                )
                online_results = self.online_search(query["input"])
                return build_retrieval_payload(
                    exposed_rag_search_result,
                    online_results,
                    knowledge_base_score_threshold=config.rerank_threshold if config.is_rerank else None,
                )
            return build_retrieval_payload(
                exposed_rag_search_result,
                [],
                knowledge_base_score_threshold=config.rerank_threshold if config.is_rerank else None,
            )

        # Open queries without explicit scope:
        # if local retrieval already hit, answer from local only.
        if exposed_rag_search_result:
            logger.info(
                f"Returning local-only results for open query. "
                f"local_result_count={len(exposed_rag_search_result)}"
            )
            return build_retrieval_payload(
                exposed_rag_search_result,
                [],
                knowledge_base_score_threshold=config.rerank_threshold if config.is_rerank else None,
            )

        online_results = self.online_search(query["input"])
        if online_results:
            logger.info(
                f"Falling back to online search for open query. "
                f"local_result_count={len(exposed_rag_search_result)}, online_result_count={len(online_results)}"
            )
        return build_retrieval_payload(
            exposed_rag_search_result,
            online_results,
            knowledge_base_score_threshold=config.rerank_threshold if config.is_rerank else None,
        )
    """

    @staticmethod
    def _source_priority(knowledge_base_type):
        """TODO：内容高度相似时按 subject、teacher、original、online 的顺序保留来源。"""
        return {
            "subject": 0,
            "teacher": 1,
            "original": 2,
            "online": 3,
        }.get(str(knowledge_base_type or ""), 9)

    @staticmethod
    def _normalize_content(content):
        """TODO：去除文本空白差异，用于快速判断完全重复内容。"""
        return re.sub(r"\s+", "", str(content or "")).lower()

    @staticmethod
    def _query_routes(original_query, rewrite_query):
        """TODO：原问题和重写问题相同时只保留一条检索路由。"""
        routes = [("originalQuery", original_query)]
        if rewrite_query and rewrite_query.strip() != original_query.strip():
            routes.append(("rewriteQuery", rewrite_query.strip()))
        return routes

    def _rewrite_query(self, original_query, history_messages):
        """TODO：仅将原问题和历史传给重写工具，关闭时返回原问题。"""
        query_rewriter = getattr(self, "query_rewriter", None)
        if query_rewriter is None:
            return original_query
        return query_rewriter.invoke({  # TODO：RAG 必须通过标准工具接口调用问题重写模块。
            "original_query": original_query,
            "history_messages": list(history_messages or []),
        })

    @staticmethod
    def _subject_filters(metadata_filters):
        """TODO：subject 库只使用 subject 元数据进行精确过滤。"""
        normalized = Retriever.normalize_metadata_filters(metadata_filters)
        subject = normalized.get("subject")
        return {"subject": subject} if subject else {}

    @staticmethod
    def _teacher_filters(metadata_filters):
        """TODO：teacher 库必须同时具备 subject 和 teacher，两个条件在 ES 中按 AND 过滤。"""
        normalized = Retriever.normalize_metadata_filters(metadata_filters)
        subject = normalized.get("subject")
        teacher = normalized.get("teacher")
        if not subject or not teacher:
            return None
        return {
            "subject": subject,
            "teacher": teacher,
        }

    @staticmethod
    def _normalize_candidate(item, source_type, database_name, matched_query):
        """TODO：将 subject、teacher 和联网结果收敛为同一字典结构。"""
        if isinstance(item, dict):
            candidate = dict(item)
        elif isinstance(item, (list, tuple)):
            candidate = {
                "content": str(item[0] if item else ""),
                "url": str(item[1] if len(item) > 1 else ""),
            }
        else:
            candidate = {"content": str(item)}

        candidate["content"] = str(candidate.get("content") or "")
        candidate["knowledgeBaseType"] = source_type
        candidate["databaseName"] = database_name
        candidate["matchedQueries"] = [matched_query]
        if candidate.get("score") is not None and candidate.get("retrievalScore") is None:
            candidate["retrievalScore"] = candidate.pop("score")
        return candidate

    def _search_library_routes(
        self,
        routes,
        index_name,
        source_type,
        metadata_filters,
        doc_ids=None,
    ):
        """TODO：同一知识库依次使用原问题和重写问题各召回三条。"""
        if not index_name:
            return []
        candidates = []
        for matched_query, query_text in routes:
            try:
                results = self.hybrid_search(
                    query_text,
                    list(doc_ids or []),
                    [index_name],
                    metadata_filters=metadata_filters,
                    top_n=int(config.retriever_top_n),
                )
            except NotFoundError:
                logger.warning("Knowledge base index not found, skipping search. index=%s", index_name)
                return candidates
            for item in list(results or [])[:int(config.retriever_top_n)]:
                candidates.append(
                    self._normalize_candidate(
                        item,
                        source_type,
                        index_name,
                        matched_query,
                    )
                )
        return candidates

    @staticmethod
    def _merge_matched_queries(target, incoming):
        """TODO：同一候选被多条查询命中时保留完整路由来源。"""
        merged = []
        for query_name in list(target.get("matchedQueries") or []) + list(incoming.get("matchedQueries") or []):
            if query_name not in merged:
                merged.append(query_name)
        target["matchedQueries"] = merged

    def _deduplicate_exact(self, candidates):
        """TODO：先按完全相同内容去重，避免重复进入语义比较。"""
        deduplicated = {}
        for candidate in sorted(candidates, key=self._deduplication_priority):
            candidate_key = self._normalize_content(candidate.get("content"))
            if not candidate_key:
                candidate_key = f"{candidate.get('knowledgeBaseType')}:{candidate.get('url')}:{len(deduplicated)}"
            if candidate_key in deduplicated:
                self._merge_matched_queries(deduplicated[candidate_key], candidate)
                continue
            deduplicated[candidate_key] = candidate
        return list(deduplicated.values())

    def _deduplication_priority(self, item):
        # 等价内容按明确的来源系数保留一份，避免旧 subject 优先在相关性排序前丢掉个人来源。
        file_id = item.get("fileId")
        managed = isinstance(file_id, str) and file_id.startswith(("pk_", "ok_"))
        return (-self._managed_source_weight(item) if managed else 0, self._source_priority(item.get("knowledgeBaseType")))

    @staticmethod
    def _cosine_similarity(left_vector, right_vector):
        """TODO：使用纯 Python 计算候选内容向量的余弦相似度。"""
        numerator = sum(float(left) * float(right) for left, right in zip(left_vector, right_vector))
        left_norm = math.sqrt(sum(float(value) * float(value) for value in left_vector))
        right_norm = math.sqrt(sum(float(value) * float(value) for value in right_vector))
        if not left_norm or not right_norm:
            return 0.0
        return numerator / (left_norm * right_norm)

    def _deduplicate_near_similar(self, candidates):
        """内容相似度达到配置阈值时保留最终加权分数更高的消息源。"""
        candidates = self._deduplicate_exact(candidates)
        if len(candidates) < 2 or not hasattr(self, "embeddings"):
            return candidates
        ordered_candidates = sorted(
            candidates,
            # 近似内容仍可能与问题有不同相关性，必须在全量评分后按实际加权结果保留。
            key=lambda item: (-float(item.get("score", 0.0)), self._deduplication_priority(item)),
        )
        try:
            vectors = self.embeddings.embed_documents([
                candidate.get("content", "") for candidate in ordered_candidates
            ])
        except Exception:
            logger.exception("Candidate similarity calculation failed, keeping exact-deduplicated results.")
            return candidates

        threshold = float(getattr(config, "content_similarity_threshold", 0.95))
        kept_candidates = []
        kept_vectors = []
        for candidate, vector in zip(ordered_candidates, vectors):
            duplicate_index = None
            for index, kept_vector in enumerate(kept_vectors):
                if self._cosine_similarity(vector, kept_vector) >= threshold:
                    duplicate_index = index
                    break
            if duplicate_index is None:
                kept_candidates.append(candidate)
                kept_vectors.append(vector)
                continue
            self._merge_matched_queries(kept_candidates[duplicate_index], candidate)
        return kept_candidates

    def _score_candidates(self, query_text, candidates):
        """TODO：每个候选只执行一次 rerank 推理，并将分数回写到统一消息源结构。"""
        if not candidates:
            return []
        if getattr(config, "is_rerank", True):
            scored_pairs = ReRank.score_all(query_text, candidates)
        else:
            scored_pairs = [
                (candidate, float(candidate.get("retrievalScore", 1.0)))
                for candidate in candidates
            ]
        scored_candidates = []
        for candidate, score in scored_pairs:
            normalized = dict(candidate)
            # 先保留未加权相关性用于原有最低阈值，再按来源乘系数排序，避免学院内容仅因0.6被全数阈值淘汰。
            normalized["relevanceScore"] = round(float(score), 4)
            normalized["sourceWeight"] = self._managed_source_weight(normalized)
            normalized["score"] = round(float(score) * normalized["sourceWeight"], 4)
            scored_candidates.append(normalized)
        return sorted(
            scored_candidates,
            key=lambda item: (
                -float(item.get("score", 0.0)),
                self._source_priority(item.get("knowledgeBaseType")),
            ),
        )

    @staticmethod
    def _managed_source_weight(item):
        # 来源由 ES 已授权的准确 fileId 前缀区分；旧共享库及联网资料保持既有分值。
        file_id = item.get("fileId")
        if isinstance(file_id, str) and file_id.startswith("pk_"):
            return config.managed_personal_weight
        if isinstance(file_id, str) and file_id.startswith("ok_"):
            return config.managed_official_weight
        return 1.0

    def _search_online_routes(self, routes):
        """TODO：联网搜索同样使用原问题和重写问题各召回三条。"""
        candidates = []
        for matched_query, query_text in routes:
            try:
                results = self.online_search(query_text)
            except Exception:
                logger.exception("Online search failed for query route=%s", matched_query)
                continue
            for item in list(results or [])[:int(config.max_online_results)]:
                candidates.append(
                    self._normalize_candidate(
                        item,
                        "online",
                        "online",
                        matched_query,
                    )
                )
        return candidates

    def search(self, query):
        """TODO：新 RAG 链路只检索 subject、teacher 和必要的联网结果，最终统一 rerank 前五条。"""
        original_query = str(query.get("input") or "").strip()
        if not original_query:
            return build_retrieval_payload([], [])

        # TODO：元数据只供知识库过滤使用，不再传入通用问题重写工具。
        metadata_filters = self.normalize_metadata_filters(query.get("metadata_filters"))
        rewrite_query = self._rewrite_query(
            original_query,
            query.get("history"),
        )
        query_routes = self._query_routes(original_query, rewrite_query)
        rerank_query = rewrite_query or original_query

        search_mode = query.get("search_mode")
        if search_mode not in {"local", "web", "hybrid"}:
            if query.get("use_online") is True:
                search_mode = "hybrid"
            elif query.get("use_online") is False:
                search_mode = "local"
            else:
                search_mode = "hybrid" if self.is_online else "local"
        use_online = search_mode in {"web", "hybrid"}

        local_candidates = []
        database_names = query.get("database_names")
        doc_ids = list(query.get("doc_ids") or [])
        skip_local_retrieval = should_skip_local_retrieval(
            search_mode=search_mode,
            database_names=database_names,
            doc_ids=doc_ids,
            metadata_filters=metadata_filters,
        )
        if search_mode != "web" and not skip_local_retrieval:
            subject_index = getattr(config, "subject_vector_store_name", None)
            local_candidates.extend(
                self._search_library_routes(
                    query_routes,
                    subject_index,
                    "subject",
                    self._subject_filters(metadata_filters),
                    doc_ids=doc_ids,
                )
            )

            # 本人或所问课程的已发布文件由 Java 返回，即使模型没传 subject/teacher 也能自动检索个人库。
            personal_ids = [grant.file_id for grant in get_managed_retrieval_grants() if grant.binding_type == "PERSONAL"
                            and (not doc_ids or grant.file_id in doc_ids)]
            if personal_ids:
                teacher_index = getattr(config, "teacher_vector_store_name", None)
                local_candidates.extend(
                    self._search_library_routes(
                        query_routes,
                        teacher_index,
                        "teacher",
                        self._subject_filters(metadata_filters),
                        doc_ids=personal_ids,
                    )
                )
            else:
                logger.info("Skipping teacher knowledge base because no authorized published personal files are available.")

            # 适配器传入的业务知识库继续作为 original 来源参与统一去重和排序。
            dedicated_indexes = {subject_index, getattr(config, "teacher_vector_store_name", None)}
            for database_name in list(database_names or []):
                if not database_name or database_name in dedicated_indexes:
                    continue
                local_candidates.extend(
                    self._search_library_routes(
                        query_routes,
                        database_name,
                        "original",
                        metadata_filters,
                        doc_ids=doc_ids,
                    )
                )
        elif skip_local_retrieval:
            logger.info("Skipping local retrieval because no knowledge base scope was selected.")

        # 完全同文可先合并；近似片段先全量评分，来源系数应用后再去重和截取。
        local_candidates = self._deduplicate_exact(local_candidates)
        scored_local_candidates = self._score_candidates(rerank_query, local_candidates)
        rerank_enabled = bool(getattr(config, "is_rerank", True))
        rerank_threshold = float(getattr(config, "rerank_threshold", 0.0))
        exposed_local_candidates = [  # TODO：恢复旧链路规则，仅将达到 rerank 阈值的本地结果视为有效结果。
            candidate
            for candidate in scored_local_candidates
            if not rerank_enabled or float(candidate.get("relevanceScore", 0.0)) >= rerank_threshold
        ]
        # 未通过原始相关性阈值的片段不能在近似合并中淘汰合格的学院/个人内容。
        scored_local_candidates = self._deduplicate_near_similar(exposed_local_candidates)

        should_search_online = use_online and (
            search_mode == "web"
            or not exposed_local_candidates  # TODO：恢复旧链路规则，本地存在有效结果时不再联网补充。
        )
        if should_search_online:
            online_candidates = self._search_online_routes(query_routes)
            if online_candidates:
                combined_candidates = self._deduplicate_exact(local_candidates + online_candidates)
                combined_scores = self._score_candidates(rerank_query, combined_candidates)
                eligible_combined = [candidate for candidate in combined_scores
                    if not rerank_enabled or float(candidate.get("relevanceScore", 0.0)) >= rerank_threshold]
                scored_local_candidates = self._deduplicate_near_similar(eligible_combined)

        final_candidates = [  # TODO：恢复旧链路阈值过滤，并在通过阈值的候选中保留前 5 条。
            candidate
            for candidate in sorted(
                scored_local_candidates,
                key=lambda item: (
                    -float(item.get("score", 0.0)),
                    self._source_priority(item.get("knowledgeBaseType")),
                ),
            )
            if not rerank_enabled or float(candidate.get("relevanceScore", 0.0)) >= rerank_threshold
        ][:int(config.rerank_top_n)]
        for rank, candidate in enumerate(final_candidates, start=1):
            candidate["rank"] = rank

        knowledge_base_results = [
            candidate
            for candidate in final_candidates
            if candidate.get("knowledgeBaseType") in {"subject", "teacher", "original"}
        ]
        online_results = [
            candidate
            for candidate in final_candidates
            if candidate.get("knowledgeBaseType") == "online"
        ]
        payload = build_retrieval_payload(
            knowledge_base_results,
            online_results,
            knowledge_base_score_threshold=None,
        )
        payload["originalQuery"] = original_query
        payload["rewriteQuery"] = rewrite_query
        return payload

    # TODO：以上为新 RAG 链路及其专用的重写、多路检索、去重和消息源处理方法。

    def execute_search(self, query_body, index_name=None):
        # 所有 ES 入口统一附加可信访问范围，覆盖 wildcard、预览、BM25 和 query-KNN；模型参数不能取消此过滤。
        if "knn" in query_body:
            raise ValueError("top-level knn is not supported by the protected retrieval path")
        protected_body = copy.deepcopy(query_body)
        protected_body.setdefault("size", 20)
        protected_body["query"] = {"bool": {
            "must": [protected_body.get("query", {"match_all": {}})],
            "filter": [managed_access_filter()],
        }}
        return self.vector_store.search(index=index_name or self.vector_store_name, body=protected_body)

    def extract_rank_mapping(self, search_results) -> dict:
        """提取文档ID与排名的映射关系"""
        return {hit["_id"]: rank + 1 for rank, hit in enumerate(search_results['hits']['hits'])}

    @staticmethod
    def metadata_result(content, metadata=None):
        metadata = dict(metadata or {})
        result = {"content": str(content or "")}
        # 管理来源只暴露已许可的公开图片 URL，本地解析图片与原件路径不进入模型或返回值。
        file_id = metadata.get("id")
        managed = isinstance(file_id, str) and file_id.startswith(("pk_", "ok_"))
        if managed:
            from .management_es import KnowledgeES
            images = [{"url": image["url"], "alt": image["description"]} for image in KnowledgeES.public_images(metadata)]
        else:
            images = images_from_metadata(metadata)
        if images:
            result["images"] = images
        for metadata_key, result_key in {
            "id": "fileId",
            "fileName": "fileName",
            "filePath": "filePath",
            "databaseName": "databaseName",
            "subject": "subject",
            "course": "course",
            "teacher": "teacher",
            "chunk_id": "chunkId",
            "parent_id": "parentId",
            "department_id": "departmentId",
            "task_version": "activeVersion",
        }.items():
            if managed and metadata_key == "filePath":
                continue
            value = metadata.get(metadata_key)
            if value not in (None, "", []):
                result[result_key] = value
        return result

    @staticmethod
    def elastic_hit_to_result(hit):
        source = hit.get("_source", {})
        return Retriever.metadata_result(
            source.get("text", ""),
            source.get("metadata"),
        )

    def rrf_merge(self, bm25_results, vector_results, query, top_n=10, alpha=0.5):
        """RRF 融合算法实现"""
        # 提取两个检索结果的文档排名
        bm25_ranks = self.extract_rank_mapping(bm25_results)
        vector_ranks = self.extract_rank_mapping(vector_results)

        # 合并所有文档ID
        all_docs = set(itertools.chain(bm25_ranks.keys(), vector_ranks.keys()))

        # 动态调整检索权重
        if len(query.split()) > 3:  # 长查询侧重语义
            alpha = 0.8
        else:  # 短查询侧重关键词
            alpha = 0.4

        # 根据每个文档的排名计算每个文档的 RRF 分数
        rrf_scores = {}
        for doc_id in all_docs:
            score = 0.0
            if doc_id in bm25_ranks:
                score += (1 - alpha) * (1.0 / (60 + bm25_ranks[doc_id]))
            if doc_id in vector_ranks:
                score += alpha * (1.0 / (60 + vector_ranks[doc_id]))
            rrf_scores[doc_id] = score

        # 按分数降序排序
        sorted_docs = sorted(rrf_scores.items(), key=lambda x: x[1], reverse=True)[:top_n]
        result_id = [doc_id for doc_id, _ in sorted_docs]
        if config.mode == 'rag':
            hits_by_id = {
                hit["_id"]: hit
                for hit in itertools.chain(
                    bm25_results.body["hits"]["hits"],
                    vector_results.body["hits"]["hits"],
                )
            }
            result = [
                {**self.elastic_hit_to_result(hits_by_id[doc_id]), "retrievalScore": rrf_scores[doc_id]}
                for doc_id in result_id
                if doc_id in hits_by_id
            ]
        else:
            # 如果是agent方式会按结构化输出
            result = []
            temp_ids = []
            for i in bm25_results.body['hits']['hits']:
                if i['_id'] in result_id:
                    if i['_id'] not in temp_ids:
                        temp_ids.append(i['_id'])
                        result.append({"title": i['_source']['metadata']['fileName'],
                                       "content": i['_source']['metadata']['source'],
                                       "link": i['_source']['metadata']['filePath']})
            # result = list(set(result["link"]))
        logger.info("混合检索的结果为：")
        for i, doc in enumerate(result):
            logger.info(f"结果 {i + 1}: {doc}")

        return result

    def hybrid_search(self, query, doc_ids, database_names=None, metadata_filters=None, top_n=5):
        """混合检索入口函数"""
        if self.vector_store_type == 'elastic':
            database_names = database_names or []
            elastic_filters = self.build_elastic_filters(doc_ids, metadata_filters)  # 构建 Elasticsearch 过滤条件
            if database_names:
                target_index = ",".join(database_names)
            elif doc_ids:
                target_index = '*'
            else:
                target_index = self.vector_store_name
            if not doc_ids:
                # 执行 BM25 检索
                bm25_query = {
                    "size": top_n,  # TODO：BM25 按路由设定的 top_n 召回。
                    "query": {
                        "bool": {
                            "must": [
                                {"match": {self.text_field: query}}  # 原始 BM25 查询
                            ],
                            **({"filter": elastic_filters} if elastic_filters else {})
                        }
                    }
                }
                # 执行向量检索
                vector_query = {
                    "size": top_n,  # TODO：向量检索按路由设定的 top_n 召回。
                    "query": {
                        "bool": {
                            "must": [
                                {
                                    "knn": {
                                        "field": self.vector_field,
                                        "query_vector": self.get_query_vector(query),
                                        "k": top_n,  # TODO：向量返回数量与每路召回数一致。
                                        "num_candidates": max(top_n * 3, top_n)  # TODO：保留 KNN 内部候选池以维持召回质量。
                                    }
                                }
                            ],
                            **({"filter": elastic_filters} if elastic_filters else {})
                        }
                    }
                }
            else:
                bm25_query = {
                    "size": top_n,  # TODO：带文档范围时 BM25 仍使用指定 top_n。
                    "query": {
                        "bool": {
                            "must": [
                                {"match": {self.text_field: query}}  # 原始 BM25 查询
                            ],
                            "filter": elastic_filters
                        }
                    }
                }
                vector_query = {
                    "size": top_n,  # TODO：带文档范围时向量检索仍使用指定 top_n。
                    "query": {
                        "bool": {
                            "must": [
                                {
                                    "knn": {
                                        "field": self.vector_field,
                                        "query_vector": self.get_query_vector(query),
                                        "k": top_n,  # TODO：带范围向量检索返回指定 top_n。
                                        "num_candidates": max(top_n * 3, top_n)  # TODO：带范围 KNN 保留内部候选池。
                                    }
                                }
                            ],
                            "filter": elastic_filters
                        }
                    }
                }
            bm25_results = self.execute_search(bm25_query, index_name=target_index)
            vector_results = self.execute_search(vector_query, index_name=target_index)

            # RRF 融合排序
            return self.rrf_merge(bm25_results, vector_results, query, top_n=top_n)
        elif self.vector_store_type == 'chroma':
            # 执行BM25检索

            # 执行向量检索
            query_embeddings = self.get_query_vector(query)
            vector_filter = self.build_vector_store_filter(doc_ids, metadata_filters)
            if vector_filter is None:
                vector_results = self.vector_store.query(query_embeddings=query_embeddings, n_results=top_n)  # TODO：Chroma 改为使用 top_n。
            else:
                vector_results = self.vector_store.query(query_embeddings=query_embeddings, n_results=top_n, where=vector_filter)  # TODO：带过滤的 Chroma 检索使用 top_n。
            logger.info("混合检索的结果为：")
            for i, doc in enumerate(vector_results['documents'][0]):
                logger.info(f"结果 {i + 1}: {doc}")

            metadatas = (vector_results.get('metadatas') or [[]])[0]
            return [
                self.metadata_result(doc, metadatas[index] if index < len(metadatas) else {})
                for index, doc in enumerate(vector_results['documents'][0])
            ]

        elif self.vector_store_type == 'faiss':
            # 执行BM25检索

            # 执行向量检索
            query_embeddings = self.get_query_vector(query)
            vector_filter = self.build_vector_store_filter(doc_ids, metadata_filters)
            if vector_filter is None:
                vector_results = self.vector_store.similarity_search_by_vector(query_embeddings, k=top_n)  # TODO：FAISS 改为使用 top_n。
            else:
                vector_results = self.vector_store.similarity_search_by_vector(query_embeddings, k=top_n, filter=vector_filter)  # TODO：带过滤的 FAISS 检索使用 top_n。
            results = [
                self.metadata_result(result.page_content, result.metadata)
                for result in vector_results
            ]
            logger.info("混合检索的结果为：")
            for i, doc in enumerate(results):
                logger.info(f"结果 {i + 1}: {doc}")

            return results

    def get_query_vector(self, query):
        """生成查询向量"""
        return self.embeddings.embed_query(query)

    def extract_keywords(self, text, top_k=3, min_word_length=2):
        """
        提取中文关键词（基于 jieba 分词 + TF-IDF）
        - text: 中文文本
        - top_k: 提取关键词数量
        - min_word_length: 最小词长（过滤单字）
        """
        # 使用 jieba 进行中文分词
        words = jieba.lcut(text)

        # 去除停用词和无意义词
        stopwords = self.load_stopwords()
        filtered_words = [word for word in words if word not in stopwords and len(word) >= min_word_length]

        # 使用 TF-IDF 提取关键词
        keywords = jieba.analyse.extract_tags(' '.join(filtered_words), topK=top_k)

        return keywords

    def load_stopwords(self):
        """加载中文停用词表"""
        stopwords = {"的", "是", "在", "了", "和", "就", "有", "也", "于", "他", "她", "它", "这", "那", "与", "及",
                     "但", "而", "则", "之", "以", "为", "可", "能", "会", "对", "从", "到", "得"}
        return stopwords

    def extract_existing_keywords(self, query_text):
        """
        从当前查询中提取已存在的关键词（忽略标点和空格）
        """
        # 使用正则表达式提取词语（忽略标点）
        words = re.findall(r'[\u4e00-\u9fa5a-zA-Z0-9]+', query_text)
        return set(words)

    def generate_next_query(self, current_query, results):
        """
        生成下一轮查询，避免重复添加关键词
        """
        # 提取当前查询中的已有关键词
        existing_keywords = self.extract_existing_keywords(current_query)

        # 提取新文档中的关键词
        new_keywords = []
        for doc in results[:2]:  # 取前两段内容
            doc_keywords = self.extract_keywords(doc, top_k=3)
            new_keywords.extend(doc_keywords)

        # 去除重复和已存在的关键词
        unique_new_keywords = []
        for keyword in new_keywords:
            if (keyword not in existing_keywords) and (keyword not in unique_new_keywords):
                unique_new_keywords.append(keyword)
        # 拼接新查询
        new_query_text = f"{current_query} {' '.join(unique_new_keywords)}"
        return new_query_text

    def iterative_search(self, initial_query, doc_ids, database_names=None, metadata_filters=None, max_iterations=3, top_n=5):
        """
        迭代检索核心逻辑（模拟论文中的多轮检索）
        - 初始查询 -> 检索 -> 生成新查询 -> 再次检索 -> ...
        - 最终合并所有轮次的结果
        """
        all_results = []
        current_query = initial_query

        for i in range(max_iterations):
            logger.info(f"\n=== 第 {i + 1} 轮迭代检索 ===")
            results = self.hybrid_search(
                current_query,
                doc_ids,
                database_names,
                metadata_filters=metadata_filters,
                top_n=top_n
            )

            # 收集唯一文档ID，避免重复
            for doc in results:
                if doc not in all_results:
                    all_results.append(doc)

            # 生成新查询
            if i < max_iterations - 1:
                new_query = self.generate_next_query(current_query, results)
                logger.info(f"生成新查询: {new_query}")
                current_query = new_query

        # 去重并保留 top_n 个最佳文档
        final_result = list(set(all_results))[:top_n]
        logger.info("\n=== 最终迭代检索结果 ===")
        for i, doc in enumerate(final_result):
            logger.info(f"最终结果 {i + 1}: {doc[:20]}...")

        return final_result

    def online_search(self, query):
        # 混合检索未配置 Tavily 时退化为本地检索，避免发送无凭据的外部请求。
        if not config.tavily_api_key:
            logger.warning("TAVILY_API_KEY is not configured; skipping online search.")
            return []

        search = TavilySearchResults(max_results=self.max_online_results, tavily_api_key=config.tavily_api_key)
        raw_results = search.invoke(re.sub(r"[\"']", "", query))

        if isinstance(raw_results, dict):
            results = raw_results.get("results", [])
        elif isinstance(raw_results, list):
            results = raw_results
        else:
            if "Query is invalid" in str(raw_results) or "400 Client Error" in str(raw_results):
                logger.warning(
                    f"Online search rejected the query. Tavily may not support this query format or non-English input. query={query!r}, payload={raw_results}"
                )
            logger.warning(f"Online search returned unexpected payload: {raw_results}")
            return []

        if not all(isinstance(result, dict) for result in results):
            logger.warning(f"Online search returned non-dict results: {raw_results}")
            return []

        for i, result in enumerate(results):
            logger.info(f"Online search result #{i + 1}: {result}")

        last_result = [
            {
                "content": str(result.get("content", "")),
                "title": str(result.get("title", "")),
                "url": str(result.get("url", "")),
                "retrievalScore": round(float(result.get("score", 0.0)), 4),
            }
            for result in results
            if result.get("score", 0) > self.online_threshold
        ]
        # TODO：以上将联网结果从元组改为带标题、URL 和检索分数的字典。
        logger.info(f"\nOnline search final results: {last_result}")

        return last_result

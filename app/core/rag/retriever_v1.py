import itertools
import logging
import re
import time
from pathlib import Path

import chromadb
import jieba
import jieba.analyse
from elasticsearch import Elasticsearch
from langchain_community.vectorstores import FAISS
from langchain_huggingface import HuggingFaceEmbeddings

from .config import embeddings_name, embeddings_device
from .rerank import ReRank
from . import config
from langchain_community.tools.tavily_search import TavilySearchResults

logger = logging.getLogger(__name__)

class Retriever:
    def __init__(self,
                 vector_store_type,
                 embeddings_name="BAAI/bge-small-zh-v1.5",
                 embeddings_device='cpu',
                 retriever_mode='hybrid_search',
                 is_online=False,
                 online_threshold=0.7,
                 max_online_results=5,
                 vector_store_name='langchain',
                 text_field="text",
                 vector_field="vector"):
        """
        初始化检索器
        :param vector_store_type: elastic or faiss
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
        if vector_store_type == 'elastic':
            # 历史检索器同样复用统一配置，避免遗留示例凭据被误用于真实环境。
            es_host = config.es_url
            if not es_host.startswith(("http://", "https://")):
                es_host = f"http://{es_host}"
            self.vector_store = Elasticsearch(
                hosts=[es_host],
                basic_auth=(config.es_user, config.es_password),
                verify_certs=False,
                ssl_show_warn=False
            )
        elif vector_store_type == 'chroma':
            persistent_client = chromadb.PersistentClient(
                str(Path(__file__).resolve().parent / ".." / ".." / ".." / "data_process" / "chroma_data"))
            self.vector_store = persistent_client.get_or_create_collection('langchain')
        elif vector_store_type == 'faiss':
            self.vector_store = FAISS.load_local(str(Path(__file__).resolve().parent / ".." / ".." / ".." / "data_process" / "chroma_data"), self.embeddings,
                                           index_name='chinese', allow_dangerous_deserialization=True)
        else:
            logger.error("你选择的向量数据库不正确，请重新选择")
        self.is_online = is_online
        self.online_threshold = online_threshold
        self.max_online_results = max_online_results
        self.retriever_mode = retriever_mode
        self.vector_store_name = vector_store_name
        self.text_field = text_field
        self.vector_field = vector_field

    @staticmethod
    def normalize_metadata_filters(metadata_filters):
        if not metadata_filters:
            return {}
        return {
            key: value
            for key, value in metadata_filters.items()
            if value not in (None, "", [])
        }

    def build_elastic_filters(self, doc_ids=None, metadata_filters=None):
        filters = []
        if doc_ids:
            filters.append({"terms": {"metadata.id.keyword": doc_ids}})
        for key, value in self.normalize_metadata_filters(metadata_filters).items():
            filters.append({"term": {f"metadata.{key}.keyword": value}})
        return filters

    def build_vector_store_filter(self, doc_ids=None, metadata_filters=None):
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

    def search(self, query):
        rag_search_result = []
        max_rerank_score = 0.0
        has_doc_scope = bool(query.get("doc_ids"))
        has_database_scope = bool(query.get("database_names"))
        metadata_filters = self.normalize_metadata_filters(query.get("metadata_filters"))
        has_metadata_scope = bool(metadata_filters)
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

        if config.is_rerank:
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

        if not self.is_online:
            return rag_search_result

        # Scoped knowledge-base queries: prefer local retrieval and only use online as fallback.
        if has_doc_scope or has_database_scope or has_metadata_scope:
            should_fallback_online = (
                not rag_search_result or
                (config.is_rerank and max_rerank_score < config.rerank_threshold)
            )
            if should_fallback_online:
                logger.info(
                    f"Falling back to online search for scoped query. local_result_count={len(rag_search_result)}, "
                    f"max_rerank_score={max_rerank_score:.4f}, threshold={config.rerank_threshold:.4f}"
                )
                return self.online_search(query["input"])
            return rag_search_result

        # Open queries without doc scope: keep the previous internet-first feel.
        online_results = self.online_search(query["input"])
        if online_results:
            logger.info(
                f"Merging local and online results for open query. "
                f"local_result_count={len(rag_search_result)}, online_result_count={len(online_results)}"
            )
            return rag_search_result + online_results
        return rag_search_result

    def execute_search(self, query_body, index_name=None):
        """?? Elasticsearch ??"""
        query_body['size'] = 20
        return self.vector_store.search(index=index_name or self.vector_store_name, body=query_body)

    def extract_rank_mapping(self, search_results) -> dict:
        """提取文档ID与排名的映射关系"""
        return {hit["_id"]: rank + 1 for rank, hit in enumerate(search_results['hits']['hits'])}

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

        # 计算每个文档的 RRF 分数
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
        result = [i['_source']['text'] for i in bm25_results.body['hits']['hits'] if i['_id'] in result_id]

        logger.info("混合检索的结果为：")
        for i, doc in enumerate(result):
            logger.info(f"结果 {i + 1}: {doc}")

        return result

    def hybrid_search(self, query, doc_ids, database_names=None, metadata_filters=None, top_n=5):
        """混合检索入口函数"""
        if self.vector_store_type == 'elastic':
            database_names = database_names or []
            elastic_filters = self.build_elastic_filters(doc_ids, metadata_filters)
            if database_names:
                target_index = ",".join(database_names)
            elif doc_ids:
                target_index = '*'
            else:
                target_index = self.vector_store_name

            if not doc_ids:
                bm25_query = {
                    "query": {
                        "bool": {
                            "must": [
                                {"match": {self.text_field: query}}
                            ],
                            **({"filter": elastic_filters} if elastic_filters else {})
                        }
                    }
                }
                vector_query = {
                    "query": {
                        "bool": {
                            "must": [
                                {
                                    "knn": {
                                        "field": self.vector_field,
                                        "query_vector": self.get_query_vector(query),
                                        "k": 20,
                                        "num_candidates": 30
                                    }
                                }
                            ],
                            **({"filter": elastic_filters} if elastic_filters else {})
                        }
                    }
                }
            else:
                bm25_query = {
                    "query": {
                        "bool": {
                            "must": [
                                {"match": {self.text_field: query}}
                            ],
                            "filter": elastic_filters
                        }
                    }
                }
                vector_query = {
                    "query": {
                        "bool": {
                            "must": [
                                {
                                    "knn": {
                                        "field": self.vector_field,
                                        "query_vector": self.get_query_vector(query),
                                        "k": 20,
                                        "num_candidates": 30
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
                vector_results = self.vector_store.query(query_embeddings=query_embeddings, n_results=5)
            else:
                vector_results = self.vector_store.query(query_embeddings=query_embeddings, n_results=5, where=vector_filter)
            logger.info("混合检索的结果为：")
            for i, doc in enumerate(vector_results['documents'][0]):
                logger.info(f"结果 {i + 1}: {doc}")

            return vector_results['documents'][0]

        elif self.vector_store_type == 'faiss':
            # 执行BM25检索

            # 执行向量检索
            query_embeddings = self.get_query_vector(query)
            vector_filter = self.build_vector_store_filter(doc_ids, metadata_filters)
            if vector_filter is None:
                vector_results = self.vector_store.similarity_search_by_vector(query_embeddings, k=5)
            else:
                vector_results = self.vector_store.similarity_search_by_vector(query_embeddings, k=5, filter=vector_filter)
            results = [result.page_content for result in vector_results]
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
                time.sleep(0.5)  # 避免频繁请求

        # 去重并保留 top_n 个最佳文档
        final_result = list(set(all_results))[:top_n]
        logger.info("\n=== 最终迭代检索结果 ===")
        for i, doc in enumerate(final_result):
            logger.info(f"最终结果 {i + 1}: {doc[:20]}...")

        return final_result

    def online_search(self, query):
        search = TavilySearchResults(max_results=self.max_online_results)
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
            (result.get("content", ""), result.get("url", ""))
            for result in results
            if result.get("score", 0) > self.online_threshold
        ]
        logger.info(f"\nOnline search final results: {last_result}")

        return last_result

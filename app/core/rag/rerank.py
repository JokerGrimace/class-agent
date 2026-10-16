import os
import logging
import warnings

os.environ.setdefault("USE_TF", "0")
os.environ.setdefault("USE_TORCH", "1")

import torch
from sentence_transformers import CrossEncoder

from . import config

logger = logging.getLogger(__name__)

warnings.filterwarnings("ignore", message="1Torch was not compiled with flash attention.")


def remove_duplicates(documents, key="link"):
    seen = set()
    unique_docs = []
    for doc in documents:
        val = doc.get(key)
        if val not in seen:
            seen.add(val)
            unique_docs.append(doc)
    return unique_docs


class ReRank:
    model = None

    @classmethod
    def get_model(cls):
        if cls.model is None:
            cls.model = CrossEncoder(
                config.rerank_model,
                device=config.rerank_device,
                max_length=512,
                trust_remote_code=True,
            ).eval()
        return cls.model

    @staticmethod
    def document_content(document):
        if isinstance(document, dict):
            return str(document.get("content", ""))
        return str(document)

    @staticmethod
    def _build_pairs(query: str, documents: list):
        if config.mode == "rag":
            candidate_documents = documents
            pairs = [[query, ReRank.document_content(doc)] for doc in candidate_documents]
        else:
            candidate_documents = remove_duplicates(documents, key="link")
            pairs = [[query, doc["content"]] for doc in candidate_documents]
        return candidate_documents, pairs

    @staticmethod
    def _normalize_scores(scores):
        scores_tensor = torch.tensor(scores, dtype=torch.float32)
        return torch.sigmoid(scores_tensor).tolist()

    @staticmethod
    def build_score_breakdown(documents, raw_scores, normalized_scores, rerank_threshold):
        details = []
        for doc, raw_score, normalized_score in zip(documents, raw_scores, normalized_scores):
            details.append({
                "document": doc,
                "raw_score": round(float(raw_score), 4),
                "normalized_score": round(float(normalized_score), 4),
                "passed_threshold": float(normalized_score) > float(rerank_threshold),
            })
        return details

    @staticmethod
    def score_all(query: str, documents: list, rerank_threshold=config.rerank_threshold) -> list:
        # 来源加权与最终条数限制在 Retriever 中完成，这里必须返回每个候选的真实相关性。
        if not documents:
            return []

        logger.info("Starting rerank.")
        logger.info(f"Using rerank model: {config.rerank_model}")

        candidate_documents, pairs = ReRank._build_pairs(query, documents)
        with torch.no_grad():
            scores = ReRank.get_model().predict(pairs)

        normalized_scores = ReRank._normalize_scores(scores)
        score_breakdown = ReRank.build_score_breakdown(
            candidate_documents,
            scores,
            normalized_scores,
            rerank_threshold,
        )
        logger.info(f"Rerank threshold: {rerank_threshold}")
        for item in score_breakdown:
            logger.info(
                "Rerank candidate | raw_score=%s | normalized_score=%s | passed=%s | document=%s",
                item["raw_score"],
                item["normalized_score"],
                item["passed_threshold"],
                item["document"],
            )
        sorted_results = sorted(
            zip(candidate_documents, normalized_scores),
            key=lambda item: item[1],
            reverse=True,
        )

        return list(sorted_results)

    @staticmethod
    def rerank(query: str, documents: list, rerank_threshold=config.rerank_threshold) -> list:
        # 原有公共 rerank 调用保留阈值/前N返回契约，新检索流程显式使用全量评分接口。
        sorted_results = ReRank.score_all(query, documents, rerank_threshold)
        if config.threshold_or_top_n == "threshold":
            return [(doc, score) for doc, score in sorted_results if score > rerank_threshold]
        return list(sorted_results[:config.rerank_top_n])

    @staticmethod
    def max_rerank_score(query: str, documents: list) -> float:
        if not documents:
            return 0.0

        _, pairs = ReRank._build_pairs(query, documents)
        with torch.no_grad():
            scores = ReRank.get_model().predict(pairs)

        normalized_scores = ReRank._normalize_scores(scores)
        logger.info(
            "Max rerank score computed from normalized scores: %s",
            [round(float(score), 4) for score in normalized_scores],
        )
        return max(normalized_scores) if normalized_scores else 0.0

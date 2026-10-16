from __future__ import annotations

from urllib.parse import urlsplit  # TODO：用于从联网来源 URL 生成消息源标签。

from .images import append_images_to_content


def normalize_search_mode(search_mode, use_online, default_online_enabled=False):
    if search_mode == "web":
        search_mode = "hybrid"

    if search_mode in {"local", "hybrid"}:
        return search_mode

    if use_online is True:
        return "hybrid"
    if use_online is False:
        return "local"
    return "hybrid" if default_online_enabled else "local"


def normalize_database_names(database_names):
    if database_names is None:
        return None
    return database_names


def should_skip_local_retrieval(search_mode, database_names, doc_ids, metadata_filters):
    if search_mode == "web":
        return True

    has_doc_scope = bool(doc_ids)
    has_metadata_scope = bool(metadata_filters)
    has_explicit_empty_database_selection = database_names == []

    return has_explicit_empty_database_selection and not has_doc_scope and not has_metadata_scope


def build_empty_context_message(search_mode, database_names, doc_ids, metadata_filters):
    has_doc_scope = bool(doc_ids)
    has_metadata_scope = bool(metadata_filters)
    has_explicit_empty_database_selection = database_names == []

    if search_mode == "local":
        if has_explicit_empty_database_selection and not has_doc_scope and not has_metadata_scope:
            return "当前没有选择知识库，所以这次没有命中知识库内容。"
        return "这次没有命中知识库内容，请换个问法，或补充更明确的关键词。"

    if search_mode == "hybrid":
        return "这次没有命中知识库内容，也没有拿到可用的联网结果，请换个问法再试一次。"

    return "这次没有拿到可用内容，请稍后再试。"


def normalize_knowledge_base_result(item):
    if isinstance(item, dict):
        content = str(item.get("content", ""))
        normalized = dict(item)
        normalized["content"] = content
        source_parts = []
        knowledge_base_type = normalized.get("knowledgeBaseType")
        if knowledge_base_type == "subject":
            source_parts.append("subject库")
        elif knowledge_base_type == "teacher":  # TODO：新增 teacher 库消息源标签。
            source_parts.append("teacher库")
        elif knowledge_base_type == "original":
            source_parts.append("原有库")
        for key in ("fileName", "subject", "course", "teacher"):
            value = normalized.get(key)
            if value not in (None, "", []):
                source_parts.append(str(value))
        if not source_parts:
            fallback_path = normalized.get("filePath")
            fallback_id = normalized.get("fileId")
            if fallback_path not in (None, "", []):
                source_parts.append(str(fallback_path).replace("\\", "/").split("/")[-1])
            elif fallback_id not in (None, "", []):
                source_parts.append(str(fallback_id))
        if source_parts:
            normalized["sourceLabel"] = " | ".join(source_parts)
        if "score" in item and item["score"] is not None:
            normalized["score"] = item["score"]
        return normalized
    return str(item)


def normalize_online_result(item):
    """TODO：联网结果改为与知识库结果一致的字典消息源格式。"""
    if isinstance(item, dict):
        normalized = dict(item)
    elif isinstance(item, (list, tuple)):
        normalized = {
            "content": str(item[0] if item else ""),
            "url": str(item[1] if len(item) > 1 else ""),
        }
    else:
        normalized = {"content": str(item)}

    normalized["content"] = str(normalized.get("content") or "")
    normalized["title"] = str(normalized.get("title") or "")
    normalized["url"] = str(normalized.get("url") or "")
    normalized["knowledgeBaseType"] = "online"
    normalized.setdefault("databaseName", "online")
    source_parts = ["联网搜索"]
    if normalized["title"]:
        source_parts.append(normalized["title"])
    elif normalized["url"]:
        source_parts.append(urlsplit(normalized["url"]).netloc or normalized["url"])
    normalized["sourceLabel"] = " | ".join(source_parts)
    return normalized
    # TODO：以上将联网结果收敛为与知识库一致的字典结构。


def filter_knowledge_base_results_by_score(knowledge_base_results=None, knowledge_base_score_threshold=None):
    filtered_results = []
    for item in list(knowledge_base_results or []):
        normalized = normalize_knowledge_base_result(item)
        if isinstance(normalized, dict):
          # 加权分数用于排名，原最低相关性阈值继续核验未加权分数，学院片段不会仅因来源系数被剔除。
          score = normalized.get("relevanceScore", normalized.get("score"))
          if score is None:
              continue
          if knowledge_base_score_threshold is not None and float(score) < float(knowledge_base_score_threshold):
              continue
        filtered_results.append(normalized)
    return filtered_results


def build_combined_context(knowledge_base_results=None, online_results=None):
    combined = []
    knowledge_base_items = [
        normalize_knowledge_base_result(item)
        for item in list(knowledge_base_results or [])
    ]
    online_items = [
        normalize_online_result(item)
        for item in list(online_results or [])
    ]
    knowledge_base_types = {
        item.get("knowledgeBaseType")
        for item in knowledge_base_items
        if isinstance(item, dict)
    }
    # 管理知识按相关性乘来源系数排序，不再通过旧提示把学院资料设为绝对优先。
    has_managed = any(isinstance(item, dict) and isinstance(item.get("fileId"), str)
                      and item["fileId"].startswith(("pk_", "ok_")) for item in knowledge_base_items)
    if has_managed:
        combined.append("知识片段已按相关性与来源权重排序，请依据内容回答并保留来源。")
    elif "subject" in knowledge_base_types and "teacher" in knowledge_base_types:
        combined.append(
            "资料优先级规则：当 [subject库] 与 [teacher库] 内容高度相似时，优先采用 [subject库]；"
            "[teacher库] 用于补充教师维度的内容。"
        )
    elif "subject" in knowledge_base_types and "original" in knowledge_base_types:
        combined.append(
            "资料优先级规则：当 [subject库] 与 [原有库] 内容不一致时，必须直接采用 [subject库] 的结论作为最终答案；"
            "[原有库] 只能用于说明存在冲突，不能推翻 [subject库]。不要回答“无法确定”、不要建议再咨询老师或教务。"
        )

    ranked_items = knowledge_base_items + online_items

    def result_rank(item):
        if not isinstance(item, dict) or item.get("rank") is None:
            return float("inf")
        try:
            return float(item["rank"])
        except (TypeError, ValueError):
            return float("inf")

    ranked_items = sorted(
        enumerate(ranked_items),
        key=lambda indexed_item: (
            result_rank(indexed_item[1]),
            indexed_item[0],
        ),
    )
    for _, item in ranked_items:
        if isinstance(item, dict):
            content = append_images_to_content(item.get("content", ""), item)
            knowledge_base_type = item.get("knowledgeBaseType")
            if knowledge_base_type == "subject":
                combined.append(f"[subject库]\n{content}")
            elif knowledge_base_type == "teacher":
                combined.append(f"[teacher库]\n{content}")
            elif knowledge_base_type == "original":
                combined.append(f"[原有库]\n{content}")
            elif knowledge_base_type == "online":
                online_parts = ["[联网搜索]"]
                if item.get("title"):
                    online_parts.append(f"标题：{item['title']}")
                if item.get("url"):
                    online_parts.append(f"网址：{item['url']}")
                online_parts.append(f"内容：{content}")
                combined.append("\n".join(online_parts))
            else:
                combined.append(content)
        else:
            combined.append(item)
    return combined
    # TODO：以上新增 subject、teacher、online 标记并按最终 rank 恢复上下文顺序。


def build_retrieval_payload(knowledge_base_results=None, online_results=None, knowledge_base_score_threshold=None):
    normalized_knowledge_base_results = filter_knowledge_base_results_by_score(
        knowledge_base_results=knowledge_base_results,
        knowledge_base_score_threshold=knowledge_base_score_threshold,
    )
    normalized_online_results = [
        normalize_online_result(item)
        for item in list(online_results or [])
    ]
    # TODO：以上确保 onlineResults 也返回统一字典消息源。
    return {
        "knowledgeBaseResults": normalized_knowledge_base_results,
        "onlineResults": normalized_online_results,
        "combinedContext": build_combined_context(
            normalized_knowledge_base_results,
            normalized_online_results,
        ),
    }


def should_expose_knowledge_base_results(results, rerank_enabled, max_rerank_score, rerank_threshold):
    results = list(results or [])
    if not results:
        return False

    if not rerank_enabled:
        return True

    return max_rerank_score > rerank_threshold


def detect_answer_source(search_mode, context, knowledge_base_results=None, online_results=None):
    context = list(context or [])
    if not context:
        return "empty"

    has_online = bool(list(online_results or [])) or any(
        (
            isinstance(item, dict)
            and item.get("knowledgeBaseType") == "online"
        )
        or (isinstance(item, (list, tuple)) and len(item) >= 2 and item[1])
        or (isinstance(item, str) and item.startswith("[联网搜索]"))
        for item in context
    )
    has_knowledge_base = bool(list(knowledge_base_results or [])) or any(
        (
            isinstance(item, dict)
            and item.get("knowledgeBaseType") in {"subject", "teacher", "original"}
        )
        or (
            isinstance(item, str)
            and not item.startswith("[联网搜索]")
        )
        for item in context
    )

    if has_knowledge_base and has_online:
        return "hybrid"
    if has_online:
        return "online_no_kb"
    return "knowledge_base"
    # TODO：以上兼容新 online 字典和上下文标记的答案来源判断。

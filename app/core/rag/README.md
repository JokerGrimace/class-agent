# RAG tool package

`app.core.rag` owns the complete RAG business path:

- `file_process/`: document loading, PDF-to-Markdown conversion, Markdown/HTML processing, and chunking;
- `data_process/`: Elasticsearch, Chroma, and FAISS ingestion, replacement, and deletion;
- `upload.py`: framework-independent upload persistence;
- `retriever.py`, `rerank.py`, and related policy modules: retrieval and ranking;
- `tool.py`: the public facade for ingestion and retrieval.

The HTTP API is an adapter only. Scripts and workers can use the package directly:

```python
from app.core.rag.tool import RAGTool

rag = RAGTool(retriever=my_retriever, upload_dir="data/uploaded_files")
result = rag.import_upload(
    upload,
    database_name="subject_knowledge",
    vector_store_type="elastic",
    metadata={"subject": "computer"},
)
answer_context = rag.search({"input": "课程要求是什么？", "doc_ids": []})
```

Markdown/PDF 中的图片会以 JSON 字符串写入文本切片的
`metadata["related_images"]`，检索结果同时返回 `content` 和 `images`。
如果图片使用相对路径，上传 metadata 可传入对模型/客户端可访问的
`imageBaseUrl`；否则保留 Markdown 里的原始路径。

The module now lives in the project's reserved `app/core/rag/` directory. The
adapter loads `app.core.rag.config` and `app.core.rag.retriever` directly, so it
no longer depends on the old `app/RAG/src` layout.

## Elasticsearch 连接配置

将本目录 `.env.example` 复制为 `.env`，由部署人员填写
`ICLASS_RAG_ES_URL`、`ICLASS_RAG_ES_USER` 和 `ICLASS_RAG_ES_PASSWORD`。
配置按进程环境、本目录私有 `.env`、`RAGAS.yaml` 的顺序读取。
开源副本不包含测试服务器 SSH、账号、端口或密码。

检索与摄取依赖分别列于 `requirements-retrieval.txt` 和
`requirements-ingestion.txt`。

## 知识广场管理与可信检索

知识广场由 Java 校验真实教师、备课学科、资源和订阅后调用四个
`/v1/rag/manage/` 管理接口。`ICLASS_RAG_SERVICE_TOKEN` 必须与 Java 一致，
至少 32 字符；`ICLASS_RAG_REPORT_URL` 指向 Java 的
`/iclass/internal/rag/task-report`。这些变量保存在私有配置，不能使用登录密码。

私有技术任务存储在 `data/management/tasks.sqlite3`，原件按版本保存在
RAG 私有上传目录；服务重启复用这些目录以恢复任务和回报。新版写入完成后
等待 Java 回执的实际 `activeVersion`，确认前保留旧成功版可读。

正常 Agent 工具检索使用当前用户 Bearer 向 Java 的
`/resource/knowledge/retrieval-scope` 取得文件与成功版本。教师使用本人全部
备课学科和已订阅学院，学生使用会话课程对应的权限。直接调用未设置可信
范围的检索不会读取两个管理索引。来源系数为
`ICLASS_RAG_PERSONAL_WEIGHT=1.0`、`ICLASS_RAG_OFFICIAL_WEIGHT=0.6`；
最低相关性筛选和加权排序完成后再进行近似去重及截取。

PDF 使用 `mineru[pipeline]` 及对应模型。正常解析读取进程环境变量
`MINERU_TOOLS_CONFIG_JSON`（模型配置 JSON 的绝对路径）和
`MINERU_MODEL_SOURCE`（本机验证使用 `modelscope`）。只配置
`ICLASS_RAG_MINERU_CONFIG` 不会自动设置正常解析所读取的 MinerU 环境变量。
完整字段与跨服务发布约定见
[`knowledge-square-rag-contract.md`](../../../docs/superpowers/plans/2026-10-10-knowledge-square-rag-contract.md)。

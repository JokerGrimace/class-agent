from __future__ import annotations

from contextlib import asynccontextmanager
import logging
from pathlib import Path
import threading
import uuid

import httpx

from .management_es import KnowledgeES, create_management_es_client, management_index
from .schemas import KnowledgeReportReceipt, KnowledgeTask
from .task_store import TaskConflict, TaskStore, file_operation_lock
from .upload import persist_uploaded_file


logger = logging.getLogger(__name__)
SERVICE_HEADER = "X-IClass-Rag-Service-Token"


# 服务凭据从私有配置读取，缺失或长度不足时不能退回普通用户 token。
def management_service_token():
    from .config import _environment_value
    return str(_environment_value("ICLASS_RAG_SERVICE_TOKEN", ""))


# 回报目的地固定在部署配置，任务正文没有 URL 覆盖入口，HTTP 成功也必须核对 Java 业务 code。
def report_task_result(payload):
    from .config import _environment_value
    token = management_service_token()
    if len(token) < 32:
        return None
    url = str(_environment_value("ICLASS_RAG_REPORT_URL", "http://127.0.0.1:7500/iclass/internal/rag/task-report"))
    response = httpx.post(url, json=payload, headers={SERVICE_HEADER: token}, timeout=10, follow_redirects=False)
    if response.status_code != 200:
        return None
    data = response.json()
    if data.get("code") != "200":
        return None
    receipt = KnowledgeReportReceipt.model_validate(data["body"])
    if receipt.file_id != payload["body"]["fileId"]:
        return None
    return receipt


# 管理摄取按独立版本目录保存原件，逻辑文件 ID 与物理上传 ID 分离。
def prepare_managed_file(task, upload_id):
    from .management_ingestion import prepare_version
    return prepare_version(task, upload_id)


def default_repository(scope):
    from .management_ingestion import ManagedKnowledgeES
    return ManagedKnowledgeES(create_management_es_client(), management_index(scope))


class KnowledgeManager:
    def __init__(self, store, *, repository_factory=default_repository, prepare=prepare_managed_file,
                 reporter=report_task_result):
        self.store = store
        self.repository_factory = repository_factory
        self.prepare = prepare
        self.reporter = reporter
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._thread = None
        self._start_lock = threading.Lock()

    def submit(self, task: KnowledgeTask, upload=None):
        # 同版本重试先由持久任务去重，不再次覆盖正在使用的原件。
        def persist():
            upload_id = f"managed_{uuid.uuid4().hex}"
            persist_uploaded_file(upload, upload_id)
            return upload_id

        result = self.store.submit(task, persist if upload is not None else None)
        self.start()
        self._wake.set()
        return result

    # worker 开机扫描 SQLite；重启后的 PROCESSING 任务持有原件，可在 OS 锁释放后恢复。
    def start(self):
        with self._start_lock:
            if self._thread is None or not self._thread.is_alive():
                self._stop.clear()
                self._thread = threading.Thread(target=self._run, name="rag-management", daemon=True)
                self._thread.start()

    def stop(self):
        self._stop.set()
        self._wake.set()
        if self._thread is not None:
            self._thread.join(timeout=2)

    def _run(self):
        while not self._stop.is_set():
            try:
                self.process_once()
                self.flush_reports()
            except Exception:
                # 技术任务循环不能因一次数据库/网络故障永久退出，也不记录可能带密钥的异常正文。
                logger.error("RAG management worker cycle failed")
            self._wake.wait(2)
            self._wake.clear()

    def process_once(self):
        for file_id in self.store.pending_files():
            # 多进程共享同一私有数据库与锁目录，忙碌的文件留给持锁 worker 处理。
            with file_operation_lock(self.store.path.parent / "locks", file_id) as acquired:
                if not acquired:
                    continue
                current = self.store.current_task(file_id)
                if current is None or current[1]["state"] not in {"QUEUED", "PROCESSING", "REMOVING"}:
                    continue
                self._execute(*current)
        self.cleanup_once()

    def _execute(self, task, record):
        repository = None
        try:
            repository = self.repository_factory(task.scope())
            if task.task_action == "REMOVE":
                # 移出与状态发布使用技术版本 CAS；空目标成功，ES 故障保存可重试失败状态。
                with self.store.connection(immediate=True) as db:
                    if self.store.is_current(task, db):
                        repository.remove(task.scope())
                        self.store.transition(task, "REMOVED", chunk_count=0, active_version=None, db=db)
                return
            self.store.transition(task, "PROCESSING")
            prepared = self.prepare(task, record["upload_id"])
            if not self.store.is_current(task):
                return
            # 只清本次未成功版本的残留，解析失败始终保留旧成功版本。
            self.store.schedule_cleanup(task, task.task_version)
            repository.remove(task.scope(), version=task.task_version)
            count = repository.write(task, prepared)
            if repository.count(task.scope(), task.task_version) != count:
                raise RuntimeError("written chunk count does not match")
            with self.store.connection(immediate=True) as db:
                if not self.store.is_current(task, db):
                    repository.remove(task.scope(), version=task.task_version)
                    return
                # ES 完整写入后保留所有旧成功版，收到 Java 实际发布版本回执后才清旧。
                self.store.transition(task, "READY", chunk_count=count, active_version=task.task_version, db=db)
        except Exception as exc:
            # 原始异常可能包含路径、连接凭据与模型配置，持久状态只保存可公开的固定消息。
            logger.warning("RAG managed task failed: file_id=%s version=%s exception=%s",
                           task.file_id, task.task_version, type(exc).__name__)
            if task.task_action == "INGEST" and repository is not None:
                self.store.schedule_cleanup(task, task.task_version)
                try:
                    repository.remove(task.scope(), version=task.task_version)
                except Exception:
                    logger.error("RAG failed-version cleanup requires retry: file_id=%s version=%s",
                                 task.file_id, task.task_version)
            self.store.transition(task, "FAILED" if task.task_action == "INGEST" else "REMOVE_FAILED",
                                  error_message="知识文件解析入库失败，请重试" if task.task_action == "INGEST"
                                  else "知识片段清理失败，请重试")
        finally:
            if repository is not None and hasattr(repository, "close"):
                repository.close()

    # 失败/旧版清理恢复时仍获取同文件 OS 锁与 SQLite CAS，只删除精确版本，保留当前成功版。
    def cleanup_once(self):
        for item in self.store.pending_cleanup():
            file_id, version = item["file_id"], item["version"]
            with file_operation_lock(self.store.path.parent / "locks", file_id) as acquired:
                if not acquired:
                    continue
                repository = None
                try:
                    current = self.store.current_task(file_id)
                    if current is None:
                        continue
                    repository = self.repository_factory(current[0].scope())
                    with self.store.connection(immediate=True) as db:
                        if not self.store.cleanup_allowed(file_id, version, db):
                            continue
                        repository.remove(current[0].scope(), version=version)
                        self.store.cleanup_complete(file_id, version, db)
                except Exception:
                    self.store.cleanup_failed(file_id, version)
                    logger.warning("RAG version cleanup deferred: file_id=%s version=%s", file_id, version)
                finally:
                    if repository is not None and hasattr(repository, "close"):
                        repository.close()

    # outbox 直到 Java 返回成功才移除；超时、业务拒绝、连接失败均按持久时间重试。
    def flush_reports(self):
        for report in self.store.pending_reports():
            import json
            try:
                response = self.reporter(json.loads(report["payload"]))
                receipt = response if isinstance(response, KnowledgeReportReceipt) else KnowledgeReportReceipt.model_validate(response)
                self.store.report_delivered(report["id"], receipt)
            except Exception:
                self.store.report_failed(report["id"])

    def chunks(self, scope, active_version, page, page_size):
        # 读取短事务冻结成功版本，重解析的长时间解析阶段仍可查看旧成功版。
        # 发布、移出与清理也使用该技术库事务，因此一个分页查询不会与版本删除交错。
        with self.store.connection(immediate=True) as db:
            self.store.require_active(scope, active_version, db)
            client = create_management_es_client()
            try:
                return KnowledgeES(client, management_index(scope)).chunks(scope, active_version, page, page_size)
            finally:
                client.close()


_manager = None
_manager_lock = threading.Lock()


def get_knowledge_manager():
    global _manager
    with _manager_lock:
        if _manager is None:
            # 固定 RAG 私有持久目录，不能根据 HTTP 请求指定技术数据库路径。
            path = Path(__file__).resolve().parent / "data" / "management" / "tasks.sqlite3"
            _manager = KnowledgeManager(TaskStore(path))
        return _manager


@asynccontextmanager
async def rag_management_lifespan(app):
    manager = None
    if len(management_service_token()) >= 32:
        manager = get_knowledge_manager()
        manager.start()
    yield
    if manager is not None:
        manager.stop()

from __future__ import annotations

from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import time

from .schemas import KnowledgeScope, KnowledgeTask, KnowledgeTaskResult


class TaskConflict(ValueError):
    pass


class TaskNotFound(ValueError):
    pass


# 文件级 OS 锁同时约束多 worker 和重启后的执行者，进程退出时由操作系统释放。
@contextmanager
def file_operation_lock(directory: Path, file_id: str):
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / (hashlib.sha256(file_id.encode("utf-8")).hexdigest() + ".lock")
    stream = path.open("a+b")
    if path.stat().st_size == 0:
        stream.write(b"0")
        stream.flush()
    acquired = False
    try:
        stream.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            acquired = True
        except (OSError, BlockingIOError):
            pass
        yield acquired
    finally:
        if acquired:
            stream.seek(0)
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
        stream.close()


# SQLite 仅保存 RAG 私有技术任务和待回报事件，不写 Java 业务表。
class TaskStore:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connection() as db:
            db.executescript("""
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS bindings (
                    file_id TEXT PRIMARY KEY, binding_key TEXT NOT NULL UNIQUE,
                    scope TEXT NOT NULL, latest_version INTEGER NOT NULL,
                    active_version INTEGER, chunk_count INTEGER NOT NULL DEFAULT 0
                );
                CREATE TABLE IF NOT EXISTS tasks (
                    file_id TEXT NOT NULL, version INTEGER NOT NULL, action TEXT NOT NULL,
                    state TEXT NOT NULL, upload_id TEXT, error TEXT,
                    PRIMARY KEY(file_id, version)
                );
                CREATE TABLE IF NOT EXISTS outbox (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, payload TEXT NOT NULL,
                    file_id TEXT NOT NULL, version INTEGER NOT NULL, state TEXT NOT NULL,
                    due REAL NOT NULL DEFAULT 0, attempts INTEGER NOT NULL DEFAULT 0,
                    UNIQUE(file_id, version, state)
                );
                CREATE TABLE IF NOT EXISTS cleanup (
                    file_id TEXT NOT NULL, version INTEGER NOT NULL, due REAL NOT NULL DEFAULT 0,
                    PRIMARY KEY(file_id, version)
                );
                CREATE TABLE IF NOT EXISTS publication_versions (
                    file_id TEXT NOT NULL, version INTEGER NOT NULL,
                    PRIMARY KEY(file_id, version)
                );
                -- 已有私有技术库升级时保留已成功版本，不猜测 Java 是否已经确认。
                INSERT OR IGNORE INTO publication_versions(file_id,version)
                    SELECT file_id,active_version FROM bindings WHERE active_version IS NOT NULL;
            """)

    @contextmanager
    def connection(self, immediate=False):
        db = sqlite3.connect(self.path, timeout=60)
        db.row_factory = sqlite3.Row
        try:
            if immediate:
                db.execute("BEGIN IMMEDIATE")
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    # 范围采用明确字段的规范 JSON，仅为精确相等比较，不做名称或大小写兼容猜测。
    @staticmethod
    def _scope_json(scope):
        return json.dumps(scope.model_dump(), ensure_ascii=False, sort_keys=True)

    def _binding(self, db, scope):
        binding = db.execute("SELECT * FROM bindings WHERE file_id=?", (scope.file_id,)).fetchone()
        if binding is None:
            raise TaskNotFound("managed file binding not found")
        if binding["scope"] != self._scope_json(scope):
            raise TaskConflict("managed file scope does not match")
        return binding

    # 接收与去重是同一技术库事务，新版可立即撤销正在解析的旧版。
    def submit(self, task: KnowledgeTask, persist=None):
        scope = task.scope()
        with self.connection(immediate=True) as db:
            binding = db.execute("SELECT * FROM bindings WHERE file_id=?", (task.file_id,)).fetchone()
            if binding is not None:
                self._binding(db, scope)
                if task.task_version < binding["latest_version"]:
                    raise TaskConflict("task_version is outdated")
                if task.task_version == binding["latest_version"]:
                    record = db.execute("SELECT action FROM tasks WHERE file_id=? AND version=?",
                                        (task.file_id, task.task_version)).fetchone()
                    if record["action"] != task.task_action:
                        raise TaskConflict("task_action conflicts with existing version")
                    return self._result(db, scope, task.task_version)
            else:
                try:
                    db.execute("INSERT INTO bindings(file_id,binding_key,scope,latest_version) VALUES(?,?,?,?)",
                               (task.file_id, f"{task.binding_type}:{task.binding_id}",
                                self._scope_json(scope), task.task_version))
                except sqlite3.IntegrityError as exc:
                    raise TaskConflict("binding already belongs to another managed file") from exc
            upload_id = persist() if persist is not None else None
            if task.task_action == "INGEST" and not upload_id:
                raise ValueError("ingest requires persisted source file")
            state = "QUEUED" if task.task_action == "INGEST" else "REMOVING"
            db.execute("INSERT INTO tasks(file_id,version,action,state,upload_id) VALUES(?,?,?,?,?)",
                       (task.file_id, task.task_version, task.task_action, state, upload_id))
            db.execute("UPDATE bindings SET latest_version=? WHERE file_id=?", (task.task_version, task.file_id))
            if task.task_action == "REMOVE":
                # 移出撤销后不允许迟到发布回执重新激活旧片段；已登记版本仍由移出 worker 整体清理。
                db.execute("DELETE FROM cleanup WHERE file_id=?", (task.file_id,))
            return self._result(db, scope, task.task_version)

    def _result(self, db, scope, version):
        binding = self._binding(db, scope)
        record = db.execute("SELECT * FROM tasks WHERE file_id=? AND version=?", (scope.file_id, version)).fetchone()
        if record is None:
            raise TaskNotFound("managed task not found")
        return KnowledgeTaskResult(file_id=scope.file_id, task_version=version,
                                   task_action=record["action"], task_state=record["state"],
                                   active_version=binding["active_version"], chunk_count=binding["chunk_count"],
                                   error_message=record["error"])

    def result(self, scope, version):
        with self.connection() as db:
            return self._result(db, scope, version)

    def require_active(self, scope, version, db=None):
        if db is None:
            with self.connection() as db:
                return self.require_active(scope, version, db)
        binding = self._binding(db, scope)
        latest = db.execute("SELECT action FROM tasks WHERE file_id=? AND version=?",
                            (scope.file_id, binding["latest_version"])).fetchone()
        readable = db.execute("SELECT 1 FROM publication_versions WHERE file_id=? AND version=?",
                              (scope.file_id, version)).fetchone()
        if latest["action"] == "REMOVE" or readable is None:
            raise TaskConflict("active_version no longer matches")
        return version

    def is_current(self, task, db=None):
        if db is None:
            with self.connection() as db:
                return self.is_current(task, db)
        return self._binding(db, task.scope())["latest_version"] == task.task_version

    # 取得文件级 OS 锁后读取最新版；旧解析结束时不能发布另一任务的结果。
    def current_task(self, file_id):
        with self.connection() as db:
            binding = db.execute("SELECT * FROM bindings WHERE file_id=?", (file_id,)).fetchone()
            if binding is None:
                return None
            record = db.execute("SELECT * FROM tasks WHERE file_id=? AND version=?",
                                (file_id, binding["latest_version"])).fetchone()
            task = KnowledgeTask(**json.loads(binding["scope"]), task_version=record["version"], task_action=record["action"])
            return task, dict(record)

    def pending_files(self):
        with self.connection() as db:
            return [row["file_id"] for row in db.execute(
                "SELECT b.file_id FROM bindings b JOIN tasks t ON t.file_id=b.file_id AND t.version=b.latest_version "
                "WHERE t.state IN ('QUEUED','PROCESSING','REMOVING') ORDER BY b.file_id")]

    # 最终发布在 SQLite 版本 CAS 事务内运行，任务接收不能插入当前检查与状态更新之间。
    def transition(self, task, state, *, chunk_count=None, active_version=None, error_message=None, db=None):
        if db is None:
            with self.connection(immediate=True) as db:
                return self.transition(task, state, chunk_count=chunk_count, active_version=active_version,
                                       error_message=error_message, db=db)
        if not self.is_current(task, db):
            return False
        db.execute("UPDATE tasks SET state=?,error=? WHERE file_id=? AND version=?",
                   (state, error_message, task.file_id, task.task_version))
        if state in {"READY", "REMOVED"}:
            db.execute("UPDATE bindings SET active_version=?,chunk_count=? WHERE file_id=?",
                       (active_version, chunk_count or 0, task.file_id))
            # 成功版不可成为补偿删除目标，移出成功后清空该文件所有旧清理记录。
            if state == "READY":
                db.execute("DELETE FROM cleanup WHERE file_id=? AND version=?", (task.file_id, active_version))
                # READY 仅表示 ES 已写完整；Java 确认前旧版、新版都保留，实际授权仍由 Java 控制。
                db.execute("INSERT OR IGNORE INTO publication_versions(file_id,version) VALUES(?,?)",
                           (task.file_id, active_version))
            else:
                db.execute("DELETE FROM cleanup WHERE file_id=?", (task.file_id,))
                db.execute("DELETE FROM publication_versions WHERE file_id=?", (task.file_id,))
        result = self._result(db, task.scope(), task.task_version)
        # 回报持久化与结果变更在同一事务提交，网络失败不会丢掉状态事件。
        payload = {"body": {"bindingType": task.binding_type, "bindingId": task.binding_id,
                   "fileId": task.file_id, "taskVersion": task.task_version, "taskAction": task.task_action,
                   "taskState": state, "chunkCount": result.chunk_count, "activeVersion": result.active_version,
                   "errorMessage": result.error_message}}
        db.execute("INSERT OR IGNORE INTO outbox(payload,file_id,version,state) VALUES(?,?,?,?)",
                   (json.dumps(payload, ensure_ascii=False), task.file_id, task.task_version, state))
        return True

    def pending_reports(self):
        with self.connection() as db:
            return [dict(row) for row in db.execute("SELECT * FROM outbox WHERE due<=? ORDER BY id LIMIT 50", (time.time(),))]

    def report_delivered(self, report_id, receipt):
        # 回执确认和可读版本收束同一事务，进程中断不能先丢回执再遗失旧版清理计划。
        with self.connection(immediate=True) as db:
            report = db.execute("SELECT * FROM outbox WHERE id=?", (report_id,)).fetchone()
            if report is None:
                return
            if receipt.file_id != report["file_id"]:
                raise TaskConflict("Java receipt belongs to another file")
            confirmed = receipt.active_version
            if confirmed is not None:
                known = db.execute("SELECT 1 FROM publication_versions WHERE file_id=? AND version=?",
                                   (receipt.file_id, confirmed)).fetchone()
                if known is not None:
                    # 只收束比 Java 实际发布版本更旧的成功版，保留还在等待确认的更高技术版本。
                    db.execute("INSERT OR IGNORE INTO cleanup(file_id,version) "
                               "SELECT file_id,version FROM publication_versions WHERE file_id=? AND version<?",
                               (receipt.file_id, confirmed))
                    db.execute("DELETE FROM publication_versions WHERE file_id=? AND version<?",
                               (receipt.file_id, confirmed))
            db.execute("DELETE FROM outbox WHERE id=?", (report_id,))

    def report_failed(self, report_id):
        with self.connection() as db:
            db.execute("UPDATE outbox SET attempts=attempts+1,due=? WHERE id=?", (time.time() + 30, report_id))

    # 补偿记录只保存确切版本；不能用清理旧版的任务删除后来发布的成功版。
    def schedule_cleanup(self, task, version, db=None):
        if db is None:
            with self.connection(immediate=True) as db:
                self.schedule_cleanup(task, version, db)
                return
        db.execute("INSERT OR IGNORE INTO cleanup(file_id,version) VALUES(?,?)", (task.file_id, version))

    def older_versions(self, task, db):
        return [row["version"] for row in db.execute("SELECT version FROM tasks WHERE file_id=? AND version<?",
                                                    (task.file_id, task.task_version))]

    def pending_cleanup(self):
        with self.connection() as db:
            return [dict(row) for row in db.execute("SELECT * FROM cleanup WHERE due<=? ORDER BY file_id,version LIMIT 50",
                                                   (time.time(),))]

    def cleanup_allowed(self, file_id, version, db):
        binding = db.execute("SELECT * FROM bindings WHERE file_id=?", (file_id,)).fetchone()
        record = db.execute("SELECT state FROM tasks WHERE file_id=? AND version=?",
                            (file_id, binding["latest_version"])).fetchone()
        protected = db.execute("SELECT 1 FROM publication_versions WHERE file_id=? AND version=?",
                               (file_id, version)).fetchone()
        return protected is None and binding["active_version"] != version and (
            binding["latest_version"] != version or record["state"] in {"FAILED", "REMOVED", "REMOVE_FAILED"})

    def cleanup_complete(self, file_id, version, db=None):
        if db is None:
            with self.connection() as db:
                self.cleanup_complete(file_id, version, db)
                return
        db.execute("DELETE FROM cleanup WHERE file_id=? AND version=?", (file_id, version))

    def cleanup_failed(self, file_id, version):
        with self.connection() as db:
            db.execute("UPDATE cleanup SET due=? WHERE file_id=? AND version=?", (time.time() + 30, file_id, version))

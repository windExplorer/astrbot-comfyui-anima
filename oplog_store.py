"""独立的业务操作日志（操作流水 / oplog）。

与 AstrBot 的 logging 完全解耦：不依赖任何 logger 传播链，关键业务事件直接
写入独立的 SQLite（oplog.db），保证「不遗漏」——例如用户生图、图库去重、限额
扣减/重置、配置保存、图库删除/收藏等系统级操作，都能在日志页溯源。

设计要点：
- 每条记录含结构化字段（事件类型、用户、会话、摘要、关联内容 hash 等），
  便于前端筛选与对账（如「限额计数 2 但图库仅 1」时能定位到某次去重）。
- 写入独立连接 + 全程 try/except，日志失败绝不影响生图/主流程。
- 单线程事件循环使用，足够本插件场景；写入在调用线程内同步完成。
"""

import json
import logging
import sqlite3
import time
from pathlib import Path

logger = logging.getLogger("astrbot_plugin_comfyui_anima.oplog")

# 事件类型（常量，保证可筛选、语义清晰）
EV_DRAW_SUCCESS = "draw_success"        # 生图成功（出图）
EV_DRAW_FAIL = "draw_fail"              # 生图失败/超时/无图
EV_GALLERY_DEDUP = "gallery_dedup"      # 图库去重命中（不新增行，仅计数+1）
EV_GALLERY_NEW = "gallery_new"          # 图库新增记录
EV_QUOTA_INC = "quota_inc"              # 限额扣减（每次成功出图 +1）
EV_QUOTA_RESET = "quota_reset"          # 限额重置（单用户/全部）
EV_CONFIG_SAVE = "config_save"          # 配置保存
EV_GALLERY_DELETE = "gallery_delete"    # 图库删除（移入回收站）
EV_GALLERY_RESTORE = "gallery_restore"  # 图库恢复
EV_GALLERY_PURGE = "gallery_purge"      # 图库彻底删除
EV_GALLERY_STAR = "gallery_star"        # 图库收藏/取消收藏
EV_GALLERY_TAGS = "gallery_tags"        # 图库打标签


def _now() -> float:
    return time.time()


class OpLogStore:
    """独立业务操作日志存储。线程不安全，但单线程事件循环够用。"""

    def __init__(self, data_dir: Path) -> None:
        self.data_dir = Path(data_dir)
        self.db_path = self.data_dir / "oplog.db"
        self._conn = None
        self._init_db()

    def _conn_get(self):
        if self._conn is None:
            self._conn = sqlite3.connect(str(self.db_path))
            self._conn.row_factory = sqlite3.Row
            # 开启 WAL：降低写入锁等待与 fsync 开销，读写并发更流畅
            try:
                self._conn.execute("PRAGMA journal_mode=WAL")
                self._conn.execute("PRAGMA synchronous=NORMAL")
            except Exception as e:  # pragma: no cover
                logger.warning(f"[oplog] 开启 WAL 失败（不影响使用）: {e}")
        return self._conn

    def _init_db(self) -> None:
        try:
            conn = self._conn_get()
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS oplog (
                    id         INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts         REAL NOT NULL,
                    event      TEXT NOT NULL,
                    user_id    TEXT DEFAULT '',
                    user_name  TEXT DEFAULT '',
                    session_id TEXT DEFAULT '',
                    summary    TEXT DEFAULT '',
                    detail     TEXT DEFAULT '',
                    ref_sha    TEXT DEFAULT '',
                    extra      TEXT DEFAULT '{}'
                )
                """
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_oplog_ts ON oplog(ts DESC)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_oplog_event ON oplog(event)")
            conn.commit()
        except Exception as e:  # pragma: no cover
            logger.warning(f"[oplog] 初始化失败: {e}")

    def add(
        self,
        event: str,
        summary: str = "",
        *,
        user_id: str = "",
        user_name: str = "",
        session_id: str = "",
        detail: str = "",
        ref_sha: str = "",
        extra: dict | None = None,
    ) -> None:
        """写入一条业务日志。全程 try/except，失败不抛错、不影响主流程。"""
        try:
            extra_json = "{}"
            if extra:
                try:
                    extra_json = json.dumps(extra, ensure_ascii=False)
                except Exception:
                    extra_json = "{}"
            self._conn_get().execute(
                "INSERT INTO oplog (ts, event, user_id, user_name, session_id, summary, detail, ref_sha, extra) "
                "VALUES (?,?,?,?,?,?,?,?,?)",
                (_now(), event, user_id or "", user_name or "", session_id or "",
                 summary or "", detail or "", ref_sha or "", extra_json),
            )
            self._conn_get().commit()
        except Exception as e:  # pragma: no cover
            logger.warning(f"[oplog] 写入失败: {e}")

    def query(
        self,
        *,
        event: str = "",
        keyword: str = "",
        user: str = "",
        limit: int = 200,
        offset: int = 0,
    ) -> list[dict]:
        """分页查询，返回按时间倒序的记录列表。"""
        where = []
        params: list = []
        if event:
            where.append("event=?")
            params.append(event)
        if user:
            where.append("(user_id LIKE ? OR user_name LIKE ?)")
            params += [f"%{user}%", f"%{user}%"]
        if keyword:
            where.append("(summary LIKE ? OR detail LIKE ? OR extra LIKE ?)")
            params += [f"%{keyword}%", f"%{keyword}%", f"%{keyword}%"]
        sql = "SELECT * FROM oplog"
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY id DESC LIMIT ? OFFSET ?"
        params += [int(limit), int(offset)]
        rows = self._conn_get().execute(sql, tuple(params)).fetchall()
        return [self._row_to_dict(r) for r in rows]

    def count(self, *, event: str = "", keyword: str = "", user: str = "") -> int:
        where = []
        params: list = []
        if event:
            where.append("event=?")
            params.append(event)
        if user:
            where.append("(user_id LIKE ? OR user_name LIKE ?)")
            params += [f"%{user}%", f"%{user}%"]
        if keyword:
            where.append("(summary LIKE ? OR detail LIKE ? OR extra LIKE ?)")
            params += [f"%{keyword}%", f"%{keyword}%", f"%{keyword}%"]
        sql = "SELECT COUNT(*) AS c FROM oplog"
        if where:
            sql += " WHERE " + " AND ".join(where)
        row = self._conn_get().execute(sql, tuple(params)).fetchone()
        return int(row["c"] or 0) if row else 0

    @staticmethod
    def _row_to_dict(r) -> dict:
        extra = {}
        try:
            if r["extra"]:
                extra = json.loads(r["extra"])
        except Exception:
            extra = {}
        return {
            "id": r["id"],
            "ts": r["ts"],
            "event": r["event"],
            "user_id": r["user_id"],
            "user_name": r["user_name"],
            "session_id": r["session_id"],
            "summary": r["summary"],
            "detail": r["detail"],
            "ref_sha": r["ref_sha"],
            "extra": extra,
        }

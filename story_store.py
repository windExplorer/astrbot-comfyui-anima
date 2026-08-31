"""剧情模式会话档案存储（SQLite）。

被动记录模式：用户私聊进入「剧情模式」后，插件把这段对话（用户/助手轮次）、
期间生成的图片（按图库 sha256 关联）、以及退出时 LLM 生成的摘要，归档成一条
「剧情档案」。本模块负责落库与检索，单文件、WAL、内容简单可靠。

表设计（字段尽可能详尽，方便 WebUI 管理页展示）：
- story_sessions  一条剧情档案（人/时间/摘要/场景/角色/标签/评分/备注…）
- story_turns     每一轮对话（role / content / image_sha）
- story_images    剧情期间生成的图片（sha / prompt / 尺寸 / 工作流）
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path

try:
    import sqlite3  # noqa: F401
    _HAS_SQLITE = True
except Exception:  # pragma: no cover
    _HAS_SQLITE = False

logger = None
try:
    from astrbot.api import logger as _log
    logger = _log
except Exception:
    import logging
    logger = logging.getLogger("astrbot_plugin_comfyui_anima.story_store")


def _now() -> str:
    """ISO 本地时间字符串（不带时区，便于展示与排序）。"""
    return time.strftime("%Y-%m-%d %H:%M:%S")


class StoryStore:
    """剧情会话档案存储。线程不安全但本插件为单线程事件循环，足够。"""

    def __init__(self, data_dir: Path, cfg: dict | None = None,
                 cfg_provider=None) -> None:
        self.data_dir = Path(data_dir)
        self.db_path = self.data_dir / "story.db"
        self.cfg = cfg or {}
        self._cfg_provider = cfg_provider if callable(cfg_provider) else None
        self._conn = None
        self._init_db()

    # ------------------------------------------------------------------ #
    # 连接 / 建表
    # ------------------------------------------------------------------ #
    def _conn_get(self):
        if self._conn is None:
            self._conn = sqlite3.connect(str(self.db_path))
            self._conn.row_factory = sqlite3.Row
            try:
                self._conn.execute("PRAGMA journal_mode=WAL")
                self._conn.execute("PRAGMA synchronous=NORMAL")
            except Exception as e:  # pragma: no cover
                logger.warning(f"[剧情] 开启 WAL 失败（不影响使用）: {e}")
        return self._conn

    def _init_db(self) -> None:
        if not _HAS_SQLITE:
            logger.warning("[剧情] 环境无 sqlite3，剧情档案功能不可用")
            return
        conn = self._conn_get()
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS story_sessions (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                session_key TEXT,
                user_id     TEXT,
                user_name   TEXT,
                platform    TEXT,
                title       TEXT,
                summary     TEXT,
                status      TEXT DEFAULT 'active',
                mood        TEXT,
                scene       TEXT,
                characters  TEXT,
                tags        TEXT,
                rating      INTEGER DEFAULT 0,
                notes       TEXT,
                source      TEXT,
                turn_count  INTEGER DEFAULT 0,
                image_count INTEGER DEFAULT 0,
                message_count INTEGER DEFAULT 0,
                started_at  TEXT,
                ended_at    TEXT,
                created_at  TEXT,
                updated_at  TEXT,
                extra       TEXT
            );
            CREATE TABLE IF NOT EXISTS story_turns (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id  INTEGER NOT NULL,
                seq         INTEGER DEFAULT 0,
                role        TEXT,
                content     TEXT,
                image_sha   TEXT,
                created_at  TEXT,
                extra       TEXT
            );
            CREATE TABLE IF NOT EXISTS story_images (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id  INTEGER NOT NULL,
                turn_id     INTEGER,
                sha         TEXT,
                prompt      TEXT,
                prompt_raw  TEXT,
                width       INTEGER,
                height      INTEGER,
                workflow    TEXT,
                seed        TEXT,
                created_at  TEXT,
                extra       TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_story_turns_sid ON story_turns(session_id);
            CREATE INDEX IF NOT EXISTS idx_story_images_sid ON story_images(session_id);
            CREATE INDEX IF NOT EXISTS idx_story_sessions_user ON story_sessions(user_id);
            CREATE INDEX IF NOT EXISTS idx_story_sessions_started ON story_sessions(started_at);
            """
        )
        conn.commit()

    # ------------------------------------------------------------------ #
    # 内部工具
    # ------------------------------------------------------------------ #
    @staticmethod
    def _row_to_dict(row) -> dict:
        if row is None:
            return {}
        d = dict(row)
        if "extra" in d and d["extra"]:
            try:
                d["extra"] = json.loads(d["extra"])
            except Exception:
                pass
        return d

    def _cfg_val(self, key, default=None):
        if self._cfg_provider is not None:
            try:
                c = self._cfg_provider() or {}
                if key in c:
                    return c[key]
            except Exception:
                pass
        return self.cfg.get(key, default)

    # ------------------------------------------------------------------ #
    # 会话生命周期
    # ------------------------------------------------------------------ #
    def create_session(self, session_key: str, user_id: str, user_name: str = "",
                       platform: str = "", source: str = "", status: str = "active",
                       **fields) -> int:
        """创建一条剧情档案，返回 session_id。"""
        now = _now()
        conn = self._conn_get()
        cur = conn.execute(
            """INSERT INTO story_sessions
               (session_key, user_id, user_name, platform, title, summary, status,
                mood, scene, characters, tags, rating, notes, source,
                turn_count, image_count, message_count, started_at, ended_at,
                created_at, updated_at, extra)
               VALUES (?,?,?,?, ?,?,?, ?,?,?,?,?,?,?, 0,0,0, ?,?, ?,?,?)""",
            (
                session_key, user_id, user_name, platform,
                fields.get("title", ""), fields.get("summary", ""), status,
                fields.get("mood", ""), fields.get("scene", ""),
                fields.get("characters", ""), fields.get("tags", ""),
                fields.get("rating", 0) or 0, fields.get("notes", ""),
                source or "trigger",
                now, None, now, now,
                json.dumps(fields.get("extra") or {}, ensure_ascii=False),
            ),
        )
        conn.commit()
        return cur.lastrowid

    def append_turn(self, session_id: int, role: str, content: str,
                    image_sha: str = None) -> int:
        """追加一轮对话；同时刷新会话的 turn_count / message_count / updated_at。"""
        conn = self._conn_get()
        seq = conn.execute(
            "SELECT COALESCE(MAX(seq),0)+1 FROM story_turns WHERE session_id=?",
            (session_id,),
        ).fetchone()[0]
        cur = conn.execute(
            """INSERT INTO story_turns (session_id, seq, role, content, image_sha, created_at)
               VALUES (?,?,?,?,?,?)""",
            (session_id, seq, role, content or "", image_sha or None, _now()),
        )
        conn.execute(
            """UPDATE story_sessions
               SET turn_count=(SELECT COUNT(*) FROM story_turns WHERE session_id=? AND role IN ('user','assistant')),
                   message_count=(SELECT COUNT(*) FROM story_turns WHERE session_id=?),
                   updated_at=?
               WHERE id=?""",
            (session_id, session_id, _now(), session_id),
        )
        conn.commit()
        return cur.lastrowid

    def link_image(self, session_id: int, sha: str, prompt: str = "",
                   prompt_raw: str = "", width: int = 0, height: int = 0,
                   workflow: str = "", seed: str = "", turn_id: int = None) -> int:
        """关联一张剧情期间生成的图片；刷新会话 image_count。"""
        conn = self._conn_get()
        cur = conn.execute(
            """INSERT INTO story_images
               (session_id, turn_id, sha, prompt, prompt_raw, width, height, workflow, seed, created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (session_id, turn_id, sha, prompt or "", prompt_raw or "",
             width or 0, height or 0, workflow or "", seed or "", _now()),
        )
        conn.execute(
            "UPDATE story_sessions SET image_count=(SELECT COUNT(*) FROM story_images WHERE session_id=?), updated_at=? WHERE id=?",
            (session_id, _now(), session_id),
        )
        conn.commit()
        return cur.lastrowid

    def finish_session(self, session_id: int, summary: str = None, **fields) -> None:
        """结束会话：写摘要与结束时间，并套用可编辑字段。"""
        conn = self._conn_get()
        now = _now()
        sets = ["status='finished'", "ended_at=?", "updated_at=?"]
        params: list = [now, now]
        if summary is not None:
            sets.append("summary=?")
            params.append(summary)
        for f in ("title", "mood", "scene", "characters", "tags", "notes", "source"):
            if f in fields:
                sets.append(f"{f}=?")
                params.append(fields[f])
        if "rating" in fields:
            sets.append("rating=?")
            params.append(fields["rating"] or 0)
        if "extra" in fields:
            sets.append("extra=?")
            params.append(json.dumps(fields["extra"] or {}, ensure_ascii=False))
        params.append(session_id)
        conn.execute(f"UPDATE story_sessions SET {','.join(sets)} WHERE id=?", params)
        conn.commit()

    def update_session(self, session_id: int, fields: dict) -> None:
        """WebUI 管理页编辑：仅更新允许的前端字段。"""
        allowed = {"title", "summary", "mood", "scene", "characters", "tags",
                   "rating", "notes", "status", "source"}
        conn = self._conn_get()
        sets = ["updated_at=?"]
        params: list = [_now()]
        for k, v in fields.items():
            if k not in allowed:
                continue
            sets.append(f"{k}=?")
            params.append(v if k != "rating" else (v or 0))
        if len(params) == 1:
            return
        params.append(session_id)
        conn.execute(f"UPDATE story_sessions SET {','.join(sets)} WHERE id=?", params)
        conn.commit()

    def delete_sessions(self, ids: list[int]) -> int:
        """删除若干会话（含其 turns / images）。返回删除条数。"""
        if not ids:
            return 0
        conn = self._conn_get()
        qmarks = ",".join("?" * len(ids))
        conn.execute(f"DELETE FROM story_turns WHERE session_id IN ({qmarks})", ids)
        conn.execute(f"DELETE FROM story_images WHERE session_id IN ({qmarks})", ids)
        cur = conn.execute(f"DELETE FROM story_sessions WHERE id IN ({qmarks})", ids)
        conn.commit()
        return cur.rowcount

    def last_assistant_turn(self, session_id: int) -> str:
        """返回该会话最后一条 assistant 轮次的文本（去重用）。"""
        conn = self._conn_get()
        row = conn.execute(
            "SELECT content FROM story_turns WHERE session_id=? AND role='assistant' ORDER BY seq DESC LIMIT 1",
            (session_id,),
        ).fetchone()
        return (row["content"] if row else "") or ""

    # ------------------------------------------------------------------ #
    # 查询
    # ------------------------------------------------------------------ #
    def get_sessions(self, page: int = 1, size: int = 20, keyword: str = "",
                     user_id: str = "", status: str = "",
                     date_from: str = "", date_to: str = "") -> tuple[list, int]:
        """分页检索会话列表（不含 turns/images，控制体积）。"""
        conn = self._conn_get()
        where = []
        params: list = []
        if keyword:
            like = f"%{keyword}%"
            where.append("(title LIKE ? OR summary LIKE ? OR tags LIKE ? OR characters LIKE ? OR scene LIKE ?)")
            params += [like, like, like, like, like]
        if user_id:
            where.append("user_id=?")
            params.append(user_id)
        if status:
            where.append("status=?")
            params.append(status)
        if date_from:
            where.append("started_at>=?")
            params.append(date_from)
        if date_to:
            where.append("started_at<=?")
            params.append(date_to)
        wsql = (" WHERE " + " AND ".join(where)) if where else ""
        total = conn.execute(f"SELECT COUNT(*) FROM story_sessions{wsql}", params).fetchone()[0]
        offset = max(0, (page - 1) * size)
        rows = conn.execute(
            f"SELECT * FROM story_sessions{wsql} ORDER BY started_at DESC LIMIT ? OFFSET ?",
            params + [size, offset],
        ).fetchall()
        return [self._row_to_dict(r) for r in rows], total

    def get_session(self, session_id: int) -> dict | None:
        """取完整档案（含 turns + images）。"""
        conn = self._conn_get()
        row = conn.execute("SELECT * FROM story_sessions WHERE id=?", (session_id,)).fetchone()
        if row is None:
            return None
        sess = self._row_to_dict(row)
        turns = conn.execute(
            "SELECT * FROM story_turns WHERE session_id=? ORDER BY seq ASC", (session_id,)
        ).fetchall()
        images = conn.execute(
            "SELECT * FROM story_images WHERE session_id=? ORDER BY id ASC", (session_id,)
        ).fetchall()
        sess["turns"] = [self._row_to_dict(t) for t in turns]
        sess["images"] = [self._row_to_dict(i) for i in images]
        return sess

    def stats(self) -> dict:
        """概览统计。"""
        conn = self._conn_get()
        total = conn.execute("SELECT COUNT(*) FROM story_sessions").fetchone()[0]
        finished = conn.execute("SELECT COUNT(*) FROM story_sessions WHERE status='finished'").fetchone()[0]
        active = conn.execute("SELECT COUNT(*) FROM story_sessions WHERE status='active'").fetchone()[0]
        images = conn.execute("SELECT COUNT(*) FROM story_images").fetchone()[0]
        turns = conn.execute("SELECT COUNT(*) FROM story_turns").fetchone()[0]
        return {
            "total": total, "finished": finished, "active": active,
            "images": images, "turns": turns,
        }

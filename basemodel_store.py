"""底模（Base Model）库存储。

v7.0.0 新增：把「底模」从自由文本升级为可管理的实体库——
支持语言 / 优先语种 / 提示词风格 / danbooru 适配 / C站链接 / 封面。
工作流配置侧只读引用（按 file_name 关联），本模块负责增删查改。

沿用本插件惯例：每个业务域一个独立 SQLite（data_dir/basemodel.db），
WAL + CREATE TABLE IF NOT EXISTS + _ensure_columns 缺列迁移（CharacterStore 范式）。
单线程事件循环使用，线程不安全但足够。
"""

from __future__ import annotations

import json
import logging
import sqlite3
import time
from pathlib import Path

logger = logging.getLogger(__name__)

# 提示词风格枚举：natural=自然语言（可掺杂标签）；danbooru=danbooru 标签
PROMPT_STYLES = ("natural", "danbooru")
# 支持语言枚举（v7.0.0 暂只做中英）
LANGUAGES = ("中文", "英文")


class BaseModelStore:
    """底模库存储。单线程事件循环使用。"""

    def __init__(self, data_dir: Path) -> None:
        self.data_dir = Path(data_dir)
        try:
            self.data_dir.mkdir(parents=True, exist_ok=True)
        except Exception:
            pass
        self.db_path = self.data_dir / "basemodel.db"
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
                logger.warning(f"[底模库] 开启 WAL 失败（不影响使用）: {e}")
        return self._conn

    def _ensure_columns(self, table: str, cols: "dict[str, str]") -> None:
        """缺列则补（幂等），CharacterStore 同款迁移助手。"""
        try:
            conn = self._conn_get()
            have = {
                (r["name"] if isinstance(r, sqlite3.Row) else r[1])
                for r in conn.execute(f"PRAGMA table_info({table})").fetchall()
            }
            for _col, _decl in (cols or {}).items():
                if _col in have:
                    continue
                try:
                    conn.execute(f"ALTER TABLE {table} ADD COLUMN {_col} {_decl}")
                    logger.info(f"【底模库】 迁移：{table} 补列 {_col}")
                except Exception as e:
                    if "duplicate column" not in str(e).lower():
                        logger.warning(f"【底模库】 迁移补列 {table}.{_col} 失败: {e}")
            conn.commit()
        except Exception as e:
            logger.warning(f"【底模库】 迁移检查失败（{table}）: {e}")

    def _init_db(self) -> None:
        conn = self._conn_get()
        conn.execute(
            """CREATE TABLE IF NOT EXISTS basemodels (
                id             INTEGER PRIMARY KEY AUTOINCREMENT,
                name           TEXT NOT NULL,
                file_name      TEXT DEFAULT '',
                civitai_url    TEXT DEFAULT '',
                image          TEXT DEFAULT '',
                prompt_style   TEXT DEFAULT 'natural',
                languages      TEXT DEFAULT '["中文", "英文"]',
                priority_lang  TEXT DEFAULT '中文',
                danbooru_ready INTEGER NOT NULL DEFAULT 0,
                description    TEXT DEFAULT '',
                created_at     REAL NOT NULL DEFAULT 0,
                updated_at     REAL NOT NULL DEFAULT 0
            )"""
        )
        self._ensure_columns("basemodels", {
            "file_name": "TEXT DEFAULT ''",
            "civitai_url": "TEXT DEFAULT ''",
            "image": "TEXT DEFAULT ''",
            "prompt_style": "TEXT DEFAULT 'natural'",
            "languages": "TEXT DEFAULT '[\"中文\", \"英文\"]'",
            "priority_lang": "TEXT DEFAULT '中文'",
            "danbooru_ready": "INTEGER NOT NULL DEFAULT 0",
            "description": "TEXT DEFAULT ''",
        })
        conn.commit()

    # ------------------------------------------------------------------ #
    # 行序列化
    # ------------------------------------------------------------------ #
    @staticmethod
    def _row_to_dict(row) -> dict:
        d = dict(row)
        try:
            langs = json.loads(d.get("languages") or "[]")
            d["languages"] = langs if isinstance(langs, list) else []
        except Exception:
            d["languages"] = []
        d["danbooru_ready"] = bool(d.get("danbooru_ready"))
        return d

    # ------------------------------------------------------------------ #
    # CRUD
    # ------------------------------------------------------------------ #
    def list_all(self) -> list[dict]:
        conn = self._conn_get()
        rows = conn.execute(
            "SELECT * FROM basemodels ORDER BY name COLLATE NOCASE ASC"
        ).fetchall()
        return [self._row_to_dict(r) for r in rows]

    def get(self, model_id: int) -> dict | None:
        conn = self._conn_get()
        row = conn.execute(
            "SELECT * FROM basemodels WHERE id=?", (int(model_id),)
        ).fetchone()
        return self._row_to_dict(row) if row else None

    def find_by_file_name(self, file_name: str) -> dict | None:
        """按模型文件名（unet_name/ckpt_name）精确关联（大小写不敏感）。"""
        fn = (file_name or "").strip().lower()
        if not fn:
            return None
        conn = self._conn_get()
        row = conn.execute(
            "SELECT * FROM basemodels WHERE lower(file_name)=?", (fn,)
        ).fetchone()
        return self._row_to_dict(row) if row else None

    def save(self, data: dict) -> tuple[int | None, str | None]:
        """新增/更新。返回 (id, error)。error 非空即失败。

        data 必带 name；带 id=更新，否则新增（file_name 冲突时更新同文件名条目）。
        """
        name = (data.get("name") or "").strip()
        if not name:
            return None, "底模名称不能为空"
        file_name = (data.get("file_name") or "").strip()
        style = (data.get("prompt_style") or "natural").strip().lower()
        if style not in PROMPT_STYLES:
            style = "natural"
        langs = data.get("languages")
        if not isinstance(langs, list):
            langs = ["中文", "英文"]
        langs = [str(x) for x in langs if str(x) in LANGUAGES] or ["中文", "英文"]
        prio = (data.get("priority_lang") or "").strip()
        if prio not in langs:
            prio = langs[0]
        now = time.time()
        conn = self._conn_get()
        mid = data.get("id")
        try:
            if mid:
                conn.execute(
                    """UPDATE basemodels SET name=?, file_name=?, civitai_url=?, image=?,
                       prompt_style=?, languages=?, priority_lang=?, danbooru_ready=?,
                       description=?, updated_at=? WHERE id=?""",
                    (name, file_name, (data.get("civitai_url") or "").strip(),
                     (data.get("image") or "").strip(), style, json.dumps(langs, ensure_ascii=False),
                     prio, 1 if data.get("danbooru_ready") else 0,
                     (data.get("description") or "").strip(), now, int(mid)),
                )
                conn.commit()
                return int(mid), None
            # 新增：file_name 已登记则覆盖更新那条（实现「同名文件名去重」）
            if file_name:
                exist = self.find_by_file_name(file_name)
                if exist:
                    data = {**exist, **data, "id": exist["id"]}
                    return self.save(data)
            cur = conn.execute(
                """INSERT INTO basemodels (name, file_name, civitai_url, image, prompt_style,
                   languages, priority_lang, danbooru_ready, description, created_at, updated_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                (name, file_name, (data.get("civitai_url") or "").strip(),
                 (data.get("image") or "").strip(), style, json.dumps(langs, ensure_ascii=False),
                 prio, 1 if data.get("danbooru_ready") else 0,
                 (data.get("description") or "").strip(), now, now),
            )
            conn.commit()
            return int(cur.lastrowid), None
        except Exception as e:
            return None, str(e)

    def delete(self, model_id: int) -> str | None:
        try:
            conn = self._conn_get()
            conn.execute("DELETE FROM basemodels WHERE id=?", (int(model_id),))
            conn.commit()
            return None
        except Exception as e:
            return str(e)

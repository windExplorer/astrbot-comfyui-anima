"""底模（模型族）存储 —— v7.0.3 起语义修正。

这里的「底模」指市面上的开源绘图模型**族**（anima / krea2 / z-image-turbo /
qwen image 2.1 / boogu 等，此前写死在代码里），现在是可增删改查的动态配置：
名称、匹配关键字（用于与基础工作流解析出的模型文件名自动关联）、
支持语言 + 优先语种、提示词风格、danbooru 适配、描述、封面。

（C站链接与采集在「基础工作流」侧，不在本库。）

沿用本插件惯例：独立 SQLite（data_dir/basemodel.db），WAL + 缺列迁移。
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
                keywords       TEXT DEFAULT '',
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
            "keywords": "TEXT DEFAULT ''",
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

    @staticmethod
    def _split_keywords(raw) -> list[str]:
        """关键字拆分：逗号/顿号/换行/分号/空格分隔，去空去重（保序，小写比较用）。"""
        if isinstance(raw, list):
            parts = [str(x) for x in raw]
        else:
            import re as _re
            parts = _re.split(r"[,，、;；\n\r\t ]+", str(raw or ""))
        out: list[str] = []
        for p in parts:
            p = p.strip()
            if p and p not in out:
                out.append(p)
        return out

    def find_by_id(self, model_id) -> dict | None:
        return self.get(model_id)

    def match_model(self, model_file: str = "", class_type: str = "") -> dict | None:
        """按「匹配关键字」在模型文件名 / 底模类名里找所属底模（模型族）。

        关键字大小写不敏感、子串匹配；多个命中时取关键字最长（最具体）的那个，
        例如 qwen_image_2.1_int8_convrot.safetensors 同时含 "qwen" 与
        "qwen_image"，应命中关键字更长的那个条目。
        """
        hay = f"{model_file or ''} {class_type or ''}".lower()
        if not hay.strip():
            return None
        best = None
        best_len = 0
        for row in self.list_all():
            for kw in self._split_keywords(row.get("keywords") or ""):
                k = kw.lower()
                if k and k in hay and len(k) > best_len:
                    best, best_len = row, len(k)
        return best

    def save(self, data: dict) -> tuple[int | None, str | None]:
        """新增/更新。返回 (id, error)。error 非空即失败。

        data 必带 name；带 id=更新，否则新增（同名条目更新）。
        """
        name = (data.get("name") or "").strip()
        if not name:
            return None, "底模名称不能为空"
        keywords = "、".join(self._split_keywords(data.get("keywords") or ""))
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
                    """UPDATE basemodels SET name=?, keywords=?, image=?,
                       prompt_style=?, languages=?, priority_lang=?, danbooru_ready=?,
                       description=?, updated_at=? WHERE id=?""",
                    (name, keywords, (data.get("image") or "").strip(), style,
                     json.dumps(langs, ensure_ascii=False),
                     prio, 1 if data.get("danbooru_ready") else 0,
                     (data.get("description") or "").strip(), now, int(mid)),
                )
                conn.commit()
                return int(mid), None
            # 新增：同名条目则覆盖更新
            row = conn.execute(
                "SELECT id FROM basemodels WHERE name=? COLLATE NOCASE", (name,)
            ).fetchone()
            if row:
                data = {**data, "id": row["id"]}
                return self.save(data)
            cur = conn.execute(
                """INSERT INTO basemodels (name, keywords, image, prompt_style,
                   languages, priority_lang, danbooru_ready, description, created_at, updated_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?)""",
                (name, keywords, (data.get("image") or "").strip(), style,
                 json.dumps(langs, ensure_ascii=False),
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

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
PROMPT_STYLES = ("natural", "qwen", "danbooru")
# 支持语言枚举（v7.0.0 暂只做中英）
LANGUAGES = ("中文", "英文")

# 默认底模（模型族）——首次初始化/一键补齐时写入。
# 依据：插件代码里出现过的底模提示词规范（main.py 的 LLM 指引 + 老工作流下拉白名单），
# 加上近期在用的 Qwen Image 2.1 与 boogu 编辑模型。
DEFAULT_BASEMODELS = (
    {
        "name": "anima", "keywords": "anima",
        "prompt_style": "danbooru", "languages": ["英文", "中文"], "priority_lang": "英文",
        "danbooru_ready": True,
        "description": "动漫标签系：Danbooru 标签 + 质量前缀（masterpiece, best quality, very aesthetic, absurdres）；禁自然语言长句、禁 Pony 质量词。",
    },
    {
        "name": "illustrious", "keywords": "illustrious",
        "prompt_style": "danbooru", "languages": ["英文"], "priority_lang": "英文",
        "danbooru_ready": True,
        "description": "动漫 SDXL 系（Illustrious）：Danbooru 标签 + 质量前缀。",
    },
    {
        "name": "NoobAI", "keywords": "noobai、noob",
        "prompt_style": "danbooru", "languages": ["英文"], "priority_lang": "英文",
        "danbooru_ready": True,
        "description": "动漫 SDXL 系（NoobAI-XL）：Danbooru 标签风格。",
    },
    {
        "name": "Pony", "keywords": "pony",
        "prompt_style": "danbooru", "languages": ["英文"], "priority_lang": "英文",
        "danbooru_ready": True,
        "description": "Pony Diffusion：score_9 / score_8_up / score_7_up 质量体系 + Danbooru 标签（该质量词仅本族可用）。",
    },
    {
        "name": "z-image-turbo", "keywords": "z-image、zimage、z_image",
        "prompt_style": "natural", "languages": ["中文", "英文"], "priority_lang": "中文",
        "danbooru_ready": False,
        "description": "阿里 Z-Image Turbo：中文或英文自然语言整句（中文理解最好），不写标签/质量词；要渲染的文字放引号。",
    },
    {
        "name": "krea2", "keywords": "krea",
        "prompt_style": "natural", "languages": ["英文"], "priority_lang": "英文",
        "danbooru_ready": False,
        "description": "自然语言系（Krea）：英文整句描述，不写 Danbooru 标签与质量词。",
    },
    {
        "name": "FLUX", "keywords": "flux",
        "prompt_style": "natural", "languages": ["英文"], "priority_lang": "英文",
        "danbooru_ready": False,
        "description": "FLUX.1 系：英文自然语言整句，不写标签/质量词。",
    },
    {
        "name": "Qwen Image 2.1", "keywords": "qwen、qwen_image、qwenimage",
        "prompt_style": "qwen", "languages": ["中文", "英文"], "priority_lang": "中文",
        "danbooru_ready": False,
        "description": "Qwen-Image 2.1：**官方格式长描述**（文生图约 20 句英文长段、不写比例与画质套话；"
                       "图像编辑正文语言随指令、多图用 <image1> 引用）。选此项后第三方插件调用/含中文的"
                       "原生调用会按 skills/qwen-image/ 里的规范改写提示词。",
    },
    {
        "name": "boogu（编辑/加字）", "keywords": "boogu",
        "prompt_style": "natural", "languages": ["中文", "英文"], "priority_lang": "中文",
        "danbooru_ready": False,
        "description": "boogu-edit-turbo：图生图编辑/加字模型，吃自然语言指令（保持原图不变 + 要加的文字）。",
    },
    {
        "name": "SDXL", "keywords": "sdxl",
        "prompt_style": "danbooru", "languages": ["英文"], "priority_lang": "英文",
        "danbooru_ready": True,
        "description": "SDXL 通用：质量词（masterpiece, best quality）+ 标签混合。",
    },
    {
        "name": "SD 1.5", "keywords": "sd15、sd1.5、sd-v1-5、v1-5",
        "prompt_style": "danbooru", "languages": ["英文"], "priority_lang": "英文",
        "danbooru_ready": True,
        "description": "SD 1.5 通用：质量词 + 标签混合。",
    },
)


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
        # 列迁移之后再播种（旧库缺 keywords 列时也能正常写入）
        self._seed_defaults()

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

    def _seed_defaults(self) -> int:
        """空库时写入默认底模（模型族）。返回写入条数。

        仅在**完全空库**时播种：避免用户删掉的默认项被反复塞回来。
        想找回默认项用 reseed_defaults()（只补缺，不覆盖已有同名的自定义内容）。
        """
        try:
            conn = self._conn_get()
            n = conn.execute("SELECT COUNT(*) AS c FROM basemodels").fetchone()["c"]
            if n:
                return 0
        except Exception:
            return 0
        added = 0
        for item in DEFAULT_BASEMODELS:
            _, err = self.save(dict(item))
            if not err:
                added += 1
        if added:
            logger.info(f"【底模库】 已写入 {added} 条默认底模（模型族），可在「配置项」页增删改")
        return added

    def reseed_defaults(self) -> int:
        """补齐缺失的默认底模（按名称判断，已存在的不动）。返回新增条数。"""
        have = {str(r.get("name") or "").strip().lower() for r in self.list_all()}
        added = 0
        for item in DEFAULT_BASEMODELS:
            if str(item["name"]).strip().lower() in have:
                continue
            _, err = self.save(dict(item))
            if not err:
                added += 1
        if added:
            logger.info(f"【底模库】 已补齐 {added} 条默认底模")
        return added

    def delete(self, model_id: int) -> str | None:
        try:
            conn = self._conn_get()
            conn.execute("DELETE FROM basemodels WHERE id=?", (int(model_id),))
            conn.commit()
            return None
        except Exception as e:
            return str(e)

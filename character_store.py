"""角色卡片（Character Card）存储层。

独立的 SQLite（data_dir/character.db）维护三类数据：

- ``characters``        角色卡：角色名 / 别名 / 绑定人格 / 作品 / 关联 LoRA / 主锚点 …
- ``character_anchors`` 锚点：一个角色可有多套「提示词标签组」（默认装 / 泳装 / 校服…），
  每套含正标签串、负标签串、建议权重、可选覆盖 LoRA、是否抑制全局触发词
- ``character_refs``    参考图（0~N 张）：本地路径 / 来源 URL / sha256 / NSFW 打标

三张表都带 ``created_at`` + ``created_by``（谁建的）；角色卡另有 ``source``（创建方式），
参考图另有 ``origin``（上传 / 指令 / 工具 / 图库 / 出图自动关联）与 ``external``
（文件不归本插件所有，如直接引用 gallery/ 里的成品图 —— 删记录时**不得**删文件）。

设计要点（见 ``docs/TODO-角色卡片.md``）：

- **persona ↔ character 为可选 1:1 绑定**：``characters.persona_name`` 非空即表示该卡是
  某个 bot 人格的形象（用户说「画你」时按当前会话人格查这张卡）。
- **锚点是绘图的实际载体**：所有进提示词的东西都在锚点里，角色卡只存身份信息。
- 未指定锚点时用「主锚点」（``characters.primary_anchor_id``，每个角色恰有 1 个）。

沿用本插件既有 store 范式：WAL + ``CREATE TABLE IF NOT EXISTS`` + 缺列 ``ALTER TABLE`` 迁移，
单线程事件循环使用（线程不安全但足够）。
"""

import json
import logging
import shutil
import sqlite3
import time
from pathlib import Path

logger = logging.getLogger("astrbot_plugin_comfyui_anima.character")

# 角色卡可写字段白名单（update 用，防注入任意列名）
# `created_by` 可写只为「老数据回填一次」服务（见 create_character 的合并分支）；
# 各调用方的 update 入参都是显式字段名，不会由前端传。
_CHAR_FIELDS = {
    "name", "aliases", "persona_name", "work", "lora_name",
    "primary_anchor_id", "source", "note", "enabled",
    "cover_ref_id",  # v6.1.0：角色封面（指向 character_refs.id，0 = 自动取第一张）
    "created_by",    # v6.3.0：谁建的
}
_ANCHOR_FIELDS = {
    "name", "kind", "positive", "negative", "weight",
    "lora_name", "skip_trigger_words", "note", "sort_order",
}

# 锚点种类：appearance=只写外观；outfit=服装/造型；full=外观+服装（默认，最常用）
ANCHOR_KINDS = ("full", "appearance", "outfit")

# 角色卡「创建方式」（characters.source）。历史数据统一是 "user"，前端按「未记录」展示。
CHAR_SOURCES = ("webui", "command", "llm", "import", "user")

# 参考图来源（character_refs.origin）：决定前端展示文案，也决定自动清理的范围——
# 只有 auto 会被 auto_link_keep 上限裁掉，人工录入的一律保留。
REF_ORIGIN_UPLOAD = "upload"    # WebUI 上传
REF_ORIGIN_COMMAND = "command"  # /角色 参考图 记住
REF_ORIGIN_TOOL = "tool"        # comfyui_character 工具 add_ref
REF_ORIGIN_GALLERY = "gallery"  # 从图库按 sha 导入（人工点选）
REF_ORIGIN_AUTO = "auto"        # 出图命中锚点后自动关联（v6.3.0）
REF_ORIGIN_WEB = "web"          # 联网抓取直链


def _json_list(raw) -> list[str]:
    """把 DB 里的 aliases（JSON 数组字符串）解析成 list[str]，容错各种脏数据。"""
    if not raw:
        return []
    if isinstance(raw, list):
        return [str(x).strip() for x in raw if str(x).strip()]
    try:
        val = json.loads(raw)
        if isinstance(val, list):
            return [str(x).strip() for x in val if str(x).strip()]
    except Exception:
        pass
    # 兼容「逗号分隔字符串」的历史写法
    return [p.strip() for p in str(raw).replace("，", ",").split(",") if p.strip()]


# 参考图单张大小上限（与工具侧 add_ref 的 12MB 保持一致，v6.0.0）
_MAX_REF_BYTES = 12 * 1024 * 1024


def _dump_list(items) -> str:
    return json.dumps(
        [str(x).strip() for x in (items or []) if str(x).strip()],
        ensure_ascii=False,
    )


def _slugify(name: str, maxlen: int = 40) -> str:
    """角色名 → 目录名安全串（保留中英文与数字，其余替成下划线；空则 'char'）。"""
    import re as _re

    s = _re.sub(r"[^\w\u4e00-\u9fff\-]+", "_", (name or "").strip())
    s = s.strip("_")[:maxlen]
    return s or "char"


class CharacterStore:
    """角色卡片存储。单线程事件循环使用，线程不安全但足够。"""

    def __init__(self, data_dir: Path) -> None:
        self.data_dir = Path(data_dir)
        # v6.0.0：目录缺失时 sqlite3.connect 会直接 OperationalError（旧代码假定
        # data_dir 一定存在）。这里显式创建，与 platform_store 的做法一致。
        try:
            self.data_dir.mkdir(parents=True, exist_ok=True)
        except Exception:
            pass
        self.db_path = self.data_dir / "character.db"
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
                logger.warning(f"[角色卡] 开启 WAL 失败（不影响使用）: {e}")
        return self._conn

    def _ensure_columns(self, table: str, cols: "dict[str, str]") -> None:
        """缺列则补（幂等）。`cols` = {列名: 列定义}。

        v6.1.0 新增：本库此前只能靠 `CREATE TABLE IF NOT EXISTS` 建表，**没有任何升级路径**
        （审计指出）——给已存在的表加字段会静默失效。这里按 `PRAGMA table_info` 判断后 ALTER。
        失败只在「列已存在」时静默，其余情况记日志（避免像 image_store 那样把库锁/损坏一起吞掉）。
        """
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
                    logger.info(f"【角色卡】 迁移：{table} 补列 {_col}")
                except Exception as e:
                    if "duplicate column" not in str(e).lower():
                        logger.warning(f"【角色卡】 迁移补列 {table}.{_col} 失败: {e}")
            conn.commit()
        except Exception as e:
            logger.warning(f"【角色卡】 迁移检查失败（{table}）: {e}")

    def _init_db(self) -> None:
        conn = self._conn_get()
        conn.execute(
            """CREATE TABLE IF NOT EXISTS characters (
                id                INTEGER PRIMARY KEY AUTOINCREMENT,
                name              TEXT NOT NULL,
                aliases           TEXT DEFAULT '[]',
                persona_name      TEXT DEFAULT '',
                work              TEXT DEFAULT '',
                lora_name         TEXT DEFAULT '',
                primary_anchor_id INTEGER NOT NULL DEFAULT 0,
                source            TEXT DEFAULT 'user',
                note              TEXT DEFAULT '',
                enabled           INTEGER NOT NULL DEFAULT 1,
                created_at        REAL NOT NULL DEFAULT 0,
                updated_at        REAL NOT NULL DEFAULT 0
            )"""
        )
        conn.execute(
            """CREATE TABLE IF NOT EXISTS character_anchors (
                id                 INTEGER PRIMARY KEY AUTOINCREMENT,
                character_id       INTEGER NOT NULL,
                name               TEXT NOT NULL,
                kind               TEXT DEFAULT 'full',
                positive           TEXT NOT NULL DEFAULT '',
                negative           TEXT DEFAULT '',
                weight             REAL NOT NULL DEFAULT 1.2,
                lora_name          TEXT DEFAULT '',
                skip_trigger_words INTEGER NOT NULL DEFAULT 1,
                note               TEXT DEFAULT '',
                sort_order         INTEGER NOT NULL DEFAULT 0,
                created_at         REAL NOT NULL DEFAULT 0,
                updated_at         REAL NOT NULL DEFAULT 0
            )"""
        )
        conn.execute(
            """CREATE TABLE IF NOT EXISTS character_refs (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                character_id INTEGER NOT NULL,
                anchor_id    INTEGER NOT NULL DEFAULT 0,
                path         TEXT NOT NULL DEFAULT '',
                url          TEXT DEFAULT '',
                sha256       TEXT DEFAULT '',
                nsfw_score   REAL DEFAULT -1,
                note         TEXT DEFAULT '',
                created_at   REAL NOT NULL DEFAULT 0
            )"""
        )
        # 缺列迁移（v6.1.0）：
        #   character_refs.anchor_id —— 图片可挂到某个**锚点**下（0 = 角色级，未绑定锚点）
        #   characters.cover_ref_id  —— 角色**封面**（指向 character_refs.id，0 = 未设、取第一张）
        # 此前 character_store 完全没有迁移路径（审计已指出），这里补上通用助手。
        self._ensure_columns(
            "character_refs",
            {
                "anchor_id": "INTEGER NOT NULL DEFAULT 0",
            },
        )
        self._ensure_columns(
            "characters",
            {
                "cover_ref_id": "INTEGER NOT NULL DEFAULT 0",
            },
        )
        # 缺列迁移（v6.3.0）：把「谁、以什么方式」补齐到三张表
        #   *.created_by       —— 创建者（QQ 昵称(号) / WebUI 控制台 / 导入）
        #   character_anchors.source —— 锚点的创建渠道（角色卡早有该字段，锚点此前没有）
        #   character_refs.origin    —— 图片来源渠道（upload/command/tool/gallery/auto/web）
        #   character_refs.external  —— 1 = 文件不归本卡所有（图库成品图），删记录时不删盘
        self._ensure_columns(
            "characters",
            {"created_by": "TEXT NOT NULL DEFAULT ''"},
        )
        self._ensure_columns(
            "character_anchors",
            {
                "created_by": "TEXT NOT NULL DEFAULT ''",
                "source": "TEXT NOT NULL DEFAULT 'user'",
            },
        )
        self._ensure_columns(
            "character_refs",
            {
                "created_by": "TEXT NOT NULL DEFAULT ''",
                "origin": "TEXT NOT NULL DEFAULT 'upload'",
                "external": "INTEGER NOT NULL DEFAULT 0",
            },
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_anchor_char ON character_anchors(character_id)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_ref_char ON character_refs(character_id)"
        )
        conn.commit()

    # ------------------------------------------------------------------ #
    # 内部工具
    # ------------------------------------------------------------------ #
    @staticmethod
    def _row_to_char(row, anchor_count: int = 0) -> dict | None:
        if row is None:
            return None
        d = dict(row)
        d["aliases"] = _json_list(d.get("aliases"))
        d["enabled"] = bool(d.get("enabled", 1))
        if anchor_count:
            d["anchor_count"] = anchor_count
        return d

    @staticmethod
    def _row_to_anchor(row) -> dict | None:
        if row is None:
            return None
        d = dict(row)
        d["skip_trigger_words"] = bool(d.get("skip_trigger_words", 1))
        try:
            d["weight"] = float(d.get("weight") or 1.2)
        except (TypeError, ValueError):
            d["weight"] = 1.2
        return d

    @staticmethod
    def _row_to_ref(row) -> dict | None:
        """参考图行 → dict：`external` 转 bool、`nsfw_score` 转 float，前端拿到的形状稳定。"""
        if row is None:
            return None
        d = dict(row)
        d["external"] = bool(d.get("external") or 0)
        try:
            d["nsfw_score"] = float(d.get("nsfw_score") if d.get("nsfw_score") is not None else -1)
        except (TypeError, ValueError):
            d["nsfw_score"] = -1.0
        d.setdefault("origin", REF_ORIGIN_UPLOAD)
        return d

    def _find_char_row(self, key):
        """按 id（int / 纯数字串）或名称（大小写不敏感）精确查一行。"""
        conn = self._conn_get()
        if isinstance(key, int) or (isinstance(key, str) and key.strip().isdigit()):
            return conn.execute(
                "SELECT * FROM characters WHERE id=?", (int(key),)
            ).fetchone()
        _k = str(key or "").strip().lower()
        if not _k:
            return None
        for row in conn.execute("SELECT * FROM characters ORDER BY id"):
            if (row["name"] or "").strip().lower() == _k:
                return row
            for al in _json_list(row["aliases"]):
                if al.lower() == _k:
                    return row
        return None

    # ------------------------------------------------------------------ #
    # 角色卡 CRUD
    # ------------------------------------------------------------------ #
    def create_character(
        self,
        name: str,
        aliases=None,
        persona_name: str = "",
        work: str = "",
        lora_name: str = "",
        source: str = "user",
        note: str = "",
        enabled: bool = True,
        created_by: str = "",
    ) -> dict:
        """新建角色卡。同名（大小写不敏感）或命中已有别名时**返回已有卡**并补齐传入字段。

        `source` = 创建方式（webui / command / llm / import），`created_by` = 谁建的；
        两者只在**新建**时写入，已有卡不会被覆盖（谁建的应当永远是当初那个人）。
        """
        _name = (name or "").strip()
        if not _name:
            raise ValueError("角色名不能为空")
        exist = self._find_char_row(_name)
        if exist is not None:
            upd = {}
            if aliases:
                merged = _json_list(exist["aliases"]) + _json_list(aliases)
                upd["aliases"] = _dump_list(list(dict.fromkeys(merged)))
            for k, v in (
                ("persona_name", persona_name), ("work", work),
                ("lora_name", lora_name), ("note", note),
            ):
                if (v or "").strip() and not (exist[k] or "").strip():
                    upd[k] = v.strip()
            # 老数据没记「谁建的」→ 这次知道了就补上（只在为空时回填，不改写既有归属）
            if (created_by or "").strip() and not (exist["created_by"] or "").strip():
                upd["created_by"] = created_by.strip()
            if upd:
                self.update_character(int(exist["id"]), **upd)
            return self.get_character(int(exist["id"]))
        now = time.time()
        conn = self._conn_get()
        cur = conn.execute(
            """INSERT INTO characters
               (name, aliases, persona_name, work, lora_name, primary_anchor_id,
                source, note, enabled, created_at, updated_at, created_by)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                _name, _dump_list(aliases), (persona_name or "").strip(),
                (work or "").strip(), (lora_name or "").strip(), 0,
                (source or "user").strip(), (note or "").strip(),
                1 if enabled else 0, now, now,
                (created_by or "").strip(),
            ),
        )
        conn.commit()
        logger.info(f"【角色卡】 新建角色「{_name}」（id={cur.lastrowid}）")
        return self.get_character(int(cur.lastrowid))

    def update_character(self, char_id, **fields) -> dict | None:
        conn = self._conn_get()
        sets, vals = [], []
        for k, v in (fields or {}).items():
            if k not in _CHAR_FIELDS:
                continue
            if k == "aliases":
                v = _dump_list(v)
            elif k == "enabled":
                v = 1 if v else 0
            sets.append(f"{k}=?")
            vals.append(v)
        if not sets:
            return self.get_character(char_id)
        sets.append("updated_at=?")
        vals.append(time.time())
        vals.append(int(char_id))
        conn.execute(f"UPDATE characters SET {', '.join(sets)} WHERE id=?", vals)
        conn.commit()
        return self.get_character(char_id)

    def delete_character(self, char_id) -> bool:
        """删除角色卡（连同其锚点、参考图记录与参考图文件目录）。"""
        row = self._find_char_row(char_id)
        if row is None:
            return False
        conn = self._conn_get()
        cid = int(row["id"])
        _slug = _slugify(row["name"] or "char")
        conn.execute("DELETE FROM character_anchors WHERE character_id=?", (cid,))
        conn.execute("DELETE FROM character_refs WHERE character_id=?", (cid,))
        conn.execute("DELETE FROM characters WHERE id=?", (cid,))
        conn.commit()
        # 参考图文件目录一并清理（数据行已删，留着只是占盘）
        try:
            _d = self.characters_dir() / f"{_slug}_{cid}"
            if _d.exists():
                shutil.rmtree(_d, ignore_errors=True)
        except Exception as e:
            logger.warning(f"【角色卡】 参考图目录清理失败（记录已删）: {e}")
        logger.info(f"【角色卡】 已删除角色「{row['name']}」（id={cid}，含锚点/参考图）")
        return True

    def get_character(self, key=None, fuzzy: bool = True) -> dict | None:
        """按 id / 名称 / 别名取角色卡。

        fuzzy=True 时额外做「去空格 + 包含」近似匹配（如「薄荷酱」命中「薄荷」），
        仅在唯一命中时返回，避免歧义。
        """
        row = self._find_char_row(key)
        if row is None and fuzzy and isinstance(key, str) and key.strip():
            _k = key.strip().lower()
            cands = []
            for r in self._conn_get().execute("SELECT * FROM characters ORDER BY id"):
                names = [(r["name"] or "").strip()] + _json_list(r["aliases"])
                if any(n and (n.lower() in _k or _k in n.lower()) for n in names):
                    cands.append(r)
            if len(cands) == 1:
                row = cands[0]
        if row is None:
            return None
        cid = int(row["id"])
        cnt = self._conn_get().execute(
            "SELECT COUNT(*) AS c FROM character_anchors WHERE character_id=?", (cid,)
        ).fetchone()["c"]
        return self._row_to_char(row, anchor_count=int(cnt or 0))

    def list_characters(self, keyword: str = "") -> list[dict]:
        kw = (keyword or "").strip().lower()
        out = []
        for row in self._conn_get().execute("SELECT * FROM characters ORDER BY id"):
            d = self._row_to_char(row)
            if kw:
                hay = " ".join(
                    [(d.get("name") or ""), *d.get("aliases", []),
                     (d.get("work") or ""), (d.get("persona_name") or "")]
                ).lower()
                if kw not in hay:
                    continue
            cnt = self._conn_get().execute(
                "SELECT COUNT(*) AS c FROM character_anchors WHERE character_id=?",
                (int(row["id"]),),
            ).fetchone()["c"]
            d["anchor_count"] = int(cnt or 0)
            out.append(d)
        return out

    def find_by_persona(self, persona_name: str) -> dict | None:
        """按绑定人格名取角色卡（「画你」链路）。"""
        _p = (persona_name or "").strip().lower()
        if not _p:
            return None
        rows = self._conn_get().execute(
            "SELECT * FROM characters WHERE LOWER(TRIM(persona_name))=? AND enabled=1 ORDER BY id",
            (_p,),
        ).fetchall()
        if not rows:
            return None
        if len(rows) > 1:
            logger.warning(
                f"【角色卡】 人格「{persona_name}」绑定了 {len(rows)} 张卡，取最早的一张"
            )
        return self.get_character(int(rows[0]["id"]))

    def match_characters(self, text: str) -> list[dict]:
        """扫描一段文本，返回其中**出现过的**角色卡（按首次出现位置排序，去重）。

        匹配对象：角色名 + 别名（大小写不敏感，忽略首尾空格）。名字长度 ≥2 才参与，
        避免单字名（如「零」）在普通句子里乱命中。
        """
        _t = (text or "").lower()
        if not _t:
            return []
        hits: list[tuple[int, int, dict]] = []
        for row in self._conn_get().execute("SELECT * FROM characters ORDER BY id"):
            d = self._row_to_char(row)
            if not d.get("enabled", True):
                continue
            names = [(d.get("name") or "").strip()] + d.get("aliases", [])
            pos = -1
            mlen = 0
            for n in names:
                _n = (n or "").strip()
                if len(_n) < 2:
                    continue
                idx = _t.find(_n.lower())
                if idx >= 0 and (pos < 0 or idx < pos):
                    pos, mlen = idx, len(_n)
            if pos >= 0:
                d["_match_len"] = mlen
                hits.append((pos, -mlen, d))
        hits.sort(key=lambda x: (x[0], x[1]))
        return [d for _p, _l, d in hits]

    # ------------------------------------------------------------------ #
    # 锚点 CRUD
    # ------------------------------------------------------------------ #
    def list_anchors(self, char_id) -> list[dict]:
        cid = int(char_id) if str(char_id).strip().isdigit() else None
        if cid is None:
            ch = self.get_character(char_id)
            if ch is None:
                return []
            cid = int(ch["id"])
        rows = self._conn_get().execute(
            "SELECT * FROM character_anchors WHERE character_id=? "
            "ORDER BY sort_order, id",
            (cid,),
        ).fetchall()
        return [self._row_to_anchor(r) for r in rows]

    def add_anchor(
        self,
        char_id,
        name: str,
        positive: str,
        kind: str = "full",
        negative: str = "",
        weight: float = 1.2,
        lora_name: str = "",
        skip_trigger_words: bool = True,
        note: str = "",
        created_by: str = "",
        source: str = "user",
    ) -> dict | None:
        """新增锚点。角色的第一个锚点自动成为主锚点。

        `created_by` / `source`（v6.3.0）：谁在哪个渠道建的（webui / command / llm / import），
        详情页要按「服装/形象提示词」逐条展示创建人与创建方式。
        """
        ch = self.get_character(char_id)
        if ch is None:
            return None
        _name = (name or "").strip() or "默认装"
        _pos = (positive or "").strip()
        if not _pos:
            raise ValueError("锚点标签（positive）不能为空")
        _kind = (kind or "full").strip() if (kind or "").strip() in ANCHOR_KINDS else "full"
        now = time.time()
        conn = self._conn_get()
        cur = conn.execute(
            """INSERT INTO character_anchors
               (character_id, name, kind, positive, negative, weight, lora_name,
                skip_trigger_words, note, sort_order, created_at, updated_at,
                created_by, source)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                int(ch["id"]), _name, _kind, _pos, (negative or "").strip(),
                float(weight or 1.2), (lora_name or "").strip(),
                1 if skip_trigger_words else 0, (note or "").strip(),
                self._next_sort_order(int(ch["id"])), now, now,
                (created_by or "").strip(), (source or "user").strip(),
            ),
        )
        aid = int(cur.lastrowid)
        if not int(ch.get("primary_anchor_id") or 0):
            conn.execute(
                "UPDATE characters SET primary_anchor_id=?, updated_at=? WHERE id=?",
                (aid, now, int(ch["id"])),
            )
        conn.commit()
        logger.info(
            f"【角色卡】 角色「{ch['name']}」新增锚点「{_name}」（id={aid}，{len(_pos)} 字）"
        )
        return self.get_anchor(int(ch["id"]), aid)

    def get_anchor_by_id(self, anchor_id) -> dict | None:
        """按锚点 id 直接取锚点（编辑/删除路径用，不需要知道它属于哪个角色）。"""
        row = self._conn_get().execute(
            "SELECT * FROM character_anchors WHERE id=?", (int(anchor_id),)
        ).fetchone()
        return self._row_to_anchor(row)

    def update_anchor(self, anchor_id, **fields) -> dict | None:
        conn = self._conn_get()
        row = conn.execute(
            "SELECT * FROM character_anchors WHERE id=?", (int(anchor_id),)
        ).fetchone()
        if row is None:
            return None
        sets, vals = [], []
        for k, v in (fields or {}).items():
            if k not in _ANCHOR_FIELDS:
                continue
            if k == "skip_trigger_words":
                v = 1 if v else 0
            sets.append(f"{k}=?")
            vals.append(v)
        if not sets:
            return self._row_to_anchor(row)
        sets.append("updated_at=?")
        vals.append(time.time())
        vals.append(int(anchor_id))
        conn.execute(f"UPDATE character_anchors SET {', '.join(sets)} WHERE id=?", vals)
        conn.commit()
        return self.get_anchor(int(row["character_id"]), int(anchor_id))

    def delete_anchor(self, anchor_id) -> bool:
        conn = self._conn_get()
        row = conn.execute(
            "SELECT * FROM character_anchors WHERE id=?", (int(anchor_id),)
        ).fetchone()
        if row is None:
            return False
        cid = int(row["character_id"])
        conn.execute("DELETE FROM character_anchors WHERE id=?", (int(anchor_id),))
        # v6.1.0：锚点下的图片**不删**（用户素材不该因为删锚点就丢），改为降级为角色级
        try:
            _moved = conn.execute(
                "UPDATE character_refs SET anchor_id=0 WHERE anchor_id=?",
                (int(anchor_id),),
            ).rowcount
            if _moved:
                logger.info(f"【角色卡】 锚点 id={anchor_id} 的 {_moved} 张图已降级为角色级（未删除）")
        except Exception as e:
            logger.warning(f"【角色卡】 锚点图片降级失败（图片保留原归属）: {e}")
        # 主锚点被删 → 顺延到剩余第一个锚点
        ch = conn.execute("SELECT * FROM characters WHERE id=?", (cid,)).fetchone()
        if ch is not None and int(ch["primary_anchor_id"] or 0) == int(anchor_id):
            nxt = conn.execute(
                "SELECT id FROM character_anchors WHERE character_id=? ORDER BY sort_order, id LIMIT 1",
                (cid,),
            ).fetchone()
            conn.execute(
                "UPDATE characters SET primary_anchor_id=?, updated_at=? WHERE id=?",
                (int(nxt["id"]) if nxt else 0, time.time(), cid),
            )
        conn.commit()
        logger.info(f"【角色卡】 已删除锚点 id={anchor_id}")
        return True

    def set_primary_anchor(self, char_id, anchor_name_or_id=None) -> dict | None:
        """把某个锚点设为主锚点；不传则取该角色第一个锚点。"""
        ch = self.get_character(char_id)
        if ch is None:
            return None
        anchors = self.list_anchors(int(ch["id"]))
        if not anchors:
            return None
        target = None
        if anchor_name_or_id is None:
            target = anchors[0]
        else:
            _k = str(anchor_name_or_id).strip()
            if _k.isdigit():
                target = next((a for a in anchors if int(a["id"]) == int(_k)), None)
            if target is None:
                target = next(
                    (a for a in anchors if (a["name"] or "").strip().lower() == _k.lower()),
                    None,
                )
            if target is None:
                target = next(
                    (a for a in anchors if _k and _k.lower() in (a["name"] or "").lower()),
                    None,
                )
        if target is None:
            return None
        self.update_character(int(ch["id"]), primary_anchor_id=int(target["id"]))
        logger.info(f"【角色卡】 「{ch['name']}」主锚点 → 「{target['name']}」")
        return self.get_anchor(int(ch["id"]), int(target["id"]))

    def _next_sort_order(self, char_id) -> int:
        """下一个排序号（MAX+1）。

        旧实现用 `len(list_anchors())`，删掉一个锚点后再新增会与既有 sort_order
        撞号，`ORDER BY sort_order, id` 的次序不稳（锚点列表/主锚点顺延跟着抖）。
        """
        try:
            row = self._conn_get().execute(
                "SELECT COALESCE(MAX(sort_order), -1) AS m"
                " FROM character_anchors WHERE character_id=?",
                (int(char_id),),
            ).fetchone()
            return int(row["m"] or 0) + 1
        except Exception:
            return 0

    def find_anchor_exact(self, char_id, name: str) -> dict | None:
        """按**精确名**取锚点（去空格、大小写不敏感），**不做**子串回退。

        v6.0.0 新增：`add_anchor`（指令/工具）的「同名即更新」判定必须用精确比较——
        `get_anchor()` 末段有子串回退，用「泳」去判会被已有锚点「泳装」命中，
        于是「想新增一套」变成「偷偷改掉了别人的锚点」。
        """
        ch = self.get_character(char_id)
        if ch is None:
            return None
        _k = (name or "").strip().lower()
        if not _k:
            return None
        return next(
            (
                a for a in self.list_anchors(int(ch["id"]))
                if (a.get("name") or "").strip().lower() == _k
            ),
            None,
        )

    def get_anchor(self, char_id, anchor_name_or_id=None) -> dict | None:
        """取锚点：给定名字/ID 则取其，否则取主锚点（再退回第一个）。"""
        ch = self.get_character(char_id)
        if ch is None:
            return None
        anchors = self.list_anchors(int(ch["id"]))
        if not anchors:
            return None
        if anchor_name_or_id is None:
            _pid = int(ch.get("primary_anchor_id") or 0)
            return next(
                (a for a in anchors if int(a["id"]) == _pid), anchors[0]
            )
        _k = str(anchor_name_or_id).strip()
        if _k.isdigit():
            hit = next((a for a in anchors if int(a["id"]) == int(_k)), None)
            if hit:
                return hit
        hit = next(
            (a for a in anchors if (a["name"] or "").strip().lower() == _k.lower()), None
        )
        if hit:
            return hit
        return next(
            (a for a in anchors if _k and _k.lower() in (a["name"] or "").lower()), None
        )

    # ------------------------------------------------------------------ #
    # 参考图（M3：文件落地走内容寻址 data_dir/characters/<slug_id>/<sha16>.<ext>）
    # ------------------------------------------------------------------ #
    def characters_dir(self) -> Path:
        """角色资料根目录（参考图等文件落地处）。"""
        d = self.data_dir / "characters"
        d.mkdir(parents=True, exist_ok=True)
        return d

    def refs_dir(self, char_id) -> Path | None:
        """某角色的参考图目录：`data_dir/characters/<名安全化>_<id>/`。

        目录名带 id 后缀，避免同名/改名的角色互相串目录；角色不存在返回 None。
        """
        ch = self.get_character(char_id)
        if ch is None:
            return None
        slug = _slugify(ch.get("name") or "char")
        d = self.characters_dir() / f"{slug}_{int(ch['id'])}"
        d.mkdir(parents=True, exist_ok=True)
        return d

    def store_ref_bytes(
        self,
        char_id,
        data: bytes,
        ext: str = ".png",
        url: str = "",
        note: str = "",
        nsfw_score: float = -1,
        anchor_id: int = 0,
        created_by: str = "",
        origin: str = REF_ORIGIN_UPLOAD,
    ) -> dict | None:
        """把图片字节落盘到角色参考图目录并落库（内容寻址，同角色同图去重）。

        文件名用 `sha256[:16] + ext`：同图重复上传只占一份空间、直接返回已有记录。
        `anchor_id`（v6.1.0）：把这张图挂到某个**锚点**下（0 = 角色级，不绑定锚点）。
        同一张图已存在但本次指定了不同锚点时，**只更新归属**而不新建记录。
        `created_by` / `origin`（v6.3.0）：谁传的吗、从哪个渠道来的，供详情页展示。
        """
        ch = self.get_character(char_id)
        if ch is None:
            return None
        if not data or len(data) < 16:
            raise ValueError("图片数据为空或过小")
        if len(data) > _MAX_REF_BYTES:
            # 统一上限（指令路径此前无限制，可把超大图整张读进内存并落盘）
            raise ValueError(
                f"参考图过大（{len(data) // 1024 // 1024} MB > {_MAX_REF_BYTES // 1024 // 1024} MB），请压缩后再存"
            )
        _ext = (ext or ".png").strip().lower()
        if not _ext.startswith("."):
            _ext = "." + _ext
        if _ext not in (".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp"):
            _ext = ".png"
        import hashlib as _hl

        sha = _hl.sha256(data).hexdigest()
        conn = self._conn_get()
        exist = conn.execute(
            "SELECT * FROM character_refs WHERE character_id=? AND sha256=?",
            (int(ch["id"]), sha),
        ).fetchone()
        if exist is not None:
            logger.info(f"【角色卡】 参考图已存在（同 sha），复用记录 id={exist['id']}")
            d = self._row_to_ref(exist)
            d["dedup"] = True
            # 本次显式指定了锚点且与原归属不同 → 只挪归属（不重复占盘）
            _want = int(anchor_id or 0)
            if _want and _want != int(exist["anchor_id"] or 0):
                try:
                    self.set_ref_anchor(int(exist["id"]), _want)
                    d["anchor_id"] = _want
                except Exception as e:
                    logger.warning(f"【角色卡】 参考图改归属失败（保持原样）: {e}")
            return d
        d_dir = self.refs_dir(int(ch["id"]))
        out = d_dir / f"{sha[:16]}{_ext}"
        try:
            if not out.exists():
                out.write_bytes(data)
        except Exception as e:
            logger.warning(f"【角色卡】 参考图落盘失败: {e}")
            raise
        cur = conn.execute(
            """INSERT INTO character_refs
               (character_id, anchor_id, path, url, sha256, nsfw_score, note, created_at,
                created_by, origin, external)
               VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (int(ch["id"]), int(anchor_id or 0), str(out), (url or "").strip(), sha,
             float(nsfw_score), (note or "").strip(), time.time(),
             (created_by or "").strip(), (origin or REF_ORIGIN_UPLOAD).strip(), 0),
        )
        conn.commit()
        logger.info(
            f"【角色卡】 角色「{ch['name']}」新增参考图 id={cur.lastrowid} "
            f"（{len(data) // 1024} KB → {out.name}）"
        )
        row = conn.execute(
            "SELECT * FROM character_refs WHERE id=?", (int(cur.lastrowid),)
        ).fetchone()
        d = self._row_to_ref(row)
        d["dedup"] = False
        return d

    def store_ref_from_path(
        self, char_id, src_path, url: str = "", note: str = "",
        nsfw_score: float = -1, anchor_id: int = 0,
        created_by: str = "", origin: str = REF_ORIGIN_UPLOAD,
    ) -> dict | None:
        """从本地已有文件落地一张参考图（复制进角色目录）。"""
        p = Path(str(src_path))
        if not p.exists() or not p.is_file():
            raise ValueError(f"文件不存在: {src_path}")
        return self.store_ref_bytes(
            char_id, p.read_bytes(), ext=p.suffix or ".png",
            url=url, note=note, nsfw_score=nsfw_score, anchor_id=anchor_id,
            created_by=created_by, origin=origin,
        )

    @staticmethod
    def file_sha256(file_path) -> str:
        """分块算文件 sha（大图别整张读进内存）。失败返回空串＝调用方按「未知 sha」处理。"""
        import hashlib as _hl

        try:
            _h = _hl.sha256()
            with open(str(file_path), "rb") as f:
                for _chunk in iter(lambda: f.read(1024 * 1024), b""):
                    _h.update(_chunk)
            return _h.hexdigest()
        except Exception as e:
            logger.warning(f"【角色卡】 计算文件 sha 失败 {file_path}: {e}")
            return ""

    def store_ref_link(
        self, char_id, file_path, sha256: str = "", url: str = "", note: str = "",
        nsfw_score: float = -1, anchor_id: int = 0,
        created_by: str = "", origin: str = REF_ORIGIN_AUTO,
    ) -> dict | None:
        """**引用**磁盘上已有的图片作为参考图（不复制、不占第二份空间）。

        用于「出图命中锚点 → 自动把 gallery/ 里的成品图挂到该锚点下」：图库本身
        就是内容寻址存储，再抄一份进 characters/<名>_<id>/ 纯属浪费。代价是这条记录
        依赖外部文件，所以标 `external=1`，`delete_ref` 只删记录**不删文件**。
        去重仍按 sha256：同一张图重复关联不会堆记录。
        """
        ch = self.get_character(char_id)
        if ch is None:
            return None
        p = Path(str(file_path))
        if not p.exists() or not p.is_file():
            raise ValueError(f"文件不存在: {file_path}")
        _sha = (sha256 or "").strip() or self.file_sha256(p)
        if not _sha:
            raise ValueError("无法计算图片 sha，拒绝引用落地")
        conn = self._conn_get()
        exist = conn.execute(
            "SELECT * FROM character_refs WHERE character_id=? AND sha256=?",
            (int(ch["id"]), _sha),
        ).fetchone()
        if exist is not None:
            d = self._row_to_ref(exist)
            d["dedup"] = True
            _want = int(anchor_id or 0)
            if _want and _want != int(exist["anchor_id"] or 0):
                try:
                    self.set_ref_anchor(int(exist["id"]), _want)
                    d["anchor_id"] = _want
                except Exception as e:
                    logger.warning(f"【角色卡】 关联图改归属失败（保持原样）: {e}")
            return d
        cur = conn.execute(
            """INSERT INTO character_refs
               (character_id, anchor_id, path, url, sha256, nsfw_score, note, created_at,
                created_by, origin, external)
               VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (int(ch["id"]), int(anchor_id or 0), str(p), (url or "").strip(), _sha,
             float(nsfw_score), (note or "").strip(), time.time(),
             (created_by or "").strip(), (origin or REF_ORIGIN_AUTO).strip(), 1),
        )
        conn.commit()
        logger.info(
            f"【角色卡】 角色「{ch['name']}」关联图片 id={cur.lastrowid}"
            f"（来源={origin}，锚点={int(anchor_id or 0)}，引用 {p.name}）"
        )
        row = conn.execute(
            "SELECT * FROM character_refs WHERE id=?", (int(cur.lastrowid),)
        ).fetchone()
        d = self._row_to_ref(row)
        d["dedup"] = False
        return d

    def trim_auto_refs(self, char_id, anchor_id: int = 0, keep: int = 6) -> int:
        """把某锚点下「自动关联」的图裁到最近 `keep` 张（只删记录，外部文件不动）。

        只裁 `origin='auto'`：手工上传/指令录入的图是用户资产，永不自动清理。
        `keep<=0` = 不限。返回删除的记录数。
        """
        if int(keep or 0) <= 0:
            return 0
        ch = self.get_character(char_id)
        if ch is None:
            return 0
        rows = self._conn_get().execute(
            "SELECT id FROM character_refs"
            " WHERE character_id=? AND anchor_id=? AND origin=? ORDER BY id DESC",
            (int(ch["id"]), int(anchor_id or 0), REF_ORIGIN_AUTO),
        ).fetchall()
        drop = [int(r["id"]) for r in rows[int(keep):]]
        for _rid in drop:
            self.delete_ref(_rid)
        if drop:
            logger.info(
                f"【角色卡】 「{ch['name']}」锚点 {anchor_id} 自动关联图超出上限，"
                f"清理 {len(drop)} 条记录（保留最近 {keep} 张）"
            )
        return len(drop)

    def set_ref_anchor(self, ref_id, anchor_id: int = 0) -> dict | None:
        """把一张图挪到某个锚点下（0 = 角色级）。锚点必须属于同一角色，否则忽略归属改为 0。"""
        ref = self.get_ref(ref_id)
        if ref is None:
            return None
        _aid = int(anchor_id or 0)
        if _aid:
            a = self.get_anchor(int(ref["character_id"]), _aid)
            if a is None:
                logger.warning(f"【角色卡】 锚点 id={_aid} 不属于该角色，图片改为角色级")
                _aid = 0
        conn = self._conn_get()
        conn.execute(
            "UPDATE character_refs SET anchor_id=? WHERE id=?", (_aid, int(ref_id))
        )
        conn.commit()
        return self.get_ref(ref_id)

    # ------------------------------------------------------------------ #
    # 角色封面（v6.1.0）
    # ------------------------------------------------------------------ #
    def set_cover_ref(self, char_id, ref_id) -> dict | None:
        """把某张参考图设为角色封面；`ref_id=0` 等同清除（回落为第一张图）。"""
        ch = self.get_character(char_id)
        if ch is None:
            return None
        _rid = int(ref_id or 0)
        if _rid:
            ref = self.get_ref(_rid)
            if ref is None or int(ref["character_id"]) != int(ch["id"]):
                raise ValueError("该图片不属于此角色")
        self.update_character(int(ch["id"]), cover_ref_id=_rid)
        return self.get_character(int(ch["id"]))

    def get_cover_ref(self, char_id) -> dict | None:
        """取角色封面图。

        优先用显式设置的 `cover_ref_id`；未设置或该图已被删 → 回落到**第一张**参考图
        （锚点图也算，按 id 顺序），都没有则返回 None。
        """
        ch = self.get_character(char_id)
        if ch is None:
            return None
        _cid = int(ch["id"])
        _rid = int(ch.get("cover_ref_id") or 0)
        if _rid:
            ref = self.get_ref(_rid)
            if ref is not None and int(ref["character_id"]) == _cid:
                ref["is_cover"] = True
                return ref
        refs = self.list_refs(_cid)
        if not refs:
            return None
        d = refs[0]
        d["is_cover"] = False
        return d

    def clear_cover_ref(self, char_id) -> dict | None:
        return self.set_cover_ref(char_id, 0)

    def get_ref(self, ref_id) -> dict | None:
        row = self._conn_get().execute(
            "SELECT * FROM character_refs WHERE id=?", (int(ref_id),)
        ).fetchone()
        return self._row_to_ref(row)

    def update_ref_nsfw(self, ref_id, score: float) -> None:
        """回写 NSFW 置信度（落地时先落库、检测是异步的，故分开写）。"""
        conn = self._conn_get()
        conn.execute(
            "UPDATE character_refs SET nsfw_score=? WHERE id=?", (float(score), int(ref_id))
        )
        conn.commit()

    def list_refs(self, char_id, anchor_id=None) -> list[dict]:
        """列出参考图。

        `anchor_id=None` → 该角色全部图片；`anchor_id=0` → 只列角色级（未绑定锚点的）；
        `anchor_id=N` → 只列挂在锚点 N 下的。每项带 `exists`（文件是否还在）与
        `is_cover`（是否当前封面），前端可直接用。
        """
        ch = self.get_character(char_id)
        if ch is None:
            return []
        _cid = int(ch["id"])
        sql = "SELECT * FROM character_refs WHERE character_id=?"
        args: list = [_cid]
        if anchor_id is not None:
            sql += " AND anchor_id=?"
            args.append(int(anchor_id or 0))
        sql += " ORDER BY id"
        rows = self._conn_get().execute(sql, args).fetchall()
        _cover = int(ch.get("cover_ref_id") or 0)
        _first_id = 0
        out = []
        for r in rows:
            d = self._row_to_ref(r)
            # 文件被外部删掉时标记出来，前端可提示（不自动清库，便于排查）
            d["exists"] = bool(d.get("path") and Path(d["path"]).exists())
            if not _first_id:
                _first_id = int(d["id"])
            out.append(d)
        # 封面标记：显式设置的封面优先；未设置则第一张图视为「自动封面」
        if _cover:
            _hit = False
            for d in out:
                if int(d["id"]) == _cover:
                    d["is_cover"] = True
                    _hit = True
            if not _hit and _first_id:
                for d in out:
                    if int(d["id"]) == _first_id:
                        d["is_cover_auto"] = True
        elif _first_id:
            for d in out:
                if int(d["id"]) == _first_id:
                    d["is_cover_auto"] = True
        return out

    def delete_ref(self, ref_id, delete_file: bool = True) -> bool:
        """删除参考图记录；文件仅当没有其它记录引用同一路径时才删（内容寻址可能共享）。

        若删的正是该角色的封面（`cover_ref_id`），一并清空封面设置（回落到自动取第一张）。
        `external` 记录（引用 gallery 里的成品图）**永不删文件**——那是图库的资产，
        可能还被图库页面、其他角色的引用共享。
        """
        row = self.get_ref(ref_id)
        if row is None:
            return False
        conn = self._conn_get()
        conn.execute("DELETE FROM character_refs WHERE id=?", (int(ref_id),))
        # v6.1.0：封面被删 → 清空 cover_ref_id，否则会留下悬挂引用
        try:
            conn.execute(
                "UPDATE characters SET cover_ref_id=0, updated_at=? WHERE id=? AND cover_ref_id=?",
                (time.time(), int(row["character_id"]), int(ref_id)),
            )
        except Exception as e:
            logger.warning(f"【角色卡】 清理封面引用失败: {e}")
        conn.commit()
        if delete_file and row.get("path") and not int(row.get("external") or 0):
            still = conn.execute(
                "SELECT COUNT(*) AS c FROM character_refs WHERE path=?", (row["path"],)
            ).fetchone()["c"]
            if not still:
                try:
                    Path(row["path"]).unlink(missing_ok=True)
                except Exception as e:
                    logger.warning(f"【角色卡】 参考图文件删除失败（记录已删）: {e}")
        return True

    # ------------------------------------------------------------------ #
    # 导出 / 导入（备份迁移用）
    # ------------------------------------------------------------------ #
    def export_all(self) -> dict:
        chars = self.list_characters()
        for c in chars:
            c["anchors"] = self.list_anchors(int(c["id"]))
            c["refs"] = self.list_refs(int(c["id"]))
            # v6.0.0：补导主锚点**名**。import_all 想用名字恢复主锚点，
            # 而旧版只导出 id（导入后 id 全变），导致该恢复逻辑实际是死代码。
            _pid = int(c.get("primary_anchor_id") or 0)
            c["primary_anchor_name"] = next(
                (a["name"] for a in c["anchors"] if int(a["id"]) == _pid), ""
            )
        return {"version": 1, "characters": chars}

    def import_all(self, data: dict) -> int:
        """导入 export_all 的数据，返回导入（新建或合并）的角色数。"""
        n = 0
        for c in ((data or {}).get("characters") or []):
            if not isinstance(c, dict) or not (c.get("name") or "").strip():
                continue
            ch = self.create_character(
                c["name"], aliases=c.get("aliases"),
                persona_name=c.get("persona_name") or "",
                work=c.get("work") or "", lora_name=c.get("lora_name") or "",
                source=c.get("source") or "import", note=c.get("note") or "",
                created_by=c.get("created_by") or "导入",
            )
            for a in (c.get("anchors") or []):
                if not isinstance(a, dict) or not (a.get("positive") or "").strip():
                    continue
                if any(
                    (x["name"] or "") == (a.get("name") or "默认装")
                    for x in self.list_anchors(int(ch["id"]))
                ):
                    continue
                self.add_anchor(
                    int(ch["id"]), a.get("name") or "默认装", a["positive"],
                    kind=a.get("kind") or "full", negative=a.get("negative") or "",
                    weight=a.get("weight") or 1.2, lora_name=a.get("lora_name") or "",
                    skip_trigger_words=bool(a.get("skip_trigger_words", True)),
                    note=a.get("note") or "",
                    created_by=a.get("created_by") or "导入",
                    source=a.get("source") or "import",
                )
            # 主锚点恢复（v6.0.0 修正）：
            #   ① 优先按导出的 primary_anchor_name 精确匹配（新版导出会带该字段）；
            #   ② 旧导出没有该字段 → 用「主锚点在导出锚点列表里的下标」映射到导入后的
            #      同序位锚点（导入保持顺序），旧实现用从未导出的名字匹配，等于永远不会恢复。
            _want = None
            _pname = str(c.get("primary_anchor_name") or "").strip()
            if _pname:
                _want = self.find_anchor_exact(int(ch["id"]), _pname)
            elif c.get("primary_anchor_id"):
                _src = c.get("anchors") or []
                _idx = next(
                    (
                        i for i, a in enumerate(_src)
                        if int(a.get("id") or 0) == int(c["primary_anchor_id"])
                    ),
                    -1,
                )
                _dst = self.list_anchors(int(ch["id"]))
                if 0 <= _idx < len(_dst):
                    _want = _dst[_idx]
            if _want is not None:
                self.set_primary_anchor(int(ch["id"]), int(_want["id"]))
            # 参考图（v6.0.0 新增）：同机迁移时按导出路径复制文件入角色目录；
            # 跨机/文件缺失则跳过并计数（文件本身不在导出里，无法凭空恢复）。
            # v6.1.0：图片的**锚点归属**按「锚点名」映射还原（导出 id 在导入后已变），
            # 封面按 sha256 匹配还原。
            _amap: "dict[int, int]" = {}
            for _sa in (c.get("anchors") or []):
                if not isinstance(_sa, dict):
                    continue
                try:
                    _old = int(_sa.get("id") or 0)
                except Exception:
                    _old = 0
                _nm = (_sa.get("name") or "").strip()
                if not _old or not _nm:
                    continue
                _hit_a = self.find_anchor_exact(int(ch["id"]), _nm)
                if _hit_a is not None:
                    _amap[_old] = int(_hit_a["id"])
            _ref_ok = 0
            _ref_skip = 0
            for r in (c.get("refs") or []):
                if not isinstance(r, dict):
                    continue
                _srcp = str(r.get("path") or "").strip()
                if _srcp and Path(_srcp).exists():
                    try:
                        _old_a = int(r.get("anchor_id") or 0)
                        # 导入是**复制文件**，落地后归本卡所有；原来的 auto 渠道要降级为
                        # import，否则 auto_link_keep 的上限清理会去删用户导入的副本。
                        _org = (r.get("origin") or "upload").strip()
                        self.store_ref_from_path(
                            int(ch["id"]), _srcp,
                            url=r.get("url") or "", note=r.get("note") or "",
                            nsfw_score=float(r.get("nsfw_score") or -1),
                            anchor_id=_amap.get(_old_a, 0),
                            created_by=r.get("created_by") or "导入",
                            origin="import" if _org == REF_ORIGIN_AUTO else _org,
                        )
                        _ref_ok += 1
                    except Exception as e:
                        _ref_skip += 1
                        logger.warning(f"【角色卡】 导入参考图失败（跳过）{_srcp}: {e}")
                else:
                    _ref_skip += 1
            if _ref_ok or _ref_skip:
                logger.info(
                    f"【角色卡】 「{ch['name']}」导入参考图：成功 {_ref_ok} 张"
                    f"，跳过 {_ref_skip} 张（文件不存在，需手动复制 data_dir/characters/）"
                )
            # 封面还原：导出的 cover_ref_id 是旧库 id → 用它的 sha256 找导入后的同图
            try:
                _cover_old = int(c.get("cover_ref_id") or 0)
            except Exception:
                _cover_old = 0
            if _cover_old:
                _src_cover = next(
                    (
                        r for r in (c.get("refs") or [])
                        if isinstance(r, dict) and int(r.get("id") or 0) == _cover_old
                    ),
                    None,
                )
                _sha_c = (_src_cover or {}).get("sha256") or ""
                if _sha_c:
                    _hit_ref = next(
                        (x for x in self.list_refs(int(ch["id"])) if x.get("sha256") == _sha_c),
                        None,
                    )
                    if _hit_ref is not None:
                        try:
                            self.set_cover_ref(int(ch["id"]), int(_hit_ref["id"]))
                        except Exception as e:
                            logger.warning(f"【角色卡】 导入封面还原失败: {e}")
            n += 1
        return n

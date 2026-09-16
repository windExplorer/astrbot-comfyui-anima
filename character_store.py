"""角色卡片（Character Card）存储层。

独立的 SQLite（data_dir/character.db）维护三类数据：

- ``characters``        角色卡：角色名 / 别名 / 绑定人格 / 作品 / 关联 LoRA / 主锚点 …
- ``character_anchors`` 锚点：一个角色可有多套「提示词标签组」（默认装 / 泳装 / 校服…），
  每套含正标签串、负标签串、建议权重、可选覆盖 LoRA、是否抑制全局触发词
- ``character_refs``    参考图（0~N 张）：本地路径 / 来源 URL / sha256 / NSFW 打标

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
import sqlite3
import time
from pathlib import Path

logger = logging.getLogger("astrbot_plugin_comfyui_anima.character")

# 角色卡可写字段白名单（update 用，防注入任意列名）
_CHAR_FIELDS = {
    "name", "aliases", "persona_name", "work", "lora_name",
    "primary_anchor_id", "source", "note", "enabled",
}
_ANCHOR_FIELDS = {
    "name", "kind", "positive", "negative", "weight",
    "lora_name", "skip_trigger_words", "note", "sort_order",
}

# 锚点种类：appearance=只写外观；outfit=服装/造型；full=外观+服装（默认，最常用）
ANCHOR_KINDS = ("full", "appearance", "outfit")


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


def _dump_list(items) -> str:
    return json.dumps(
        [str(x).strip() for x in (items or []) if str(x).strip()],
        ensure_ascii=False,
    )


class CharacterStore:
    """角色卡片存储。单线程事件循环使用，线程不安全但足够。"""

    def __init__(self, data_dir: Path) -> None:
        self.data_dir = Path(data_dir)
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
                path         TEXT NOT NULL DEFAULT '',
                url          TEXT DEFAULT '',
                sha256       TEXT DEFAULT '',
                nsfw_score   REAL DEFAULT -1,
                note         TEXT DEFAULT '',
                created_at   REAL NOT NULL DEFAULT 0
            )"""
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
    ) -> dict:
        """新建角色卡。同名（大小写不敏感）或命中已有别名时**返回已有卡**并补齐传入字段。"""
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
            if upd:
                self.update_character(int(exist["id"]), **upd)
            return self.get_character(int(exist["id"]))
        now = time.time()
        conn = self._conn_get()
        cur = conn.execute(
            """INSERT INTO characters
               (name, aliases, persona_name, work, lora_name, primary_anchor_id,
                source, note, enabled, created_at, updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (
                _name, _dump_list(aliases), (persona_name or "").strip(),
                (work or "").strip(), (lora_name or "").strip(), 0,
                (source or "user").strip(), (note or "").strip(),
                1 if enabled else 0, now, now,
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
        """删除角色卡（连同其锚点与参考图记录）。"""
        row = self._find_char_row(char_id)
        if row is None:
            return False
        conn = self._conn_get()
        cid = int(row["id"])
        conn.execute("DELETE FROM character_anchors WHERE character_id=?", (cid,))
        conn.execute("DELETE FROM character_refs WHERE character_id=?", (cid,))
        conn.execute("DELETE FROM characters WHERE id=?", (cid,))
        conn.commit()
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
    ) -> dict | None:
        """新增锚点。角色的第一个锚点自动成为主锚点。"""
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
                skip_trigger_words, note, sort_order, created_at, updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                int(ch["id"]), _name, _kind, _pos, (negative or "").strip(),
                float(weight or 1.2), (lora_name or "").strip(),
                1 if skip_trigger_words else 0, (note or "").strip(),
                len(self.list_anchors(int(ch["id"]))), now, now,
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
    # 参考图（阶段三会用到；M1 先打通读写）
    # ------------------------------------------------------------------ #
    def add_ref(
        self, char_id, path: str = "", url: str = "",
        sha256: str = "", nsfw_score: float = -1, note: str = "",
    ) -> int | None:
        ch = self.get_character(char_id)
        if ch is None:
            return None
        conn = self._conn_get()
        cur = conn.execute(
            """INSERT INTO character_refs
               (character_id, path, url, sha256, nsfw_score, note, created_at)
               VALUES (?,?,?,?,?,?,?)""",
            (int(ch["id"]), path or "", url or "", sha256 or "",
             float(nsfw_score), note or "", time.time()),
        )
        conn.commit()
        return int(cur.lastrowid)

    def list_refs(self, char_id) -> list[dict]:
        ch = self.get_character(char_id)
        if ch is None:
            return []
        rows = self._conn_get().execute(
            "SELECT * FROM character_refs WHERE character_id=? ORDER BY id",
            (int(ch["id"]),),
        ).fetchall()
        return [dict(r) for r in rows]

    def delete_ref(self, ref_id) -> bool:
        conn = self._conn_get()
        cur = conn.execute("DELETE FROM character_refs WHERE id=?", (int(ref_id),))
        conn.commit()
        return bool(cur.rowcount)

    # ------------------------------------------------------------------ #
    # 导出 / 导入（备份迁移用）
    # ------------------------------------------------------------------ #
    def export_all(self) -> dict:
        chars = self.list_characters()
        for c in chars:
            c["anchors"] = self.list_anchors(int(c["id"]))
            c["refs"] = self.list_refs(int(c["id"]))
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
                )
            if c.get("primary_anchor_id"):
                for a in self.list_anchors(int(ch["id"])):
                    if (a["name"] or "") == str(c.get("primary_anchor_name") or ""):
                        self.set_primary_anchor(int(ch["id"]), int(a["id"]))
            n += 1
        return n

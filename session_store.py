"""会话维度配置 / 限额 / 统计存储（v7.11.0）。

设计口径（与「会话管理」WebUI 页一一对应）：
- 一个会话以 AstrBot 的 unified_msg_origin（UMO）为主键，形如
  ``aiocqhttp:GroupMessage:123456`` / ``aiocqhttp:FriendMessage:123456``。
- 每行既承载「已知会话底表」（名字 / 群私 / 最近活跃），也承载会话级覆盖配置
  （固定工作流 / 限额 / NSFW / 信息卡片 / 启停 / 备注）。覆盖字段为 NULL 表示
  「跟随全局」，不覆盖任何现有行为；未配置过的会话在出图链路里完全无感。
- 会话限额与现有 quota_store（用户维度）**并存**：用户限额照旧计数、照旧校验；
  会话限额是额外一层，只对显式配置了 max_* 的会话生效，计数记在本模块的
  session_usage 表（主键 origin），不污染用户限额数据。
- 统计不另建表：直接读 image_store 的 gallery.db 与 oplog_store 的 oplog.db 聚合，
  避免与图库/操作日志口径漂移。

所有方法对异常尽量降级（返回空值 / False），不让「会话设置」拖垮出图主链路。
"""
from __future__ import annotations

import base64
import re
import sqlite3
import threading
import time
from pathlib import Path

try:
    from astrbot.api import logger
except ImportError:  # 独立测试环境降级
    import logging

    logger = logging.getLogger("anima.session_store")


# 可写配置字段白名单（WebUI 传进来的键必须先过这一关，避免 SQL 注入 / 误写）
CONFIG_FIELDS = (
    "wf_t2i", "wf_i2i",
    "pin_t2i", "pin_i2i",
    "max_total", "max_hour", "max_day",
    "nsfw", "card", "enabled",
    "note",
)
INT_FIELDS = ("pin_t2i", "pin_i2i", "max_total", "max_hour", "max_day", "nsfw", "card", "enabled")
STR_FIELDS = ("wf_t2i", "wf_i2i", "note")


def parse_origin(origin: str) -> dict:
    """把 UMO 解析为展示/过滤字段。

    UMO 格式 ``platform:MessageType:session_id``；第 2 段以 group 开头判群聊。
    私聊的 session_id 即对方账号，群聊的 session_id 即群号。
    """
    parts = (origin or "").split(":", 2)
    platform = (parts[0] if len(parts) > 0 else "").strip()
    msg_type = (parts[1] if len(parts) > 1 else "").strip()
    sid = (parts[2] if len(parts) > 2 else "").strip()
    is_group = msg_type.lower().startswith("group")
    return {
        "platform": platform,
        "msg_type": msg_type,
        "session_id": sid,
        "is_group": is_group,
        "group_id": sid if is_group else "",
        "user_id": "" if is_group else sid,
    }


def normalize_origin(origin: str, *, group_id: str = "", platform: str = "aiocqhttp") -> str:
    """把各种来源的会话键统一成完整 UMO ``platform:MessageType:id``。

    v7.11.3：AstrBot 的 ``event.session_id`` 是**纯 ID**（如 ``123456``），
    而 ``event.unified_msg_origin`` 才是完整 UMO。联系人同步写的是 UMO、图库/
    oplog/出图登记此前写的是纯 ID，三套键并存导致同一好友登记两遍、群被判成
    私聊。这里统一归一到 UMO：

    - 已经是 UMO（含 ``:`` 且第二段是消息类型）→ 原样返回
    - 插件伪会话 ``plugin:<source>`` → 原样返回
    - 纯 ID + group_id 非空 → ``<platform>:GroupMessage:<id>``（群）
    - 纯 ID + 无 group_id → ``<platform>:FriendMessage:<id>``（私聊）
    """
    s = str(origin or "").strip()
    if not s:
        return ""
    if s.startswith("plugin:"):
        return s
    # 完整 UMO：platform:MessageType:ID（第二段通常以 Message 结尾）
    parts = s.split(":", 2)
    if len(parts) >= 3 and parts[1].strip().lower().endswith("message"):
        return s
    gid = str(group_id or "").strip()
    if gid and gid not in ("0", "None"):
        return f"{platform}:GroupMessage:{gid}"
    return f"{platform}:FriendMessage:{s}"


def _hour_start(ts: float) -> int:
    return int(ts // 3600 * 3600)


def _day_start(ts: float) -> int:
    lt = time.localtime(ts)
    return int(time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday, 0, 0, 0, 0, 0, -1)))


class SessionStore:
    """会话配置 + 会话限额计数 + 历史统计聚合。"""

    def __init__(self, data_dir: Path) -> None:
        self.data_dir = Path(data_dir)
        self.db_path = self.data_dir / "sessions.db"
        self._local = threading.local()
        self._init_db()

    # ------------------------------------------------------------------ #
    # 连接 / 建表
    # ------------------------------------------------------------------ #
    def _conn(self) -> sqlite3.Connection:
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = sqlite3.connect(str(self.db_path), timeout=8.0, check_same_thread=False)
            conn.row_factory = sqlite3.Row
            try:
                conn.execute("PRAGMA journal_mode=WAL")
            except Exception:
                pass
            self._local.conn = conn
        return conn

    def _init_db(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        conn = self._conn()
        conn.execute(
            """CREATE TABLE IF NOT EXISTS sessions (
                origin      TEXT PRIMARY KEY,
                name        TEXT NOT NULL DEFAULT '',
                is_group    INTEGER NOT NULL DEFAULT 0,
                group_id    TEXT NOT NULL DEFAULT '',
                user_id     TEXT NOT NULL DEFAULT '',
                last_active REAL NOT NULL DEFAULT 0,
                updated_at  REAL NOT NULL DEFAULT 0,
                wf_t2i      TEXT NOT NULL DEFAULT '',
                wf_i2i      TEXT NOT NULL DEFAULT '',
                pin_t2i     INTEGER NOT NULL DEFAULT 0,
                pin_i2i     INTEGER NOT NULL DEFAULT 0,
                max_total   INTEGER DEFAULT NULL,
                max_hour    INTEGER DEFAULT NULL,
                max_day     INTEGER DEFAULT NULL,
                nsfw        INTEGER DEFAULT NULL,
                card        INTEGER DEFAULT NULL,
                enabled     INTEGER DEFAULT NULL,
                note        TEXT NOT NULL DEFAULT ''
            )"""
        )
        conn.execute(
            """CREATE TABLE IF NOT EXISTS session_usage (
                origin     TEXT PRIMARY KEY,
                total_used INTEGER NOT NULL DEFAULT 0,
                hour_used  INTEGER NOT NULL DEFAULT 0,
                hour_start INTEGER NOT NULL DEFAULT 0,
                day_used   INTEGER NOT NULL DEFAULT 0,
                day_start  INTEGER NOT NULL DEFAULT 0,
                updated_at REAL NOT NULL DEFAULT 0
            )"""
        )
        conn.execute(
            """CREATE TABLE IF NOT EXISTS session_meta (
                key   TEXT PRIMARY KEY,
                value TEXT NOT NULL DEFAULT ''
            )"""
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_sessions_active ON sessions(last_active)")
        conn.commit()

    def close(self) -> None:
        """关闭当前线程的 SQLite 连接（测试 / 卸载时用；Windows 上文件占用会导致删库失败）。"""
        conn = getattr(self._local, "conn", None)
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass
            self._local.conn = None

    # ------------------------------------------------------------------ #
    # 元信息（联系人同步时间等）
    # ------------------------------------------------------------------ #
    def get_meta(self, key: str, default=None):
        try:
            row = self._conn().execute("SELECT value FROM session_meta WHERE key=?", (key,)).fetchone()
            if not row:
                return default
            import json
            return json.loads(row["value"] or "null")
        except Exception:
            return default

    def set_meta(self, key: str, value) -> None:
        try:
            import json
            conn = self._conn()
            conn.execute(
                "INSERT INTO session_meta (key,value) VALUES (?,?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, json.dumps(value, ensure_ascii=False)),
            )
            conn.commit()
        except Exception as e:
            logger.debug(f"[会话] set_meta 失败: {e}")

    def known_group_ids(self) -> set[str]:
        """已确认的群号集合（v7.11.7）：联系人快照 ∪ 会话表里已登记的群。

        外部插件（星语等）调生图工具时传入的合成 event 往往只有 session_id /
        unified_msg_origin，没有可用的 ``get_group_id()``。此时群号会被误当成
        「对方 QQ 号」，群聊登记成私聊、落进「非好友」标签。这里把群号暴露出来，
        供 UMO 归一与图库统计把这类记录掰回群聊。

        v7.11.7：此前只认联系人快照，用户没点「同步联系人」/ 非 aiocqhttp 平台 /
        同步失败时返回空集，v7.11.6 的修复完全失效（群仍被判私聊、群名回落触发者
        昵称）。现把 sessions 表里 ``is_group=1`` 的行也纳入——主链路正常出图写入
        的真群行同样能作为判群依据，去掉对单一快照的硬依赖。
        """
        ids: set[str] = set()
        try:
            meta = self.get_meta("contacts", {}) or {}
            ids |= {
                str(x.get("id") or "").strip()
                for x in (meta.get("group_items") or []) if isinstance(x, dict)
            }
        except Exception:
            pass
        try:
            for row in self._conn().execute("SELECT origin, group_id FROM sessions WHERE is_group=1"):
                gid = str(row["group_id"] or "").strip()
                if not gid:
                    parts = str(row["origin"] or "").strip().split(":", 2)
                    if len(parts) == 3 and parts[1].strip().lower().startswith("group"):
                        gid = parts[2].strip()
                if gid:
                    ids.add(gid)
        except Exception:
            pass
        ids.discard("")
        return ids

    def known_group_names(self) -> set[str]:
        """联系人快照里的群名集合（v7.11.7）：图库旧图 group_id 为空时的第三判据。"""
        try:
            meta = self.get_meta("contacts", {}) or {}
            return {
                str(x.get("name") or "").strip()
                for x in (meta.get("group_items") or []) if isinstance(x, dict) and str(x.get("name") or "").strip()
            }
        except Exception:
            return set()

    # ------------------------------------------------------------------ #
    # 基础 CRUD
    # ------------------------------------------------------------------ #
    def get(self, origin: str) -> dict | None:
        origin = (origin or "").strip()
        if not origin:
            return None
        try:
            row = self._conn().execute("SELECT * FROM sessions WHERE origin=?", (origin,)).fetchone()
            return dict(row) if row else None
        except Exception as e:
            logger.debug(f"[会话] get 失败: {e}")
            return None

    def list_all(self) -> list[dict]:
        try:
            rows = self._conn().execute("SELECT * FROM sessions").fetchall()
            return [dict(r) for r in rows]
        except Exception as e:
            logger.debug(f"[会话] list_all 失败: {e}")
            return []

    def upsert(
        self,
        origin: str,
        *,
        name: str = "",
        is_group: bool | None = None,
        group_id: str = "",
        user_id: str = "",
        last_active: float | None = None,
    ) -> None:
        """登记/更新会话底表字段；不触碰任何覆盖配置。"""
        origin = (origin or "").strip()
        if not origin:
            return
        parsed = parse_origin(origin)
        cur = self.get(origin) or {}
        now = time.time()
        vals = {
            "name": (name or cur.get("name") or "").strip(),
            "is_group": int(parsed["is_group"] if is_group is None else bool(is_group)),
            "group_id": (group_id or parsed["group_id"] or cur.get("group_id") or "").strip(),
            "user_id": (user_id or parsed["user_id"] or cur.get("user_id") or "").strip(),
            "last_active": float(last_active if last_active is not None else (cur.get("last_active") or 0) or now),
        }
        try:
            conn = self._conn()
            conn.execute(
                """INSERT INTO sessions (origin, name, is_group, group_id, user_id, last_active, updated_at)
                   VALUES (?,?,?,?,?,?,?)
                   ON CONFLICT(origin) DO UPDATE SET
                     name=excluded.name, is_group=excluded.is_group,
                     group_id=excluded.group_id, user_id=excluded.user_id,
                     last_active=excluded.last_active, updated_at=excluded.updated_at""",
                (origin, vals["name"], vals["is_group"], vals["group_id"], vals["user_id"],
                 vals["last_active"], now),
            )
            conn.commit()
        except Exception as e:
            logger.debug(f"[会话] upsert 失败: {e}")

    def touch(self, origin: str) -> None:
        origin = (origin or "").strip()
        if not origin:
            return
        try:
            conn = self._conn()
            conn.execute(
                "INSERT INTO sessions (origin, last_active, updated_at) VALUES (?,?,?) "
                "ON CONFLICT(origin) DO UPDATE SET last_active=excluded.last_active, updated_at=excluded.updated_at",
                (origin, time.time(), time.time()),
            )
            conn.commit()
        except Exception:
            pass

    def set_config(self, origin: str, fields: dict) -> None:
        """只写白名单字段；None 写入 NULL = 跟随全局。"""
        origin = (origin or "").strip()
        if not origin or not isinstance(fields, dict):
            return
        clean: dict = {}
        for k in CONFIG_FIELDS:
            if k not in fields:
                continue
            v = fields[k]
            if k in INT_FIELDS:
                if v is None or v == "":
                    clean[k] = None
                else:
                    try:
                        clean[k] = int(v)
                    except (TypeError, ValueError):
                        continue
            elif k in STR_FIELDS:
                clean[k] = "" if v is None else str(v).strip()
        if not clean:
            return
        self.upsert(origin)
        try:
            conn = self._conn()
            sets = ", ".join(f"{k}=?" for k in clean)
            conn.execute(
                f"UPDATE sessions SET {sets}, updated_at=? WHERE origin=?",
                tuple(clean.values()) + (time.time(), origin),
            )
            conn.commit()
        except Exception as e:
            logger.debug(f"[会话] set_config 失败: {e}")

    def reset_config(self, origin: str) -> None:
        """把所有覆盖字段清回「跟随全局」，保留会话底表行。"""
        origin = (origin or "").strip()
        if not origin:
            return
        try:
            conn = self._conn()
            conn.execute(
                """UPDATE sessions SET wf_t2i='', wf_i2i='', pin_t2i=0, pin_i2i=0,
                   max_total=NULL, max_hour=NULL, max_day=NULL, nsfw=NULL, card=NULL,
                   enabled=NULL, note='', updated_at=? WHERE origin=?""",
                (time.time(), origin),
            )
            conn.commit()
        except Exception as e:
            logger.debug(f"[会话] reset_config 失败: {e}")

    def delete(self, origin: str) -> bool:
        origin = (origin or "").strip()
        if not origin:
            return False
        try:
            conn = self._conn()
            cur = conn.execute("DELETE FROM sessions WHERE origin=?", (origin,))
            conn.execute("DELETE FROM session_usage WHERE origin=?", (origin,))
            conn.commit()
            return cur.rowcount > 0
        except Exception as e:
            logger.debug(f"[会话] delete 失败: {e}")
            return False

    # ------------------------------------------------------------------ #
    # 出图链路查询助手
    # ------------------------------------------------------------------ #
    def is_disabled(self, origin: str) -> bool:
        sc = self.get(origin)
        return bool(sc and sc.get("enabled") == 0)

    def pinned_workflow(self, origin: str, is_img2img: bool) -> str:
        """固定工作流（固定开关开启时返回名字，否则空串）。"""
        sc = self.get(origin)
        if not sc:
            return ""
        key = "wf_i2i" if is_img2img else "wf_t2i"
        pin = "pin_i2i" if is_img2img else "pin_t2i"
        if sc.get(pin):
            return (sc.get(key) or "").strip()
        return ""

    def session_workflow(self, origin: str, is_img2img: bool) -> str:
        """会话当前手动设置的工作流（不看固定开关）。"""
        sc = self.get(origin)
        if not sc:
            return ""
        return (sc.get("wf_i2i" if is_img2img else "wf_t2i") or "").strip()

    # ------------------------------------------------------------------ #
    # 会话限额（与用户限额并存）
    # ------------------------------------------------------------------ #
    def has_quota_config(self, origin: str) -> bool:
        sc = self.get(origin)
        return bool(sc and any(sc.get(k) is not None for k in ("max_total", "max_hour", "max_day")))

    def check_quota(self, origin: str) -> tuple[bool, str]:
        """校验会话限额。未配置 → 直接放行。返回 (是否允许, 拒绝原因)。"""
        sc = self.get(origin)
        if not sc:
            return True, ""
        limits = {k: sc.get(k) for k in ("max_total", "max_hour", "max_day")}
        if all(v is None for v in limits.values()):
            return True, ""
        try:
            conn = self._conn()
            row = conn.execute("SELECT * FROM session_usage WHERE origin=?", (origin,)).fetchone()
            usage = dict(row) if row else {}
        except Exception:
            return True, ""
        now = time.time()
        total = int(usage.get("total_used") or 0)
        hour = int(usage.get("hour_used") or 0) if int(usage.get("hour_start") or 0) == _hour_start(now) else 0
        day = int(usage.get("day_used") or 0) if int(usage.get("day_start") or 0) == _day_start(now) else 0
        mt, mh, md = (limits["max_total"], limits["max_hour"], limits["max_day"])
        if mt is not None and mt >= 0 and total >= mt:
            return False, "本会话的生图总次数已用尽，请联系管理员调整。"
        if md is not None and md >= 0 and day >= md:
            return False, "本会话今天的生图次数已用完，请明天再试。"
        if mh is not None and mh >= 0 and hour >= mh:
            nxt = _hour_start(now) + 3600
            hhmm = time.strftime("%H:%M", time.localtime(nxt))
            return False, f"本会话当前小时的生图次数已用完，请到 {hhmm} 后再试。"
        return True, ""

    def record_usage(self, origin: str) -> None:
        origin = (origin or "").strip()
        if not origin:
            return
        now = time.time()
        hs, ds = _hour_start(now), _day_start(now)
        try:
            conn = self._conn()
            row = conn.execute("SELECT * FROM session_usage WHERE origin=?", (origin,)).fetchone()
            if row:
                u = dict(row)
                hour = int(u.get("hour_used") or 0) + 1 if int(u.get("hour_start") or 0) == hs else 1
                day = int(u.get("day_used") or 0) + 1 if int(u.get("day_start") or 0) == ds else 1
                conn.execute(
                    """UPDATE session_usage SET total_used=total_used+1, hour_used=?, hour_start=?,
                       day_used=?, day_start=?, updated_at=? WHERE origin=?""",
                    (hour, hs, day, ds, now, origin),
                )
            else:
                conn.execute(
                    """INSERT INTO session_usage (origin, total_used, hour_used, hour_start, day_used, day_start, updated_at)
                       VALUES (?,1,1,?,1,?,?)""",
                    (origin, hs, ds, now),
                )
            conn.commit()
        except Exception as e:
            logger.debug(f"[会话] record_usage 失败: {e}")

    def get_usage(self, origin: str) -> dict:
        origin = (origin or "").strip()
        if not origin:
            return {"total_used": 0, "hour_used": 0, "day_used": 0}
        try:
            row = self._conn().execute("SELECT * FROM session_usage WHERE origin=?", (origin,)).fetchone()
            if not row:
                return {"total_used": 0, "hour_used": 0, "day_used": 0}
            u = dict(row)
            now = time.time()
            return {
                "total_used": int(u.get("total_used") or 0),
                "hour_used": int(u.get("hour_used") or 0) if int(u.get("hour_start") or 0) == _hour_start(now) else 0,
                "day_used": int(u.get("day_used") or 0) if int(u.get("day_start") or 0) == _day_start(now) else 0,
            }
        except Exception:
            return {"total_used": 0, "hour_used": 0, "day_used": 0}

    def reset_usage(self, origin: str) -> bool:
        origin = (origin or "").strip()
        if not origin:
            return False
        try:
            conn = self._conn()
            cur = conn.execute("DELETE FROM session_usage WHERE origin=?", (origin,))
            conn.commit()
            return cur.rowcount > 0
        except Exception:
            return False

    # ------------------------------------------------------------------ #
    # 历史统计（读 gallery.db / oplog.db，不落新表）
    # ------------------------------------------------------------------ #
    def history_stats(self) -> dict[str, dict]:
        """按 UMO 聚合图库/操作日志：总出图、今日出图、最近一次工作流、最近活跃时间。

        v7.11.3 修正两处口径错误：
        1) 插件记录（``user_id`` 为 ``plugin:<source>``）**只按插件名聚合**，key 固定
           为 ``plugin:<source>``，与会话归属互斥——此前用 session_id（群号）当 key，
           导致「用某插件出过图的群」被误判成插件、且同一插件在每个群各产生一条重复。
        2) 普通记录统一走 :func:`normalize_origin` 归一到完整 UMO，与联系人同步的
           UMO key 对齐——此前纯 ID 与 UMO 各算一行，私聊数翻倍、群聊被判成私聊。
        3) oplog 段不再 ``setdefault`` 新会话，只给已存在的 key 补最近活跃时间；
           否则历史 oplog 里的纯 ID 会凭空造出一大堆「不知名的会话」。
        """
        out: dict[str, dict] = {}
        today = _day_start(time.time())
        known_groups = self.known_group_ids()
        # v7.11.7：群名作第三判据——图库旧图 group_id 空、群号又不在联系人/sessions
        # 快照时，只要 group_name 命中已知群名，仍把这条记录判回群聊。
        known_group_names = self.known_group_names()
        gallery = self.data_dir / "gallery.db"
        if gallery.is_file():
            try:
                conn = sqlite3.connect(str(gallery), timeout=5.0)
                conn.row_factory = sqlite3.Row
                rows = conn.execute(
                    """SELECT session_id, user_id, user_name, group_name, group_id, workflow,
                              created_at, is_img2img
                       FROM images WHERE deleted=0 AND status=0
                       ORDER BY created_at DESC LIMIT 20000"""
                ).fetchall()
                conn.close()
                for r in rows:
                    sid = str(r["session_id"] or "").strip()
                    uid = str(r["user_id"] or "").strip()
                    is_plugin = uid.startswith("plugin:")
                    if is_plugin:
                        # 插件身份来自 user_id，与会话无关 → 按插件名聚合，绝不用 sid
                        key = uid.strip()
                    else:
                        if not sid:
                            continue
                        gid = str(r["group_id"] or "").strip()
                        # v7.11.6/v7.11.7：外部插件（星语等）合成 event 的
                        # get_group_id() 常为空，图库里只剩 session_id=群号；若该 ID
                        # 命中已知群号（联系人快照 ∪ sessions 已登记群），或 group_name
                        # 命中已知群名，都必须判群聊，否则会被 normalize_origin 当成
                        # 私聊 → 落进「非好友」、每次出图都更新那条「人名假私聊」。
                        _gname = str(r["group_name"] or "").strip()
                        if not gid and (sid in known_groups or (_gname and _gname in known_group_names)):
                            gid = sid
                        key = normalize_origin(sid, group_id=gid)
                    st = out.setdefault(key, {
                        "draw_total": 0, "draw_today": 0, "recent_workflow": "",
                        "recent_i2i_workflow": "", "last_draw": 0, "is_plugin": is_plugin,
                        "plugin_name": uid[7:].strip() if is_plugin else "",
                        "group_name": "", "user_name": "",
                    })
                    if not st["group_name"] and (r["group_name"] or "").strip():
                        st["group_name"] = str(r["group_name"]).strip()
                    if not st["user_name"] and (r["user_name"] or "").strip():
                        st["user_name"] = str(r["user_name"]).strip()
                    st["draw_total"] += 1
                    ca = float(r["created_at"] or 0)
                    if ca >= today:
                        st["draw_today"] += 1
                    # v7.11.5：按 t2i / i2i 分开记「最后一次实际出图用的工作流」，
                    # 二者互不污染（此前 recent_workflow 会把最新一张 i2i 也算进去）。
                    _wf = str(r["workflow"] or "").strip()
                    if _wf:
                        if r["is_img2img"]:
                            if not st["recent_i2i_workflow"]:
                                st["recent_i2i_workflow"] = _wf
                        elif not st["recent_workflow"]:
                            st["recent_workflow"] = _wf
                    if ca > st["last_draw"]:
                        st["last_draw"] = ca
            except Exception as e:
                logger.debug(f"[会话] 图库统计聚合失败: {e}")
        # 操作日志只给已存在的会话补「最近活动时间」（不造新会话、不重复累计出图数）
        oplog = self.data_dir / "oplog.db"
        if oplog.is_file():
            try:
                conn = sqlite3.connect(str(oplog), timeout=5.0)
                conn.row_factory = sqlite3.Row
                rows = conn.execute(
                    "SELECT session_id, user_id, event, ts FROM oplog WHERE session_id != '' ORDER BY ts DESC LIMIT 20000"
                ).fetchall()
                conn.close()
                for r in rows:
                    sid = str(r["session_id"] or "").strip()
                    uid = str(r["user_id"] or "").strip()
                    if uid.startswith("plugin:"):
                        key = uid.strip()
                    else:
                        key = normalize_origin(sid)
                    st = out.get(key)
                    if st is None:
                        continue  # 关键：oplog 不再凭空造会话
                    ts = float(r["ts"] or 0)
                    if ts > st["last_draw"]:
                        st["last_draw"] = ts
            except Exception as e:
                logger.debug(f"[会话] 操作日志统计聚合失败: {e}")
        return out


# ---------------------------------------------------------------------- #
# 会话头像缓存（v7.11.1）：腾讯公开 CDN 抓取 → 落盘 → data URI
# ---------------------------------------------------------------------- #
# 控制台在 AstrBot sandbox iframe 里，浏览器直连 CDN 常因内网/代理/CORS 失败；
# 由后端抓取并缓存成本地文件，再转 data URI 给 <img src>，两条 WebUI 通道通用。
_FRIEND_URL = "https://q4.qlogo.cn/headimg_dl?dst_uin={id}&spec=140"
_FRIEND_FALLBACK = "https://q1.qlogo.cn/g?b=qq&nk={id}&s=100"
_GROUP_URL = "https://p.qlogo.cn/gh/{id}/{id}/140"
_AVATAR_MAX_BYTES = 512 * 1024


def _avatar_mime(data: bytes) -> str:
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return ""


class AvatarCache:
    """用户/群头像：CDN 抓取 + 本地缓存 + data URI。抓取失败静默降级，不影响会话列表。"""

    def __init__(self, cache_dir: Path, *, timeout: float = 8.0, concurrency: int = 6) -> None:
        self.cache_dir = Path(cache_dir)
        self.timeout = float(timeout)
        self.concurrency = max(1, int(concurrency))
        try:
            self.cache_dir.mkdir(parents=True, exist_ok=True)
        except Exception as e:
            logger.debug(f"[头像] 缓存目录创建失败: {e}")

    def _path(self, kind: str, ident: str) -> Path:
        clean = re.sub(r"[^0-9A-Za-z_-]", "", str(ident))[:64]
        return self.cache_dir / f"{'group' if kind == 'group' else 'user'}_{clean}.img"

    def _candidates(self, kind: str, ident: str) -> list[str]:
        if kind == "group":
            return [_GROUP_URL.format(id=ident)]
        return [_FRIEND_URL.format(id=ident), _FRIEND_FALLBACK.format(id=ident)]

    async def _fetch(self, kind: str, ident: str):
        import asyncio

        try:
            import aiohttp
        except ImportError:
            return None
        timeout = aiohttp.ClientTimeout(total=self.timeout)
        for url in self._candidates(kind, ident):
            try:
                async with aiohttp.ClientSession(timeout=timeout) as sess:
                    async with sess.get(url) as resp:
                        if resp.status != 200:
                            continue
                        data = await resp.read()
                        if not data or len(data) > _AVATAR_MAX_BYTES or not _avatar_mime(data):
                            continue
                        return data
            except asyncio.CancelledError:
                raise
            except Exception:
                continue
        return None

    async def ensure_map(self, kind: str, ids: list[str], *, force: bool = False,
                         max_age_days: float | None = 30) -> dict[str, str]:
        """返回 {id: data URI}。缓存命中直接用；未命中抓 CDN；失败回退过期缓存。"""
        import asyncio

        kind = "group" if kind == "group" else "user"
        ids = [str(i).strip() for i in (ids or []) if str(i).strip()][:200]
        if not ids:
            return {}
        now = time.time()
        result: dict[str, str] = {}
        sem = asyncio.Semaphore(self.concurrency)

        async def one(ident: str):
            path = self._path(kind, ident)
            if not force and path.is_file():
                try:
                    if max_age_days is None or (now - path.stat().st_mtime) < float(max_age_days) * 86400:
                        return path.read_bytes()
                except Exception:
                    pass
            async with sem:
                data = await self._fetch(kind, ident)
            if data:
                try:
                    tmp = path.with_name(path.name + ".tmp")
                    tmp.write_bytes(data)
                    tmp.replace(path)
                except Exception as e:
                    logger.debug(f"[头像] 落盘失败 {ident}: {e}")
                return data
            if not force and path.is_file():
                try:
                    return path.read_bytes()  # 抓取失败时用过期缓存兜底
                except Exception:
                    pass
            return None

        outcomes = await asyncio.gather(*(one(i) for i in ids), return_exceptions=True)
        for ident, data in zip(ids, outcomes):
            if not isinstance(data, (bytes, bytearray)):
                continue
            mime = _avatar_mime(bytes(data))
            if mime:
                result[ident] = f"data:{mime};base64,{base64.b64encode(bytes(data)).decode('ascii')}"
        return result

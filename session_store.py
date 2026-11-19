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
        """按 session_id 聚合图库/操作日志：总出图、今日出图、最近一次工作流、最近活跃时间。

        另外把 ``user_id LIKE 'plugin:%'`` 的记录归到 ``plugin:<source>`` 伪会话，
        供「插件」标签页展示（插件来源在归档时由 _do_draw 写成 plugin:<source>）。
        """
        out: dict[str, dict] = {}
        today = _day_start(time.time())
        gallery = self.data_dir / "gallery.db"
        if gallery.is_file():
            try:
                conn = sqlite3.connect(str(gallery), timeout=5.0)
                conn.row_factory = sqlite3.Row
                rows = conn.execute(
                    """SELECT session_id, user_id, user_name, group_name, workflow, created_at, is_img2img
                       FROM images WHERE deleted=0 AND status=0
                       ORDER BY created_at DESC LIMIT 20000"""
                ).fetchall()
                conn.close()
                for r in rows:
                    sid = str(r["session_id"] or "").strip()
                    uid = str(r["user_id"] or "").strip()
                    is_plugin = uid.startswith("plugin:")
                    key = sid if sid else (uid if is_plugin else "")
                    if not key:
                        continue
                    st = out.setdefault(key, {
                        "draw_total": 0, "draw_today": 0, "recent_workflow": "",
                        "recent_i2i_workflow": "", "last_draw": 0, "is_plugin": is_plugin,
                        "plugin_name": uid[7:] if is_plugin else "",
                        "group_name": "",
                    })
                    if not st["group_name"] and (r["group_name"] or "").strip():
                        st["group_name"] = str(r["group_name"]).strip()
                    st["draw_total"] += 1
                    ca = float(r["created_at"] or 0)
                    if ca >= today:
                        st["draw_today"] += 1
                    if not st["recent_workflow"] and (r["workflow"] or "").strip():
                        st["recent_workflow"] = str(r["workflow"]).strip()
                    if not st["recent_i2i_workflow"] and r["is_img2img"] and (r["workflow"] or "").strip():
                        st["recent_i2i_workflow"] = str(r["workflow"]).strip()
                    if ca > st["last_draw"]:
                        st["last_draw"] = ca
            except Exception as e:
                logger.debug(f"[会话] 图库统计聚合失败: {e}")
        # 操作日志只用来补「最近活动时间」（不重复累计出图数——图库才是出图口径）
        oplog = self.data_dir / "oplog.db"
        if oplog.is_file():
            try:
                conn = sqlite3.connect(str(oplog), timeout=5.0)
                conn.row_factory = sqlite3.Row
                rows = conn.execute(
                    "SELECT session_id, event, ts FROM oplog WHERE session_id != '' ORDER BY ts DESC LIMIT 20000"
                ).fetchall()
                conn.close()
                for r in rows:
                    key = str(r["session_id"] or "").strip()
                    if not key:
                        continue
                    st = out.setdefault(key, {
                        "draw_total": 0, "draw_today": 0, "recent_workflow": "",
                        "recent_i2i_workflow": "", "last_draw": 0, "is_plugin": False,
                        "plugin_name": "", "group_name": "",
                    })
                    ts = float(r["ts"] or 0)
                    if ts > st["last_draw"]:
                        st["last_draw"] = ts
            except Exception as e:
                logger.debug(f"[会话] 操作日志统计聚合失败: {e}")
        return out

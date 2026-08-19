"""生图次数限制（配额）存储与判定。

用独立的 SQLite（quota.db）维护：
- 每个用户的生图计数（总次数 + 当前小时次数 + 当天次数）——插件自己维护，
  与图库归档解耦，因此「重置」只清零配额计数，不影响 gallery 里已存档的图。
- 每个用户单独的生图限额配置（max_total / max_hour / max_day，-1 表示不限制）；
  未单独配置的用户回退使用全局配置。

「当前小时」按 UTC 小时边界判断（hour_start）；「当天」按本地时区 0 点边界判断
（day_start）。跨边界时自动把对应计数清零。
"""

import logging
import sqlite3
import time
from pathlib import Path

logger = logging.getLogger("astrbot_plugin_comfyui_anima.quota")

# 当前小时窗口：以整点小时为边界
HOUR_SECONDS = 3600

_HAS_SQLITE = True


def _hour_start(ts: float) -> int:
    """返回 ts 所在小时的起始时间戳（秒）。"""
    return int(ts // HOUR_SECONDS * HOUR_SECONDS)


def _day_start(ts: float) -> int:
    """返回 ts 所在「本地日期」的 0 点时间戳（秒）。跨天按此判断重置。"""
    lt = time.localtime(ts)
    return int(time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday, 0, 0, 0, 0, 0, -1)))


class QuotaStore:
    """生图配额存储。单线程事件循环使用，线程不安全但足够。"""

    def __init__(self, data_dir: Path) -> None:
        self.data_dir = Path(data_dir)
        self.db_path = self.data_dir / "quota.db"
        self._conn = None
        self._init_db()

    # ------------------------------------------------------------------ #
    # 连接 / 建表
    # ------------------------------------------------------------------ #
    def _conn_get(self):
        if self._conn is None:
            self._conn = sqlite3.connect(str(self.db_path))
            self._conn.row_factory = sqlite3.Row
        return self._conn

    def _init_db(self) -> None:
        try:
            conn = self._conn_get()
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS quota_usage (
                    user_id    TEXT PRIMARY KEY,
                    user_name  TEXT DEFAULT '',
                    total_used INTEGER NOT NULL DEFAULT 0,
                    hour_used  INTEGER NOT NULL DEFAULT 0,
                    hour_start INTEGER NOT NULL DEFAULT 0,
                    day_used   INTEGER NOT NULL DEFAULT 0,
                    day_start  INTEGER NOT NULL DEFAULT 0,
                    updated_at REAL NOT NULL DEFAULT 0
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS quota_config (
                    user_id    TEXT PRIMARY KEY,
                    max_total  INTEGER NOT NULL DEFAULT -1,
                    max_hour   INTEGER NOT NULL DEFAULT -1,
                    max_day    INTEGER NOT NULL DEFAULT -1,
                    updated_at REAL NOT NULL DEFAULT 0
                )
                """
            )
            # 兼容旧库：历史版本没有 day_used / day_start / max_day 列时补上
            usage_cols = {r["name"] for r in conn.execute("PRAGMA table_info(quota_usage)")}
            if "day_used" not in usage_cols:
                conn.execute("ALTER TABLE quota_usage ADD COLUMN day_used INTEGER NOT NULL DEFAULT 0")
            if "day_start" not in usage_cols:
                conn.execute("ALTER TABLE quota_usage ADD COLUMN day_start INTEGER NOT NULL DEFAULT 0")
            cfg_cols = {r["name"] for r in conn.execute("PRAGMA table_info(quota_config)")}
            if "max_day" not in cfg_cols:
                conn.execute("ALTER TABLE quota_config ADD COLUMN max_day INTEGER NOT NULL DEFAULT -1")
            conn.commit()
        except Exception as e:  # pragma: no cover
            logger.warning(f"[限额] 初始化数据库失败: {e}")

    # ------------------------------------------------------------------ #
    # 计数
    # ------------------------------------------------------------------ #
    def record_used(self, user_id: str, user_name: str = "") -> None:
        """生图成功一次，增加该用户总次数、当前小时次数与当天次数。"""
        if not user_id:
            return
        now = time.time()
        hs = _hour_start(now)
        ds = _day_start(now)
        conn = self._conn_get()
        row = conn.execute(
            "SELECT hour_used, hour_start, day_used, day_start FROM quota_usage WHERE user_id=?",
            (user_id,),
        ).fetchone()
        if row is None:
            conn.execute(
                "INSERT INTO quota_usage (user_id, user_name, total_used, hour_used, hour_start, day_used, day_start, updated_at) "
                "VALUES (?,?,1,1,?,1,?,?)",
                (user_id, user_name or "", hs, ds, now),
            )
        else:
            # 跨小时重置小时计数；跨天重置当天计数
            h_used = row["hour_used"]
            if row["hour_start"] != hs:
                h_used = 0
            d_used = row["day_used"]
            if row["day_start"] != ds:
                d_used = 0
            conn.execute(
                "UPDATE quota_usage SET user_name=?, total_used=total_used+1, "
                "hour_used=?, hour_start=?, day_used=?, day_start=?, updated_at=? WHERE user_id=?",
                (user_name or "", h_used + 1, hs, d_used + 1, ds, now, user_id),
            )
        try:
            conn.commit()
        except Exception as e:  # pragma: no cover
            logger.warning(f"[限额] 记录计数失败: {e}")

    def peek(self, user_id: str):
        """只读返回某用户当前用量对象（用于日志核对，不修改计数）。无则返回 None。"""
        if not user_id:
            return None
        conn = self._conn_get()
        row = conn.execute(
            "SELECT user_id, user_name, total_used, hour_used, hour_start, day_used, day_start "
            "FROM quota_usage WHERE user_id=?",
            (user_id,),
        ).fetchone()
        return row

    def get_usage(self, user_id: str) -> dict:
        """返回用户当前用量，含跨小时/跨天自动重置后的计数。"""
        now = time.time()
        hs = _hour_start(now)
        ds = _day_start(now)
        base = {"user_id": user_id, "total_used": 0, "hour_used": 0, "day_used": 0}
        if not user_id:
            return base
        row = self._conn_get().execute(
            "SELECT total_used, hour_used, hour_start, day_used, day_start FROM quota_usage WHERE user_id=?",
            (user_id,),
        ).fetchone()
        if row is None:
            return base
        base["total_used"] = row["total_used"] or 0
        base["hour_used"] = (row["hour_used"] or 0) if row["hour_start"] == hs else 0
        base["day_used"] = (row["day_used"] or 0) if row["day_start"] == ds else 0
        return base

    # ------------------------------------------------------------------ #
    # 配置
    # ------------------------------------------------------------------ #
    def set_user_config(self, user_id: str, max_total: int, max_hour: int, max_day: int = -1) -> None:
        """保存用户单独配置；-1 表示不限制。"""
        conn = self._conn_get()
        conn.execute(
            "INSERT INTO quota_config (user_id, max_total, max_hour, max_day, updated_at) VALUES (?,?,?,?,?) "
            "ON CONFLICT(user_id) DO UPDATE SET max_total=excluded.max_total, "
            "max_hour=excluded.max_hour, max_day=excluded.max_day, updated_at=excluded.updated_at",
            (user_id, int(max_total), int(max_hour), int(max_day), time.time()),
        )
        try:
            conn.commit()
        except Exception as e:  # pragma: no cover
            logger.warning(f"[限额] 保存配置失败: {e}")

    def get_user_config(self, user_id: str) -> dict:
        """返回用户单独配置（无记录时返回空 dict，表示用全局）。"""
        if not user_id:
            return {}
        row = self._conn_get().execute(
            "SELECT max_total, max_hour, max_day FROM quota_config WHERE user_id=?", (user_id,)
        ).fetchone()
        if row is None:
            return {}
        return {"max_total": row["max_total"], "max_hour": row["max_hour"], "max_day": row["max_day"]}

    def delete_user_config(self, user_id: str) -> None:
        """删除用户单独配置（删除后该用户回退使用全局配置）。"""
        try:
            self._conn_get().execute("DELETE FROM quota_config WHERE user_id=?", (user_id,))
            self._conn_get().commit()
        except Exception as e:  # pragma: no cover
            logger.warning(f"[限额] 删除配置失败: {e}")

    # ------------------------------------------------------------------ #
    # 列表 / 重置
    # ------------------------------------------------------------------ #
    def list_users(self) -> list[dict]:
        """返回所有有过生图记录或单独配置的用户（按总次数倒序）。

        每项：user_id, user_name, total_used, hour_used, day_used（跨边界自动重置）,
        max_total, max_hour, max_day（单独配置；未配置为 None 表示用全局）。
        """
        conn = self._conn_get()
        now = time.time()
        hs = _hour_start(now)
        ds = _day_start(now)
        rows = conn.execute(
            """
            SELECT u.user_id, u.user_name, u.total_used, u.hour_used, u.hour_start,
                   u.day_used, u.day_start, c.max_total, c.max_hour, c.max_day
            FROM quota_usage u
            LEFT JOIN quota_config c ON c.user_id = u.user_id
            ORDER BY u.total_used DESC
            """
        ).fetchall()
        seen = set()
        result = []
        for r in rows:
            seen.add(r["user_id"])
            hour_used = (r["hour_used"] or 0) if r["hour_start"] == hs else 0
            day_used = (r["day_used"] or 0) if r["day_start"] == ds else 0
            result.append(
                {
                    "user_id": r["user_id"],
                    "user_name": r["user_name"] or "",
                    "total_used": r["total_used"] or 0,
                    "hour_used": hour_used,
                    "day_used": day_used,
                    "max_total": r["max_total"],
                    "max_hour": r["max_hour"],
                    "max_day": r["max_day"],
                }
            )
        # 合并只有单独配置、但尚无生图记录的用户
        cfg_rows = conn.execute(
            "SELECT user_id, max_total, max_hour, max_day FROM quota_config ORDER BY updated_at DESC"
        ).fetchall()
        for r in cfg_rows:
            if r["user_id"] in seen:
                continue
            result.append(
                {
                    "user_id": r["user_id"],
                    "user_name": "",
                    "total_used": 0,
                    "hour_used": 0,
                    "day_used": 0,
                    "max_total": r["max_total"],
                    "max_hour": r["max_hour"],
                    "max_day": r["max_day"],
                }
            )
        return result

    def reset_user(self, user_id: str) -> bool:
        """重置某用户的总次数、当前小时次数与当天次数为 0。返回是否操作成功。"""
        try:
            now = time.time()
            cur = self._conn_get().execute(
                "UPDATE quota_usage SET total_used=0, hour_used=0, hour_start=?, "
                "day_used=0, day_start=?, updated_at=? WHERE user_id=?",
                (_hour_start(now), _day_start(now), now, user_id),
            )
            self._conn_get().commit()
            return cur.rowcount > 0
        except Exception as e:  # pragma: no cover
            logger.warning(f"[限额] 重置用户失败: {e}")
            return False

    def reset_all(self) -> int:
        """重置所有用户的总次数、当前小时次数与当天次数为 0。返回重置的行数。"""
        try:
            now = time.time()
            cur = self._conn_get().execute(
                "UPDATE quota_usage SET total_used=0, hour_used=0, hour_start=?, "
                "day_used=0, day_start=?, updated_at=?",
                (_hour_start(now), _day_start(now), now),
            )
            self._conn_get().commit()
            return cur.rowcount
        except Exception as e:  # pragma: no cover
            logger.warning(f"[限额] 重置全部失败: {e}")
            return 0

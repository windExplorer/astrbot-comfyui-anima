"""LLM token 使用统计存储。

用独立的 SQLite（llm_token.db）维护插件相关的 LLM 调用 token 用量，包括：

1. 插件自己发起的辅助 LLM 调用（翻译 / 动漫改写 / 写实清理 / 参数提取，
   scene=translate / rewrite_anima / rewrite_real / extract_args）。
2. 用户通过 LLM Agent 对话触发画图时，主对话最终 LLM 响应的用量
   （scene=agent_draw）：通过 on_llm_response 钩子在 agent 结束时补记。

注意统计边界：用户在 AI 对话里触发画图那一次主对话调用发生在 AstrBot
核心层，插件无法拿到「触发工具意图那次」的 usage（AstrBot 的 on_llm_response
只在 agent 结束广播一次），只能记到画图收尾总结那次的用量。

表结构按 (user_id, scene, model, 日期) 聚合为一行，多次调用累加
total/call_count，避免每条调用单独一行导致表无限膨胀；跨天自动 rollover
到新日期桶。缓存命中（input_cached）单独记录，满足缓存统计需求。
"""

import logging
import sqlite3
import time
from pathlib import Path

logger = logging.getLogger("astrbot_plugin_comfyui_anima.token")

_HAS_SQLITE = True

# 无 user_id 时的兜底归属
SYSTEM_USER = "__system__"


def _day_bucket(ts: float) -> str:
    """返回 ts 所在「本地日期」的字符串桶（YYYY-MM-DD）。"""
    lt = time.localtime(ts)
    return f"{lt.tm_year:04d}-{lt.tm_mon:02d}-{lt.tm_mday:02d}"


class TokenStore:
    """LLM token 用量存储。单线程事件循环使用，线程不安全但足够。"""

    def __init__(self, data_dir: Path) -> None:
        self.data_dir = Path(data_dir)
        self.db_path = self.data_dir / "llm_token.db"
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
                CREATE TABLE IF NOT EXISTS llm_usage (
                    user_id      TEXT NOT NULL,
                    user_name    TEXT NOT NULL DEFAULT '',
                    scene        TEXT NOT NULL,
                    model        TEXT NOT NULL DEFAULT '',
                    day_bucket   TEXT NOT NULL,
                    input_other  INTEGER NOT NULL DEFAULT 0,
                    input_cached INTEGER NOT NULL DEFAULT 0,
                    output       INTEGER NOT NULL DEFAULT 0,
                    total        INTEGER NOT NULL DEFAULT 0,
                    call_count   INTEGER NOT NULL DEFAULT 0,
                    updated_at   REAL NOT NULL DEFAULT 0,
                    PRIMARY KEY (user_id, scene, model, day_bucket)
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_llm_usage_day ON llm_usage (day_bucket)"
            )
            # 兼容旧库：给老表补 user_name 列（若有则忽略）
            try:
                cols = {r[1] for r in conn.execute("PRAGMA table_info(llm_usage)").fetchall()}
                if "user_name" not in cols:
                    conn.execute("ALTER TABLE llm_usage ADD COLUMN user_name TEXT NOT NULL DEFAULT ''")
            except Exception as e:  # pragma: no cover
                logger.warning(f"[token] 迁移 user_name 列失败: {e}")
            conn.commit()
        except Exception as e:  # pragma: no cover
            logger.warning(f"[token] 初始化数据库失败: {e}")

    # ------------------------------------------------------------------ #
    # 记录
    # ------------------------------------------------------------------ #
    def record_used(
        self,
        user_id: str,
        scene: str,
        model: str = "",
        input_other: int = 0,
        input_cached: int = 0,
        output: int = 0,
        user_name: str = "",
    ) -> None:
        """累加一次 LLM 调用用量。按 (user_id, scene, model, 日期) 聚合。

        ``input_other`` 非缓存输入；``input_cached`` 命中缓存的输入；``output``
        输出。``total`` 为三者之和。``user_id`` 为空时归入 ``__system__``。
        ``user_name`` 记录该用户最近一次的名字（非空才覆盖，供用户排行展示）。
        """
        if not user_id:
            user_id = SYSTEM_USER
        user_name = (user_name or "").strip()
        input_other = max(int(input_other or 0), 0)
        input_cached = max(int(input_cached or 0), 0)
        output = max(int(output or 0), 0)
        total = input_other + input_cached + output
        now = time.time()
        bucket = _day_bucket(now)
        conn = self._conn_get()
        try:
            conn.execute(
                """
                INSERT INTO llm_usage
                    (user_id, user_name, scene, model, day_bucket, input_other, input_cached,
                     output, total, call_count, updated_at)
                VALUES (?,?,?,?,?,?,?,?,?,1,?)
                ON CONFLICT(user_id, scene, model, day_bucket) DO UPDATE SET
                    user_name    = CASE WHEN excluded.user_name <> '' THEN excluded.user_name
                                        ELSE llm_usage.user_name END,
                    input_other  = llm_usage.input_other + excluded.input_other,
                    input_cached = llm_usage.input_cached + excluded.input_cached,
                    output       = llm_usage.output + excluded.output,
                    total        = llm_usage.total + excluded.total,
                    call_count   = llm_usage.call_count + excluded.call_count,
                    updated_at   = excluded.updated_at
                """,
                (user_id, user_name, scene, model, bucket, input_other, input_cached, output, total, now),
            )
            conn.commit()
        except Exception as e:  # pragma: no cover
            logger.warning(f"[token] 记录用量失败: {e}")

    # ------------------------------------------------------------------ #
    # 查询 / 汇总
    # ------------------------------------------------------------------ #
    def query_summary(self, user_id: str = "", days: int = 30) -> dict:
        """返回汇总统计。

        若 ``user_id`` 非空，只统计该用户；否则统计全部。``days`` 限定最近
        N 天的桶。返回总 input_other / input_cached / output / total /
        call_count 及覆盖天数与模型数。
        """
        bucket_min = _day_bucket(time.time() - int(max(days, 1)) * 86400)
        conn = self._conn_get()
        if user_id:
            row = conn.execute(
                "SELECT SUM(input_other), SUM(input_cached), SUM(output), SUM(total), SUM(call_count) "
                "FROM llm_usage WHERE user_id=? AND day_bucket>=?",
                (user_id, bucket_min),
            ).fetchone()
        else:
            row = conn.execute(
                "SELECT SUM(input_other), SUM(input_cached), SUM(output), SUM(total), SUM(call_count) "
                "FROM llm_usage WHERE day_bucket>=?",
                (bucket_min,),
            ).fetchone()
        in_other = row["SUM(input_other)"] or 0
        in_cached = row["SUM(input_cached)"] or 0
        out = row["SUM(output)"] or 0
        total = row["SUM(total)"] or 0
        calls = row["SUM(call_count)"] or 0
        if user_id:
            model_count = conn.execute(
                "SELECT COUNT(DISTINCT model) FROM llm_usage WHERE user_id=? AND day_bucket>=? AND model<>''",
                (user_id, bucket_min),
            ).fetchone()[0]
        else:
            model_count = conn.execute(
                "SELECT COUNT(DISTINCT model) FROM llm_usage WHERE day_bucket>=? AND model<>''",
                (bucket_min,),
            ).fetchone()[0]
        return {
            "user_id": user_id or "",
            "days": int(days),
            "input_other": int(in_other),
            "input_cached": int(in_cached),
            "output": int(out),
            "total": int(total),
            "call_count": int(calls),
            "model_count": int(model_count),
        }

    def list_users(
        self,
        days: int = 30,
        merge_alsoknown: list[str] | None = None,
    ) -> list[dict]:
        """用户维度汇总，按 total 倒序。用于 WebUI 用户排行。

        ``merge_alsoknown``：可选。给出一组「其他插件/别名」名（如 ["PrivateCompanion"]），
        命中 user_name 的记录合并为一行（user_id 逗号拼接、token 求和、调用次数求和），
        便于把同一插件/非真人来源的分散记录整合；传空列表/None 则不合并。
        返回项含 user_name（供前端展示）与 total/call_count 等。
        """
        bucket_min = _day_bucket(time.time() - int(max(days, 1)) * 86400)
        rows = self._conn_get().execute(
            """
            SELECT user_id, MAX(user_name) AS user_name, SUM(input_other) AS in_other,
                   SUM(input_cached) AS in_cached, SUM(output) AS out, SUM(total) AS total,
                   SUM(call_count) AS calls
            FROM llm_usage
            WHERE day_bucket>=?
            GROUP BY user_id
            ORDER BY total DESC
            """,
            (bucket_min,),
        ).fetchall()
        ranked = [
            {
                "user_id": r["user_id"],
                "user_name": (r["user_name"] or "").strip() or r["user_id"],
                "input_other": int(r["in_other"] or 0),
                "input_cached": int(r["in_cached"] or 0),
                "output": int(r["out"] or 0),
                "total": int(r["total"] or 0),
                "call_count": int(r["calls"] or 0),
            }
            for r in rows
        ]
        # 可选：把「其他插件/别名」命中的记录合并成一行
        if merge_alsoknown:
            names = {str(x).strip() for x in merge_alsoknown if str(x).strip()}
            owner_rows = []
            merged = {
                "user_id": "",
                "user_ids": [],
                "user_id_counts": {},
                "user_name": "",
                "input_other": 0,
                "input_cached": 0,
                "output": 0,
                "total": 0,
                "call_count": 0,
            }
            for r in ranked:
                if r["user_name"] in names:
                    merged["user_name"] = next(iter(names))
                    if r["user_id"]:
                        merged["user_ids"].append(r["user_id"])
                        merged["user_id_counts"][r["user_id"]] = (
                            merged["user_id_counts"].get(r["user_id"], 0) + r["total"]
                        )
                    merged["input_other"] += r["input_other"]
                    merged["input_cached"] += r["input_cached"]
                    merged["output"] += r["output"]
                    merged["total"] += r["total"]
                    merged["call_count"] += r["call_count"]
                else:
                    owner_rows.append(r)
            if merged["total"] > 0:
                merged_ids_dedup = list(dict.fromkeys(merged["user_ids"]))
                merged["user_id"] = ",".join(merged_ids_dedup)
                merged["user_ids"] = merged_ids_dedup
                owner_rows.append(merged)
            owner_rows.sort(key=lambda x: (-x["total"], -x["call_count"]))
            ranked = owner_rows
        return ranked

    def list_detail(self, user_id: str = "", days: int = 30) -> list[dict]:
        """明细查询，可按 user_id 过滤；返回按日期倒序的 (scene/model/日期) 明细。"""
        bucket_min = _day_bucket(time.time() - int(max(days, 1)) * 86400)
        conn = self._conn_get()
        if user_id:
            rows = conn.execute(
                "SELECT user_id, scene, model, day_bucket, input_other, input_cached, output, total, call_count "
                "FROM llm_usage WHERE user_id=? AND day_bucket>=? ORDER BY day_bucket DESC, total DESC",
                (user_id, bucket_min),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT user_id, scene, model, day_bucket, input_other, input_cached, output, total, call_count "
                "FROM llm_usage WHERE day_bucket>=? ORDER BY day_bucket DESC, total DESC",
                (bucket_min,),
            ).fetchall()
        return [
            {
                "user_id": r["user_id"],
                "scene": r["scene"],
                "model": r["model"],
                "day_bucket": r["day_bucket"],
                "input_other": int(r["input_other"] or 0),
                "input_cached": int(r["input_cached"] or 0),
                "output": int(r["output"] or 0),
                "total": int(r["total"] or 0),
                "call_count": int(r["call_count"] or 0),
            }
            for r in rows
        ]

    def list_scenes(self, days: int = 30) -> list[dict]:
        """按调用场景（scene）汇总，用于 WebUI 分类展示。"""
        bucket_min = _day_bucket(time.time() - int(max(days, 1)) * 86400)
        rows = self._conn_get().execute(
            """
            SELECT scene, SUM(input_other) AS in_other, SUM(input_cached) AS in_cached,
                   SUM(output) AS out, SUM(total) AS total, SUM(call_count) AS calls
            FROM llm_usage
            WHERE day_bucket>=?
            GROUP BY scene
            ORDER BY total DESC
            """,
            (bucket_min,),
        ).fetchall()
        return [
            {
                "scene": r["scene"],
                "input_other": int(r["in_other"] or 0),
                "input_cached": int(r["in_cached"] or 0),
                "output": int(r["out"] or 0),
                "total": int(r["total"] or 0),
                "call_count": int(r["calls"] or 0),
            }
            for r in rows
        ]

    def list_daily(self, days: int = 30) -> list[dict]:
        """按日期聚合的每日 token 用量（供趋势面积图/柱状图）。

        返回按日期升序的每日 total 与调用次数，并补全无记录日期为 0，
        保证连续日期可用于折线/柱状图。days<=0 表示全部历史。
        """
        conn = self._conn_get()
        bucket_min = _day_bucket(time.time() - int(max(days, 1)) * 86400)
        if days > 0:
            rows = conn.execute(
                "SELECT day_bucket, SUM(total) AS total, SUM(call_count) AS calls "
                "FROM llm_usage WHERE day_bucket>=? GROUP BY day_bucket ORDER BY day_bucket",
                (bucket_min,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT day_bucket, SUM(total) AS total, SUM(call_count) AS calls "
                "FROM llm_usage GROUP BY day_bucket ORDER BY day_bucket"
            ).fetchall()
        # 全部历史：直接按日期排序返回有记录的日期，不补全空日期
        if days <= 0:
            return [
                {"day_bucket": r["day_bucket"], "total": int(r["total"] or 0), "call_count": int(r["calls"] or 0)}
                for r in rows
            ]
        data = {r["day_bucket"]: {"total": int(r["total"] or 0), "call_count": int(r["calls"] or 0)} for r in rows}
        # 有限窗口：从「今天往前 N-1 天」的 0 点逐天补全到今天，保证连续且不超窗
        lt = time.localtime(time.time())
        today_0 = time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday, 0, 0, 0, 0, 0, -1))
        cursor = today_0 - (int(max(days, 1)) - 1) * 86400
        out = []
        while cursor <= today_0:
            b = _day_bucket(cursor)
            d = data.get(b, {"total": 0, "call_count": 0})
            out.append({"day_bucket": b, "total": d["total"], "call_count": d["call_count"]})
            cursor += 86400
        return out

    def list_hourly(self, hours: int = 24, since_day_start: bool = False) -> list[dict]:
        """token 用量的逐小时面积图数据（按本地时区小时分桶）。

        说明：llm_usage 是按天聚合表，每小时粒度用记录的 updated_at（最后一次调用时间）
        近似聚合——对低频的 token 记录（翻译/改写/画图对话）该小时基本就是实际发生小时，
        满足趋势展示。返回按小时升序的 {hour, total, call_count}，无记录时段补 0。

        起始时间有两种：
        - ``since_day_start=True``：从「今天 0 点的整点」开始，覆盖今天已过去的各小时
          （用于「今天」范围）；
        - ``since_day_start=False``（默认）：从「当前整点 - (hours-1) 小时」开始滚动窗口
          （用于「近 N 天」中的近 1 天，跨昨天）。
        """
        conn = self._conn_get()
        now = time.time()
        hours = max(1, min(int(hours), 24 * 7))
        cur_lt = time.localtime(now)
        if since_day_start:
            start_ts = time.mktime((cur_lt.tm_year, cur_lt.tm_mon, cur_lt.tm_mday, 0, 0, 0, 0, 0, -1))
        else:
            start_ts = now - hours * 3600.0
        rows = conn.execute(
            "SELECT updated_at AS t, SUM(total) AS total, SUM(call_count) AS calls "
            "FROM llm_usage WHERE updated_at>=? GROUP BY updated_at ORDER BY updated_at",
            (start_ts,),
        ).fetchall()
        # 按「本地小时」分桶
        data: dict[str, dict] = {}
        for r in rows:
            lt = time.localtime(r["t"])
            key = f"{lt.tm_year:04d}-{lt.tm_mon:02d}-{lt.tm_mday:02d} {lt.tm_hour:02d}"
            cur = data.setdefault(key, {"total": 0, "call_count": 0})
            cur["total"] += int(r["total"] or 0)
            cur["call_count"] += int(r["calls"] or 0)
        # 从起始整点逐小时补全到当前整点
        cursor = time.mktime((cur_lt.tm_year, cur_lt.tm_mon, cur_lt.tm_mday, cur_lt.tm_hour, 0, 0, 0, 0, -1))
        if since_day_start:
            start = time.mktime((cur_lt.tm_year, cur_lt.tm_mon, cur_lt.tm_mday, 0, 0, 0, 0, 0, -1))
        else:
            start = cursor - (hours - 1) * 3600.0
        out = []
        while cursor >= start:
            lt = time.localtime(cursor)
            key = f"{lt.tm_year:04d}-{lt.tm_mon:02d}-{lt.tm_mday:02d} {lt.tm_hour:02d}"
            d = data.get(key, {"total": 0, "call_count": 0})
            out.append({"hour": f"{lt.tm_hour:02d}:00", "total": d["total"], "call_count": d["call_count"]})
            cursor -= 3600.0
        out.reverse()
        return out

    def list_models(self, days: int = 30) -> list[dict]:
        """按所用 LLM 模型（provider id）汇总，用于模型对比进度条/柱状图。"""
        bucket_min = _day_bucket(time.time() - int(max(days, 1)) * 86400)
        rows = self._conn_get().execute(
            """
            SELECT model, SUM(input_other) AS in_other, SUM(input_cached) AS in_cached,
                   SUM(output) AS out, SUM(total) AS total, SUM(call_count) AS calls
            FROM llm_usage
            WHERE day_bucket>=? AND model<>''
            GROUP BY model
            ORDER BY total DESC
            """,
            (bucket_min,),
        ).fetchall()
        return [
            {
                "model": r["model"],
                "input_other": int(r["in_other"] or 0),
                "input_cached": int(r["in_cached"] or 0),
                "output": int(r["out"] or 0),
                "total": int(r["total"] or 0),
                "call_count": int(r["calls"] or 0),
            }
            for r in rows
        ]

    # ------------------------------------------------------------------ #
    # 重置
    # ------------------------------------------------------------------ #
    def reset_user(self, user_id: str) -> bool:
        """删除某用户全部 token 统计记录。返回是否删除了记录。"""
        try:
            cur = self._conn_get().execute(
                "DELETE FROM llm_usage WHERE user_id=?", (user_id,)
            )
            self._conn_get().commit()
            return cur.rowcount > 0
        except Exception as e:  # pragma: no cover
            logger.warning(f"[token] 重置用户失败: {e}")
            return False

    def reset_all(self) -> int:
        """清空全部 token 统计记录。返回删除的行数。"""
        try:
            cur = self._conn_get().execute("DELETE FROM llm_usage")
            self._conn_get().commit()
            return cur.rowcount
        except Exception as e:  # pragma: no cover
            logger.warning(f"[token] 重置全部失败: {e}")
            return 0

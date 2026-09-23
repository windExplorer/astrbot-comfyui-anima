"""通用「配置项」存储（v7.0.9）。

「配置项」页承载所有**动态枚举**配置：底模（模型族，见 basemodel_store）之外，
还有放大模型、以后可能的采样器/调度器清单等。这里用一张表按 `kind` 区分：

    options(kind, name, note, enabled, sort_order, created_at, updated_at)

- kind：配置项分类（当前用 upscale_model）；
- name：值（放大模型即模型文件名，运行时写进节点）；
- note：备注（倍率/风格，仅展示）；
- enabled：是否在下拉里可选（不想用的可以停用而不删）。

独立 SQLite（data_dir/option.db），WAL + 缺列迁移，与其它 store 同款。
"""

from __future__ import annotations

import logging
import sqlite3
import time
from pathlib import Path

logger = logging.getLogger(__name__)

# 配置项分类
KIND_UPSCALE_MODEL = "upscale_model"
_KNOWN_KINDS = (KIND_UPSCALE_MODEL,)

# 放大模型预设（用户服务器 models/upscale_models/ 常见清单，v7.0.9 按用户截图预设）
DEFAULT_OPTIONS: dict[str, tuple[dict, ...]] = {
    KIND_UPSCALE_MODEL: (
        {"name": "2x-AnimeSharpV2_ESRGAN_Soft.pth", "note": "2x｜动漫柔和（ESRGAN）"},
        {"name": "2x-AnimeSharpV2_MoSR_Sharp.pth", "note": "2x｜动漫锐利（MoSR）"},
        {"name": "2x-AnimeSharpV2_MoSR_Soft.pth", "note": "2x｜动漫柔和（MoSR）"},
        {"name": "2x-AnimeSharpV4_Fast_RCAN_PU.safetensors", "note": "2x｜动漫快速（RCAN-PU）"},
        {"name": "2x-AnimeSharpV4_RCAN.safetensors", "note": "2x｜动漫（RCAN）"},
        {"name": "RealESRGAN_x2plus.pth", "note": "2x｜通用（RealESRGAN）"},
        {"name": "RealESRGAN_x4plus.pth", "note": "4x｜通用（RealESRGAN）"},
        {"name": "RealESRGAN_x4plus_anime_6B.pth", "note": "4x｜动漫（RealESRGAN 6B，轻量）"},
        {"name": "4x-UltraSharp.pth", "note": "4x｜通用锐利（UltraSharp）"},
        {"name": "4x-UltraSharpV2.safetensors", "note": "4x｜通用锐利（UltraSharp V2）"},
        {"name": "4x-AnimeSharp.pth", "note": "4x｜动漫（AnimeSharp）"},
        {"name": "4x-ClearRealityV1.pth", "note": "4x｜清晰/写实（ClearReality）"},
        {"name": "4x_foolhardy_Remacri.pth", "note": "4x｜通用（Remacri）"},
        {"name": "4x_NickelbackFS_72000_G.pth", "note": "4x｜通用（Nickelback）"},
        {"name": "4x_NMKD-Siax_200k.pth", "note": "4x｜通用（NMKD Siax）"},
        {"name": "ESRGAN_4x.pth", "note": "4x｜老牌通用（ESRGAN）"},
    ),
}


class OptionStore:
    """通用配置项存储。单线程事件循环使用。"""

    def __init__(self, data_dir: Path) -> None:
        self.data_dir = Path(data_dir)
        try:
            self.data_dir.mkdir(parents=True, exist_ok=True)
        except Exception:
            pass
        self.db_path = self.data_dir / "option.db"
        self._conn = None
        self._init_db()

    # ------------------------------------------------------------------ #
    def _conn_get(self):
        if self._conn is None:
            self._conn = sqlite3.connect(str(self.db_path))
            self._conn.row_factory = sqlite3.Row
            try:
                self._conn.execute("PRAGMA journal_mode=WAL")
                self._conn.execute("PRAGMA synchronous=NORMAL")
            except Exception as e:  # pragma: no cover
                logger.warning(f"[配置项] 开启 WAL 失败（不影响使用）: {e}")
        return self._conn

    def _ensure_columns(self, table: str, cols: "dict[str, str]") -> None:
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
                except Exception as e:
                    if "duplicate column" not in str(e).lower():
                        logger.warning(f"[配置项] 迁移补列 {table}.{_col} 失败: {e}")
            conn.commit()
        except Exception as e:
            logger.warning(f"[配置项] 迁移检查失败（{table}）: {e}")

    def _init_db(self) -> None:
        conn = self._conn_get()
        conn.execute(
            """CREATE TABLE IF NOT EXISTS options (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                kind       TEXT NOT NULL,
                name       TEXT NOT NULL,
                note       TEXT DEFAULT '',
                enabled    INTEGER NOT NULL DEFAULT 1,
                sort_order INTEGER NOT NULL DEFAULT 0,
                created_at REAL NOT NULL DEFAULT 0,
                updated_at REAL NOT NULL DEFAULT 0
            )"""
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_options_kind ON options(kind)"
        )
        conn.commit()
        self._ensure_columns("options", {
            "note": "TEXT DEFAULT ''",
            "enabled": "INTEGER NOT NULL DEFAULT 1",
            "sort_order": "INTEGER NOT NULL DEFAULT 0",
        })
        self._seed_defaults()

    # ------------------------------------------------------------------ #
    @staticmethod
    def _row_to_dict(row) -> dict:
        d = dict(row)
        d["enabled"] = bool(d.get("enabled", 1))
        return d

    def list_all(self, kind: str = "", only_enabled: bool = False) -> list[dict]:
        sql = "SELECT * FROM options"
        args: list = []
        conds = []
        if kind:
            conds.append("kind=?")
            args.append(kind)
        if only_enabled:
            conds.append("enabled=1")
        if conds:
            sql += " WHERE " + " AND ".join(conds)
        sql += " ORDER BY kind ASC, sort_order ASC, id ASC"
        rows = self._conn_get().execute(sql, args).fetchall()
        return [self._row_to_dict(r) for r in rows]

    def save(self, data: dict) -> tuple[int | None, str | None]:
        kind = (data.get("kind") or "").strip()
        name = (data.get("name") or "").strip()
        if not kind:
            return None, "缺少分类（kind）"
        if not name:
            return None, "名称不能为空"
        note = (data.get("note") or "").strip()
        enabled = 0 if data.get("enabled") is False else 1
        now = time.time()
        conn = self._conn_get()
        mid = data.get("id")
        try:
            if mid:
                conn.execute(
                    """UPDATE options SET kind=?, name=?, note=?, enabled=?, updated_at=?
                       WHERE id=?""",
                    (kind, name, note, enabled, now, int(mid)),
                )
                conn.commit()
                return int(mid), None
            row = conn.execute(
                "SELECT id FROM options WHERE kind=? AND name=? COLLATE NOCASE",
                (kind, name),
            ).fetchone()
            if row:
                conn.execute(
                    "UPDATE options SET note=?, enabled=?, updated_at=? WHERE id=?",
                    (note, enabled, now, row["id"]),
                )
                conn.commit()
                return int(row["id"]), None
            cur = conn.execute(
                """INSERT INTO options (kind, name, note, enabled, sort_order, created_at, updated_at)
                   VALUES (?,?,?,?,?,?,?)""",
                (kind, name, note, enabled, int(data.get("sort_order") or 0), now, now),
            )
            conn.commit()
            return int(cur.lastrowid), None
        except Exception as e:
            return None, str(e)

    def delete(self, opt_id: int) -> str | None:
        try:
            conn = self._conn_get()
            conn.execute("DELETE FROM options WHERE id=?", (int(opt_id),))
            conn.commit()
            return None
        except Exception as e:
            return str(e)

    # ------------------------------------------------------------------ #
    def _seed_defaults(self) -> int:
        """空库时写入预设（按 kind 判断：该 kind 一条都没有才播）。"""
        added = 0
        for kind, items in DEFAULT_OPTIONS.items():
            try:
                n = self._conn_get().execute(
                    "SELECT COUNT(*) AS c FROM options WHERE kind=?", (kind,)
                ).fetchone()["c"]
            except Exception:
                continue
            if n:
                continue
            for it in items:
                _, err = self.save({"kind": kind, **it})
                if not err:
                    added += 1
        if added:
            logger.info(f"【配置项】 已写入 {added} 条预设（放大模型等）")
        return added

    def reseed_defaults(self, kind: str = "") -> int:
        """补齐预设（按名称判断，已存在的不动）。kind 留空=全部预设分类。"""
        kinds = (kind,) if kind else tuple(DEFAULT_OPTIONS.keys())
        added = 0
        for k in kinds:
            have = {
                str(r.get("name") or "").strip().lower()
                for r in self.list_all(k)
            }
            for it in DEFAULT_OPTIONS.get(k, ()):
                if str(it["name"]).strip().lower() in have:
                    continue
                _, err = self.save({"kind": k, **it})
                if not err:
                    added += 1
        if added:
            logger.info(f"【配置项】 已补齐 {added} 条预设")
        return added

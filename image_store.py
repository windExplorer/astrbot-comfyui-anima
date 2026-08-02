"""SQLite 图库与语义标签召回。

把生成的成品图、图生图参考图、用户在聊天里发来/收藏的图，按内容寻址
(sha256[:16]) 永久归档到 data_dir 下的 gallery/YYYY-MM/ 与 refs/YYYY-MM/，
并写入 SQLite（gallery.db）便于检索。

关键设计：
- 成品图 / 参考图 / 用户收藏图都按内容寻址落盘，重复内容不重复占用空间。
- 语义标签（多对多）存在 image_tags 表，用于「把我们的合照发我」这类召回。
- 模型永远不直接接触本地路径：插件用 event.send(Image(file=绝对路径)) 代发。
- 保留策略：超 max_total_mb 时按 LRU 淘汰，收藏 / 带标签图永不淘汰。
"""

import hashlib
import os
import time
from pathlib import Path

try:
    import sqlite3

    _HAS_SQLITE = True
except Exception:  # pragma: no cover - 极老环境降级
    _HAS_SQLITE = False

# 内容寻址文件名取 sha256 前多少位
_SHA_PREFIX = 16

# source 取值
SRC_GEN = "gen"  # 本插件生图成品
SRC_REF = "ref"  # 图生图参考图
SRC_USER = "user"  # 用户发来/收藏的图


def _sha256_of(path: str) -> str | None:
    """计算文件 sha256（完整 64 位十六进制）。失败返回 None。"""
    h = hashlib.sha256()
    try:
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(65536), b""):
                h.update(chunk)
        return h.hexdigest()
    except OSError:
        return None


def _ext_of(path: str) -> str:
    """取扩展名（不含点，小写；无扩展名按 png 处理）。"""
    ext = os.path.splitext(path)[1].lower()
    if ext:
        return ext[1:]
    return "png"


class ImageStore:
    """图库存储与检索。线程不安全但本插件为单线程事件循环，足够。"""

    def __init__(self, data_dir: Path, cfg: dict | None = None) -> None:
        self.data_dir = Path(data_dir)
        self.gallery_dir = self.data_dir / "gallery"
        self.refs_dir = self.data_dir / "refs"
        self.gallery_dir.mkdir(parents=True, exist_ok=True)
        self.refs_dir.mkdir(parents=True, exist_ok=True)
        self.db_path = self.data_dir / "gallery.db"
        self.cfg = cfg or {}
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
        if not _HAS_SQLITE:
            logger.warning("[图库] 环境无 sqlite3，图库功能不可用")
            return
        try:
            conn = self._conn_get()
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS images (
                    sha256     TEXT PRIMARY KEY,
                    ext        TEXT NOT NULL DEFAULT 'png',
                    month      TEXT NOT NULL DEFAULT '',
                    prompt     TEXT NOT NULL DEFAULT '',
                    prompt_raw TEXT NOT NULL DEFAULT '',
                    workflow   TEXT NOT NULL DEFAULT '',
                    loras      TEXT NOT NULL DEFAULT '',
                    seed       INTEGER,
                    w          INTEGER,
                    h          INTEGER,
                    denoise    REAL,
                    is_img2img INTEGER NOT NULL DEFAULT 0,
                    ref_sha256 TEXT NOT NULL DEFAULT '',
                    source     TEXT NOT NULL DEFAULT 'gen',
                    use_count  INTEGER NOT NULL DEFAULT 0,
                    starred    INTEGER NOT NULL DEFAULT 0,
                    created_at REAL NOT NULL DEFAULT 0
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS image_tags (
                    sha256 TEXT NOT NULL,
                    tag     TEXT NOT NULL,
                    PRIMARY KEY (sha256, tag)
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_images_month ON images(month)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_tags_tag ON image_tags(tag)"
            )
            conn.commit()
        except Exception as e:  # pragma: no cover
            logger.error(f"[图库] 初始化数据库失败: {e}", exc_info=True)

    def close(self) -> None:
        if self._conn is not None:
            try:
                self._conn.close()
            except Exception:
                pass
            self._conn = None

    # ------------------------------------------------------------------ #
    # 配置辅助
    # ------------------------------------------------------------------ #
    def _cfg(self, key: str, default=None):
        try:
            val = self.cfg.get(key, default)
        except Exception:
            val = default
        return val if val is not None else default

    def enabled(self) -> bool:
        return bool(self._cfg("enabled", True))

    # ------------------------------------------------------------------ #
    # 内部：落盘 + 拼路径
    # ------------------------------------------------------------------ #
    def _path_for(self, sha256: str, ext: str, source: str) -> Path:
        """根据 sha256 与 source 拼出永久归档路径。

        成品图(gen) 进 gallery/，参考图(ref) 与用户收藏图(user) 进 refs/。
        按创建月份分子目录，便于 LRU 清理。
        """
        month = time.strftime("%Y-%m")
        if source == SRC_GEN:
            base = self.gallery_dir / month
        else:
            base = self.refs_dir / month
        base.mkdir(parents=True, exist_ok=True)
        return base / f"{sha256[:_SHA_PREFIX]}.{ext}"

    def _path_of_row(self, row) -> Path:
        """由数据库行拼回绝对路径（不校验存在性）。"""
        month = row["month"] or time.strftime("%Y-%m")
        if row["source"] == SRC_GEN:
            base = self.gallery_dir / month
        else:
            base = self.refs_dir / month
        return base / f"{row['sha256'][:_SHA_PREFIX]}.{row['ext']}"

    # ------------------------------------------------------------------ #
    # 归档
    # ------------------------------------------------------------------ #
    def archive_image(
        self,
        src_path: str,
        *,
        source: str = SRC_GEN,
        prompt: str = "",
        prompt_raw: str = "",
        workflow: str = "",
        loras=None,
        seed=None,
        w=None,
        h=None,
        denoise=None,
        is_img2img: bool = False,
        ref_sha256: str = "",
    ) -> str | None:
        """归档一张图（移动转正，内容寻址去重）。返回 sha256 或 None。

        - 先算 sha256，已存在则只补元数据 / 计数，不重复落盘；
        - 不存在则把 src_path 移动（os.replace）到 gallery/ 或 refs/ 永久目录；
          跨盘移动失败则回退为复制。
        """
        if not self.enabled():
            return None
        if not _HAS_SQLITE:
            return None
        if not src_path or not os.path.exists(src_path):
            logger.warning(f"[图库] 归档失败：源文件不存在 {src_path}")
            return None
        sha = _sha256_of(src_path)
        if not sha:
            return None

        conn = self._conn_get()
        cur = conn.execute("SELECT * FROM images WHERE sha256=?", (sha,))
        row = cur.fetchone()

        loras_json = ""
        if loras:
            try:
                loras_json = json.dumps(list(loras), ensure_ascii=False)
            except Exception:
                loras_json = ""

        if row is not None:
            # 已存在：更新计数，并在缺字段时补齐（如从 ref 升级为带 prompt 的成品）
            try:
                conn.execute(
                    "UPDATE images SET use_count=use_count+1 WHERE sha256=?", (sha,)
                )
                # 若之前是 ref/user 缺 prompt，本次是 gen 则补全
                if source == SRC_GEN and not row["prompt"] and prompt:
                    conn.execute(
                        "UPDATE images SET prompt=?, prompt_raw=?, workflow=?, "
                        "loras=?, seed=?, w=?, h=?, denoise=?, is_img2img=?, "
                        "ref_sha256=?, source=? WHERE sha256=?",
                        (
                            prompt, prompt_raw, workflow, loras_json,
                            seed, w, h, denoise,
                            1 if is_img2img else 0, ref_sha256 or "",
                            source, sha,
                        ),
                    )
                conn.commit()
            except Exception as e:
                logger.warning(f"[图库] 更新已存在记录失败: {e}")
            return sha

        # 新图：落盘（移动转正）
        ext = _ext_of(src_path)
        dest = self._path_for(sha, ext, source)
        try:
            os.replace(src_path, dest)
        except OSError:
            # 跨盘或权限问题：回退复制
            try:
                import shutil
                shutil.copy2(src_path, dest)
            except Exception as e:
                logger.error(f"[图库] 落盘失败: {e}", exc_info=True)
                return None

        try:
            conn.execute(
                """
                INSERT INTO images
                (sha256, ext, month, prompt, prompt_raw, workflow, loras,
                 seed, w, h, denoise, is_img2img, ref_sha256, source,
                 use_count, starred, created_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,1,0,?)
                """,
                (
                    sha, ext, (dest.parent.name or time.strftime("%Y-%m")),
                    prompt, prompt_raw, workflow, loras_json,
                    seed, w, h, denoise,
                    1 if is_img2img else 0, ref_sha256 or "", source,
                    time.time(),
                ),
            )
            conn.commit()
        except Exception as e:
            logger.error(f"[图库] 写库失败: {e}", exc_info=True)
            return None
        logger.info(f"[图库] 已归档 {source} 图: {dest.name}")
        return sha

    def archive_user_image(self, src_path: str, tags=None) -> str | None:
        """方案 B：收藏用户在聊天里发来的图（或任意来源图）到 refs/。"""
        sha = self.archive_image(src_path, source=SRC_USER)
        if sha and tags:
            self.add_tags(sha, tags)
        return sha

    # ------------------------------------------------------------------ #
    # 标签
    # ------------------------------------------------------------------ #
    def add_tags(self, sha256: str, tags: list[str]) -> None:
        if not sha256 or not tags:
            return
        conn = self._conn_get()
        try:
            conn.executemany(
                "INSERT OR IGNORE INTO image_tags (sha256, tag) VALUES (?, ?)",
                [(sha256, t.strip()) for t in tags if t and t.strip()],
            )
            conn.commit()
        except Exception as e:
            logger.warning(f"[图库] 打标签失败: {e}")

    def remove_tags(self, sha256: str, tags: list[str]) -> None:
        if not sha256 or not tags:
            return
        conn = self._conn_get()
        try:
            conn.executemany(
                "DELETE FROM image_tags WHERE sha256=? AND tag=?",
                [(sha256, t.strip()) for t in tags if t and t.strip()],
            )
            conn.commit()
        except Exception as e:
            logger.warning(f"[图库] 删标签失败: {e}")

    def tags_of(self, sha256: str) -> list[str]:
        if not sha256:
            return []
        conn = self._conn_get()
        try:
            rows = conn.execute(
                "SELECT tag FROM image_tags WHERE sha256=?", (sha256,)
            ).fetchall()
            return [r["tag"] for r in rows]
        except Exception:
            return []

    # ------------------------------------------------------------------ #
    # 检索 / 召回
    # ------------------------------------------------------------------ #
    def _row_to_dict(self, row) -> dict:
        return {
            "sha256": row["sha256"],
            "sha16": row["sha256"][:_SHA_PREFIX],
            "ext": row["ext"],
            "month": row["month"],
            "prompt": row["prompt"],
            "prompt_raw": row["prompt_raw"],
            "workflow": row["workflow"],
            "loras": row["loras"],
            "seed": row["seed"],
            "w": row["w"],
            "h": row["h"],
            "denoise": row["denoise"],
            "is_img2img": bool(row["is_img2img"]),
            "ref_sha256": row["ref_sha256"],
            "source": row["source"],
            "use_count": row["use_count"],
            "starred": bool(row["starred"]),
            "created_at": row["created_at"],
            "tags": self.tags_of(row["sha256"]),
        }

    def search(
        self,
        keyword: str = "",
        type: str | None = None,
        session=None,
        starred_only: bool = False,
        limit: int = 20,
        offset: int = 0,
    ) -> list[dict]:
        """按 prompt LIKE 检索（中文优先）。type: gen/ref/user/None(全部)。"""
        if not self.enabled() or not _HAS_SQLITE:
            return []
        conn = self._conn_get()
        sql = "SELECT * FROM images WHERE 1=1"
        args: list = []
        if keyword and keyword.strip():
            kw = f"%{keyword.strip()}%"
            sql += (
                " AND (prompt LIKE ? OR prompt_raw LIKE ? OR workflow LIKE ?"
                " OR sha256 IN (SELECT sha256 FROM image_tags WHERE tag LIKE ?))"
            )
            args += [kw, kw, kw, kw]
        if type:
            sql += " AND source=?"
            args.append(type)
        if starred_only:
            sql += " AND starred=1"
        sql += " ORDER BY created_at DESC LIMIT ? OFFSET ?"
        args.append(int(limit))
        args.append(int(offset))
        try:
            rows = conn.execute(sql, args).fetchall()
        except Exception as e:
            logger.warning(f"[图库] 检索失败: {e}")
            return []
        return [self._row_to_dict(r) for r in rows]

    def recall_by_tag(self, tag: str, limit: int = 20) -> list[dict]:
        """按语义标签召回。命中多张返回列表（由调用方列出让用户选）。"""
        if not self.enabled() or not _HAS_SQLITE or not tag or not tag.strip():
            return []
        conn = self._conn_get()
        kw = f"%{tag.strip()}%"
        try:
            rows = conn.execute(
                """
                SELECT i.* FROM images i
                JOIN image_tags t ON i.sha256 = t.sha256
                WHERE t.tag LIKE ?
                ORDER BY i.created_at DESC LIMIT ?
                """,
                (kw, int(limit)),
            ).fetchall()
        except Exception as e:
            logger.warning(f"[图库] 标签召回失败: {e}")
            return []
        return [self._row_to_dict(r) for r in rows]

    def resolve_ref(self, event, session: str | None = None) -> str | None:
        """指代消解：找出当前语境下「这张图」对应的本地路径。

        注意：当前消息里的图需要 await _extract_images(event)，无法在同步方法里
        完成。因此本方法只做「2) 本会话最近生成的图 / 3) 本会话最近收到的图」的
        查找；第 1 优先级的「上一条消息的图」由 main.py 的 _gallery_resolve_ref
        （async 包装）先 await 提取当前消息图片，再调用本方法兜底。
        返回本地绝对路径，找不到返回 None。
        """
        sid = session or (getattr(event, "session_id", "") or "")
        try:
            from main import g_last_generated, g_last_received
        except Exception:
            try:
                from .main import g_last_generated, g_last_received
            except Exception:
                g_last_generated = {}
                g_last_received = {}
        # 2) 本会话最近生成的图
        gen = g_last_generated.get(sid) if g_last_generated else None
        if gen and os.path.exists(gen):
            return gen
        # 3) 本会话最近收到的图
        recv = g_last_received.get(sid) if g_last_received else None
        if recv and os.path.exists(recv):
            return recv
        return None

    # ------------------------------------------------------------------ #
    # 操作
    # ------------------------------------------------------------------ #
    def _row(self, sha256: str):
        conn = self._conn_get()
        try:
            return conn.execute(
                "SELECT * FROM images WHERE sha256=?", (sha256,)
            ).fetchone()
        except Exception:
            return None

    def path_of(self, sha256_or_prefix: str) -> str | None:
        """根据完整 sha256 或前 16 位前缀，返回存在的本地路径。"""
        if not sha256_or_prefix:
            return None
        conn = self._conn_get()
        try:
            row = conn.execute(
                "SELECT * FROM images WHERE sha256 LIKE ? ORDER BY created_at DESC LIMIT 1",
                (sha256_or_prefix + "%",),
            ).fetchone()
        except Exception:
            return None
        if not row:
            return None
        p = self._path_of_row(row)
        if p.exists():
            return str(p)
        # 文件丢失：尝试跨月份找回（同一 sha256_prefix 不同 month）
        prefix = row["sha256"][:_SHA_PREFIX]
        for d in (self.gallery_dir, self.refs_dir):
            if not d.exists():
                continue
            for sub in d.iterdir():
                cand = sub / f"{prefix}.{row['ext']}"
                if cand.exists():
                    return str(cand)
        return None

    def send(self, sha256: str) -> bool:
        """标记已发送（use_count += 1）。返回路径是否可达。"""
        if not sha256:
            return False
        conn = self._conn_get()
        try:
            conn.execute(
                "UPDATE images SET use_count=use_count+1 WHERE sha256 LIKE ?",
                (sha256 + "%",),
            )
            conn.commit()
        except Exception as e:
            logger.warning(f"[图库] 发送计数失败: {e}")
        return self.path_of(sha256) is not None

    def star(self, sha256: str, on: int = 1) -> bool:
        if not sha256:
            return False
        conn = self._conn_get()
        try:
            conn.execute(
                "UPDATE images SET starred=? WHERE sha256 LIKE ?",
                (1 if on else 0, sha256 + "%"),
            )
            conn.commit()
            return True
        except Exception as e:
            logger.warning(f"[图库] 收藏失败: {e}")
            return False

    def delete(self, sha256: str) -> bool:
        if not sha256:
            return False
        conn = self._conn_get()
        row = self._row(sha256)
        if not row:
            return False
        # 收藏图不允许删除
        if row["starred"]:
            return False
        p = self._path_of_row(row)
        try:
            if p.exists():
                p.unlink()
        except OSError as e:
            logger.warning(f"[图库] 删除文件失败: {e}")
        try:
            conn.execute("DELETE FROM image_tags WHERE sha256=?", (row["sha256"],))
            conn.execute(
                "DELETE FROM images WHERE sha256=?", (row["sha256"],)
            )
            conn.commit()
            return True
        except Exception as e:
            logger.warning(f"[图库] 删除记录失败: {e}")
            return False

    def stats(self) -> dict:
        if not _HAS_SQLITE:
            return {"enabled": False}
        conn = self._conn_get()
        try:
            total = conn.execute("SELECT COUNT(*) c FROM images").fetchone()["c"]
            starred = conn.execute(
                "SELECT COUNT(*) c FROM images WHERE starred=1"
            ).fetchone()["c"]
            tagged = conn.execute(
                "SELECT COUNT(DISTINCT sha256) c FROM image_tags"
            ).fetchone()["c"]
            gen = conn.execute(
                "SELECT COUNT(*) c FROM images WHERE source=?", (SRC_GEN,)
            ).fetchone()["c"]
            ref = conn.execute(
                "SELECT COUNT(*) c FROM images WHERE source=?", (SRC_REF,)
            ).fetchone()["c"]
            user = conn.execute(
                "SELECT COUNT(*) c FROM images WHERE source=?", (SRC_USER,)
            ).fetchone()["c"]
        except Exception as e:
            logger.warning(f"[图库] 统计失败: {e}")
            return {"enabled": self.enabled()}
        # 计算占用空间（仅统计 gallery/ 与 refs/）
        size = 0
        for d in (self.gallery_dir, self.refs_dir):
            if not d.exists():
                continue
            for f in d.rglob("*"):
                if f.is_file():
                    size += f.stat().st_size
        return {
            "enabled": self.enabled(),
            "total": total,
            "starred": starred,
            "tagged": tagged,
            "gen": gen,
            "ref": ref,
            "user": user,
            "size_mb": round(size / 1024 / 1024, 2),
            "max_total_mb": int(self._cfg("max_total_mb", 2048)),
        }

    # ------------------------------------------------------------------ #
    # LRU 淘汰
    # ------------------------------------------------------------------ #
    def enforce_lru(self) -> int:
        """超 max_total_mb 时按创建时间升序淘汰非收藏、无标签图。返回删除数量。"""
        if not _HAS_SQLITE:
            return 0
        max_mb = int(self._cfg("max_total_mb", 2048))
        conn = self._conn_get()
        try:
            # 计算当前总大小
            size = 0
            for d in (self.gallery_dir, self.refs_dir):
                if not d.exists():
                    continue
                for f in d.rglob("*"):
                    if f.is_file():
                        size += f.stat().st_size
            max_bytes = max_mb * 1024 * 1024
            if size <= max_bytes:
                return 0
            # 取可淘汰的图（starred=0 且无标签），按 created_at 升序
            rows = conn.execute(
                """
                SELECT i.* FROM images i
                WHERE i.starred=0
                  AND NOT EXISTS (SELECT 1 FROM image_tags t WHERE t.sha256=i.sha256)
                ORDER BY i.created_at ASC
                """
            ).fetchall()
        except Exception as e:
            logger.warning(f"[图库] LRU 预检失败: {e}")
            return 0

        removed = 0
        for row in rows:
            if size <= max_bytes:
                break
            p = self._path_of_row(row)
            try:
                if p.exists():
                    sz = p.stat().st_size
                    p.unlink()
                    size -= sz
            except OSError:
                pass
            try:
                conn.execute(
                    "DELETE FROM images WHERE sha256=?", (row["sha256"],)
                )
                removed += 1
            except Exception:
                pass
        try:
            conn.commit()
        except Exception:
            pass
        if removed:
            logger.info(f"[图库] LRU 淘汰 {removed} 张图（容量 {max_mb}MB 上限）")
        return removed


# 顶层 logger（与 main.py 共用 astrbot.api.logger）
try:
    from astrbot.api import logger  # noqa
except Exception:  # pragma: no cover
    import logging  # type: ignore

    logger = logging.getLogger("astrbot_plugin_comfyui_anima")

# 延迟导入 json，避免与模块顶层其它 import 顺序冲突
import json  # noqa: E402

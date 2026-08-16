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

import base64
import hashlib
import json
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

# 插件名（与 metadata.yaml / 路由前缀一致），用于拼图片访问 URL
PLUGIN_NAME = "astrbot_plugin_comfyui_anima"

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
                    created_at REAL NOT NULL DEFAULT 0,
                    size_bytes INTEGER DEFAULT NULL,
                    cost_sec   REAL DEFAULT NULL,
                    user_id    TEXT DEFAULT NULL,
                    user_name  TEXT DEFAULT NULL,
                    trigger_msg TEXT DEFAULT NULL,
                    status     INTEGER NOT NULL DEFAULT 0,
                    deleted    INTEGER NOT NULL DEFAULT 0,
                    deleted_at REAL DEFAULT NULL,
                    is_public  INTEGER NOT NULL DEFAULT 0,
                    session_id TEXT DEFAULT ''
                )
                """
            )
            # 兼容已存在的旧库：缺列则补上
            for _col, _type in (
                ("size_bytes", "INTEGER"),
                ("cost_sec", "REAL"),
                ("user_id", "TEXT"),
                ("user_name", "TEXT"),
                ("trigger_msg", "TEXT"),
                ("status", "INTEGER NOT NULL DEFAULT 0"),
                ("deleted", "INTEGER NOT NULL DEFAULT 0"),
                ("deleted_at", "REAL DEFAULT NULL"),
                ("is_public", "INTEGER NOT NULL DEFAULT 0"),
                ("session_id", "TEXT DEFAULT ''"),
            ):
                try:
                    conn.execute(f"ALTER TABLE images ADD COLUMN {_col} {_type}")
                except Exception:
                    pass
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
                "CREATE INDEX IF NOT EXISTS idx_images_created ON images(created_at)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_images_session ON images(session_id)"
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
        size_bytes: int = None,
        cost_sec: float = None,
        user_id: str = "",
        user_name: str = "",
        session_id: str = "",
        trigger_msg: str = "",
        status: int = 0,
    ) -> str | None:
        """归档一张图（移动转正，内容寻址去重）。

        返回**归档后文件的最终绝对路径**（落盘后的 gallery/refs 路径，或命中去重时
        已存在文件的路径）；归档不可用/失败时返回 None。注意：本方法会把 src_path
        **移动**到永久目录，因此调用方必须用返回值作为后续发送/上报所用的路径，
        不要再使用已被移动的旧 src_path。
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

        # 去重命中：返回已存在文件的真实路径（它才是可被发送/读取的成品）
        if row is not None:
            try:
                _existing = self.path_of(sha)
                if _existing:
                    return _existing
            except Exception:
                pass

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
                        "ref_sha256=?, source=?, size_bytes=?, cost_sec=?, "
                        "user_id=?, user_name=?, session_id=?, trigger_msg=?, status=? "
                        "WHERE sha256=?",
                        (
                            prompt, prompt_raw, workflow, loras_json,
                            seed, w, h, denoise,
                            1 if is_img2img else 0, ref_sha256 or "",
                            source, size_bytes, cost_sec,
                            user_id or "", user_name or "", session_id or "",
                            trigger_msg or "", status, sha,
                        ),
                    )
                conn.commit()
            except Exception as e:
                logger.warning(f"[图库] 更新已存在记录失败: {e}")
            # 去重：文件已在永久目录，返回其真实路径
            try:
                _existing = self.path_of(sha)
                if _existing:
                    return _existing
            except Exception:
                pass
            return src_path

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
                 use_count, starred, created_at, size_bytes, cost_sec,
                 user_id, user_name, session_id, trigger_msg, status)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,1,0,?,?,?,?,?,?,?,?)
                """,
                (
                    sha, ext, (dest.parent.name or time.strftime("%Y-%m")),
                    prompt, prompt_raw, workflow, loras_json,
                    seed, w, h, denoise,
                    1 if is_img2img else 0, ref_sha256 or "", source,
                    time.time(), size_bytes, cost_sec,
                    user_id or "", user_name or "", session_id or "", trigger_msg or "", status,
                ),
            )
            conn.commit()
        except Exception as e:
            logger.error(f"[图库] 写库失败: {e}", exc_info=True)
            return None
        logger.info(f"[图库] 已归档 {source} 图: {dest.name}")
        return str(dest)

    def archive_user_image(self, src_path: str, tags=None, user_id: str = "", user_name: str = "", session_id: str = "") -> str | None:
        """方案 B：收藏用户在聊天里发来的图（或任意来源图）到 refs/。返回 sha256。
        必须传 user_id，否则会成为"无主图"串给其他用户。"""
        _final = self.archive_image(src_path, source=SRC_USER, user_id=user_id, user_name=user_name, session_id=session_id)
        if not _final:
            return None
        # 从最终路径反算 sha（与归档时一致），供调用方做收藏/召回标识。
        sha = _sha256_of(_final)
        if sha and tags:
            self.add_tags(sha, tags)
        return sha

    def add_failed_record(
        self,
        *,
        prompt: str = "",
        prompt_raw: str = "",
        workflow: str = "",
        is_img2img: bool = False,
        ref_sha256: str = "",
        size_bytes: int = None,
        cost_sec: float = None,
        user_id: str = "",
        user_name: str = "",
        trigger_msg: str = "",
        reason: str = "",
    ) -> None:
        """写入一条**出图失败**记录（status=1，无真实文件，用随机 sha 占位）。

        用于「出图记录」视图展示失败情况：哪个用户、发了什么消息、耗时、原因等。
        """
        if not self.enabled() or not _HAS_SQLITE:
            return
        sha = "fail_" + hashlib.sha256(
            (reason + str(time.time())).encode("utf-8")
        ).hexdigest()[:_SHA_PREFIX]
        conn = self._conn_get()
        try:
            conn.execute(
                """
                INSERT OR REPLACE INTO images
                (sha256, ext, month, prompt, prompt_raw, workflow, loras,
                 seed, w, h, denoise, is_img2img, ref_sha256, source,
                 use_count, starred, created_at, size_bytes, cost_sec,
                 user_id, user_name, trigger_msg, status)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,1,0,?,?,?,?,?,?,1)
                """,
                (
                    sha, "fail", time.strftime("%Y-%m"),
                    prompt, prompt_raw, workflow, "",
                    None, None, None, None,
                    1 if is_img2img else 0, ref_sha256 or "", SRC_GEN,
                    time.time(), size_bytes, cost_sec,
                    user_id or "", user_name or "", (trigger_msg or "") + (f" | 失败: {reason}" if reason else ""),
                ),
            )
            conn.commit()
        except Exception as e:
            logger.warning(f"[图库] 写入失败记录出错: {e}")

    def count_records(self, only_failed: bool = False, keyword: str = "") -> int:
        """出图记录总条数（用于 WebUI 分页显示 total）。keyword 非空时按用户/消息/提示词模糊匹配。"""
        if not self.enabled() or not _HAS_SQLITE:
            return 0
        conn = self._conn_get()
        try:
            sql = "SELECT COUNT(*) AS c FROM images WHERE 1=1" + (
                " AND status=1" if only_failed else ""
            )
            kw = (keyword or "").strip()
            params: list = []
            if kw:
                like = f"%{kw}%"
                sql += " AND (user_id LIKE ? OR user_name LIKE ? OR trigger_msg LIKE ? OR prompt LIKE ?)"
                params.extend([like, like, like, like])
            row = conn.execute(sql, tuple(params)).fetchone()
            return int(row["c"]) if row else 0
        except Exception as e:
            logger.warning(f"[图库] 记录计数失败: {e}")
            return 0

    def recent_records(self, limit: int = 200, only_failed: bool = False,
                       offset: int = 0, keyword: str = "") -> list[dict]:
        """出图记录（用于 WebUI「日志」页）。返回含用户/消息/尺寸/大小/耗时/状态的结构化记录。
        支持 offset 分页（配合 count_records）；keyword 非空时按用户/消息/提示词模糊匹配。

        失败记录 ext='fail'，前端据此判断无缩略图。
        """
        if not self.enabled() or not _HAS_SQLITE:
            return []
        conn = self._conn_get()
        try:
            sql = (
                "SELECT * FROM images WHERE 1=1"
                + (" AND status=1" if only_failed else "")
            )
            kw = (keyword or "").strip()
            params: list = []
            if kw:
                like = f"%{kw}%"
                sql += " AND (user_id LIKE ? OR user_name LIKE ? OR trigger_msg LIKE ? OR prompt LIKE ?)"
                params.extend([like, like, like, like])
            sql += " ORDER BY created_at DESC LIMIT ? OFFSET ?"
            params.extend([int(limit), int(offset)])
            rows = conn.execute(sql, tuple(params)).fetchall()
            out = []
            for r in rows:
                d = self._row_to_dict(r)
                if r["ext"] != "fail":
                    # 返回图片 URL（前端 <img src> 懒加载），不再内联 base64，
                    # 避免出图记录一多响应体爆炸导致超时。
                    sha = r["sha256"]
                    d["thumb_url"] = f"/{PLUGIN_NAME}/gallery/image?sha={sha}" if sha else ""
                    d["data_url"] = None
                else:
                    d["thumb_url"] = ""
                    d["data_url"] = None
                out.append(d)
            return out
        except Exception as e:
            logger.warning(f"[图库] 读取出图记录失败: {e}")
            return []

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

    def sha_of(self, path: str) -> str | None:
        """计算文件内容 sha256（完整 64 位）。用于对引用图内容寻址定位图库记录。"""
        return _sha256_of(path)

    def set_visibility(self, sha256: str, is_public: bool) -> bool:
        """设置图片可见性：is_public=True 公开（他人可检索/发送），False 私有（仅本人）。
        返回是否成功。"""
        if not self.enabled() or not _HAS_SQLITE:
            return False
        conn = self._conn_get()
        try:
            cur = conn.execute(
                "UPDATE images SET is_public=? WHERE sha256 LIKE ?",
                (1 if is_public else 0, sha256 + "%"),
            )
            conn.commit()
            return cur.rowcount > 0
        except Exception as e:
            logger.warning(f"[图库] 设置可见性失败: {e}")
            return False

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
            "size_bytes": row["size_bytes"],
            "cost_sec": row["cost_sec"],
            "user_id": row["user_id"],
            "user_name": row["user_name"],
            "session_id": row["session_id"] if "session_id" in row.keys() else "",
            "trigger_msg": row["trigger_msg"],
            "status": row["status"],
            "deleted": bool(row["deleted"]),
            "deleted_at": row["deleted_at"],
            "is_public": bool(row["is_public"]),
            "tags": self.tags_of(row["sha256"]),
        }

    def _gidx_rank(
        self,
        conn,
        created_at: float,
        sha256: str,
        owner: str = "",
        session=None,
        trash: bool = False,
    ) -> int:
        """计算某条记录在「基础过滤」下的全局序号（1 起始，用于 gidx）。

        「基础过滤」= deleted/status + owner(user_id)，**不含** session/keyword/tag。
        排序与 get_by_global_no 完全一致（created_at DESC, sha256 DESC 作为次级键），
        因此算出的编号不依赖当前检索范围（会话）或关键词，可直接作为图库唯一编号，
        供 `/图库 取图 <编号>` 或 comfyui_gallery send 无状态定位到同一张图。

        说明：session 不参与编号——权限隔离核心是 owner（user_id），同一用户在不同
        会话生成的图本就允许本人取用；编号只保证「同一 owner 下的全局唯一位置」，
        与 recall（无 session）和 search（带 session）的列表口径统一。
        """
        where = "deleted=1" if trash else "deleted=0"
        args: list = [1] if trash else []
        where += " AND status=0"
        if owner:
            where += " AND (is_public=1 OR user_id=?)"
            args.append(owner)
        # 计算「按 created_at DESC, sha256 DESC 排序时排在该条之前」的条数
        where += " AND (created_at > ? OR (created_at = ? AND sha256 > ?))"
        args += [created_at, created_at, sha256]
        try:
            row = conn.execute(
                f"SELECT COUNT(*) AS c FROM images WHERE {where}", args
            ).fetchone()
            return int(row["c"]) + 1 if row else 1
        except Exception as e:
            logger.warning(f"[图库] 计算全局序号失败: {e}")
            return 1

    def count_search(
        self,
        keyword: str = "",
        type: str | None = None,
        starred_only: bool = False,
        trash: bool = False,
        owner: str = "",
        session=None,
        user_filter: str = "",
    ) -> int:
        """与 search 相同的过滤条件，返回命中的总条数（用于 WebUI 分页显示 total）。
        owner: 用户隔离标识，与 search 保持一致。
        session: 会话ID；非空时额外按 session_id 过滤（用于「仅当前会话」视图，
        cross_session=false 场景）。注意：这只是检索范围，不改变权限——
        owner（user_id 归属）与 is_public 过滤始终保留。"""
        if not self.enabled() or not _HAS_SQLITE:
            return 0
        conn = self._conn_get()
        sql = "SELECT COUNT(*) AS c FROM images WHERE 1=1"
        args: list = []
        if trash:
            sql += " AND deleted=?"
            args.append(1)
        else:
            sql += " AND deleted=0"
        # 画廊不展示失败项目（失败记录 status=1 / ext='fail'）
        sql += " AND status=0"
        if owner:
            sql += " AND (is_public=1 OR user_id=?)"
            args.append(owner)
        if session:
            # 会话范围过滤：非空时仅统计当前会话内的图。
            # 仅作为检索范围缩小，不替代 owner 权限过滤。
            sql += " AND session_id=?"
            args.append(session)
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
        if user_filter and user_filter.strip():
            uf = f"%{user_filter.strip()}%"
            sql += " AND (user_id LIKE ? OR user_name LIKE ?)"
            args += [uf, uf]
        try:
            row = conn.execute(sql, args).fetchone()
            return int(row["c"]) if row else 0
        except Exception as e:
            logger.warning(f"[图库] 计数失败: {e}")
            return 0

    def search(
        self,
        keyword: str = "",
        type: str | None = None,
        session=None,
        starred_only: bool = False,
        trash: bool = False,
        limit: int = 20,
        offset: int = 0,
        owner: str = "",
        user_filter: str = "",
    ) -> list[dict]:
        """按 prompt LIKE 检索（中文优先）。type: gen/ref/user/None(全部)。
        trash=True 时只查已移入回收站(deleted=1)的图片；否则默认只看未删除的。
        owner: 用户隔离标识。传入当前用户ID后，只检索该用户的图片（含历史无主图，
        即 user_id 为空或相等的），避免跨用户串图。
        session: 会话ID；非空时额外按 session_id 过滤（用于「仅当前会话」视图，
        cross_session=false 场景）。这是检索范围缩小，不改变 owner 权限语义。
        """
        if not self.enabled() or not _HAS_SQLITE:
            return []
        conn = self._conn_get()
        sql = "SELECT * FROM images WHERE 1=1"
        args: list = []
        if trash:
            sql += " AND deleted=?"
            args.append(1)
        else:
            sql += " AND deleted=0"
        # 画廊不展示失败项目（失败记录 status=1 / ext='fail'）
        sql += " AND status=0"
        # 可见性过滤：公开图(is_public=1)所有人可见；私有图仅本人。
        # 历史无主图（user_id 为空）不对普通用户暴露，仅管理员（owner 为空/全库模式）可见。
        # owner 为空（管理员/全库/库维护场景）不过滤。
        if owner:
            sql += " AND (is_public=1 OR user_id=?)"
            args.append(owner)
        if session:
            # 会话范围过滤：非空时仅检索当前会话内的图（cross_session=false）。
            # 仅缩小检索范围，不替代 owner（user_id）权限隔离。
            sql += " AND session_id=?"
            args.append(session)
        if keyword and keyword.strip():
            kw = f"%{keyword.strip()}%"
            sql += (
                " AND (prompt LIKE ? OR prompt_raw LIKE ? OR workflow LIKE ?"
                " OR sha256 IN (SELECT sha256 FROM image_tags WHERE tag LIKE ?))"
            )
            args += [kw, kw, kw, kw]
        if type:
            # type=img2img 按「图生图」过滤（is_img2img=1），因为图生图成品图的
            # source 仍是 gen，不能用 source 匹配；其余 gen/ref/user 按 source 过滤。
            if type == "img2img":
                sql += " AND is_img2img=1"
            else:
                sql += " AND source=?"
                args.append(type)
        if starred_only:
            sql += " AND starred=1"
        if user_filter and user_filter.strip():
            uf = f"%{user_filter.strip()}%"
            sql += " AND (user_id LIKE ? OR user_name LIKE ?)"
            args += [uf, uf]
        sql += " ORDER BY created_at DESC, sha256 DESC LIMIT ? OFFSET ?"
        args.append(int(limit))
        args.append(int(offset))
        try:
            rows = conn.execute(sql, args).fetchall()
        except Exception as e:
            logger.warning(f"[图库] 检索失败: {e}")
            return []
        out = []
        for r in rows:
            d = self._row_to_dict(r)
            # 全局唯一编号：基于「基础过滤（不含 keyword/tag）」的排序位置，
            # 与 get_by_global_no 定位一致 → 编号独立于当前搜索词，可直接无状态取图。
            d["gidx"] = self._gidx_rank(
                conn, r["created_at"] or 0, r["sha256"],
                owner=owner, session=session, trash=trash,
            )
            out.append(d)
        return out

    def get_by_global_no(self, no: int, owner: str = "", keyword: str = "", session=None) -> dict | None:
        """按「全局编号」取一条记录。

        编号基于「基础过滤（deleted/status/owner，不含 session/keyword/tag）」的
        排序（created_at DESC, sha256 DESC），与 search/recall 返回的 gidx 完全一致。
        因此：无论用户从「列表」「搜索」还是「找标签」里看到的编号，都能定位到同一张图，
        无需携带当时的搜索关键词或会话范围（无状态可取图）。

        keyword / session 参数仅作兼容保留，不再参与定位（避免与 gidx 的基础过滤口径不一致）。
        """
        if not self.enabled() or not _HAS_SQLITE or no < 1:
            return None
        conn = self._conn_get()
        sql = "SELECT * FROM images WHERE 1=1"
        args: list = []
        sql += " AND deleted=0"
        sql += " AND status=0"
        if owner:
            sql += " AND (is_public=1 OR user_id=?)"
            args.append(owner)
        sql += " ORDER BY created_at DESC, sha256 DESC LIMIT 1 OFFSET ?"
        args.append(int(no) - 1)
        try:
            row = conn.execute(sql, args).fetchone()
        except Exception as e:
            logger.warning(f"[图库] 按编号取图失败: {e}")
            return None
        if not row:
            return None
        d = self._row_to_dict(row)
        d["gidx"] = int(no)
        return d

    def recall_by_tag(self, tag: str, limit: int = 20, owner: str = "") -> list[dict]:
        """按语义标签召回。命中多张返回列表（由调用方列出让用户选）。
        owner: 用户隔离标识；传入后只召回该用户（含历史无主图）的图片。"""
        if not self.enabled() or not _HAS_SQLITE or not tag or not tag.strip():
            return []
        conn = self._conn_get()
        kw = f"%{tag.strip()}%"
        try:
            if owner:
                rows = conn.execute(
                    """
                    SELECT i.* FROM images i
                    JOIN image_tags t ON i.sha256 = t.sha256
                    WHERE t.tag LIKE ? AND i.deleted=0 AND i.status=0
                      AND (i.is_public=1 OR i.user_id=?)
                    ORDER BY i.created_at DESC, i.sha256 DESC LIMIT ?
                    """,
                    (kw, owner, int(limit)),
                ).fetchall()
            else:
                rows = conn.execute(
                    """
                    SELECT i.* FROM images i
                    JOIN image_tags t ON i.sha256 = t.sha256
                    WHERE t.tag LIKE ? AND i.deleted=0 AND i.status=0
                    ORDER BY i.created_at DESC, i.sha256 DESC LIMIT ?
                    """,
                    (kw, int(limit)),
                ).fetchall()
        except Exception as e:
            logger.warning(f"[图库] 标签召回失败: {e}")
            return []
        out = []
        for r in rows:
            d = self._row_to_dict(r)
            # 与 search 一致：gidx = 基础过滤下的全局唯一编号，可无状态定位
            d["gidx"] = self._gidx_rank(
                conn, r["created_at"] or 0, r["sha256"], owner=owner, session=None, trash=False
            )
            out.append(d)
        return out

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
        # 2) 本会话最近生成的图（值为 list[路径]，取最近一张存在的）
        gen = g_last_generated.get(sid) if g_last_generated else None
        for gp in reversed(gen if isinstance(gen, list) else ([gen] if gen else [])):
            try:
                if gp and os.path.exists(gp):
                    return gp
            except Exception:
                continue
        # 3) 本会话最近收到的图（值为 list[路径]，取最近一张存在的）
        recv = g_last_received.get(sid) if g_last_received else None
        for rp in reversed(recv if isinstance(recv, list) else ([recv] if recv else [])):
            try:
                if rp and os.path.exists(rp):
                    return rp
            except Exception:
                continue
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

    def get_by_sha(self, sha256_or_prefix: str) -> dict | None:
        """根据完整 sha256 或前 16 位前缀，返回记录的完整元数据（含提示词/尺寸等）。"""
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
        return self._row_to_dict(row)

    def data_url(self, sha256_prefix: str, ext: str) -> str | None:
        """返回缩略用 base64 data URL（行内展示，避免额外接口）。失败返回 None。"""
        p = self.path_of(sha256_prefix)
        if not p or not os.path.exists(p):
            return None
        try:
            mime = {
                "png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg",
                "webp": "image/webp", "gif": "image/gif",
            }.get((ext or "png").lower(), "image/png")
            b64 = base64.b64encode(Path(p).read_bytes()).decode("ascii")
            return f"data:{mime};base64,{b64}"
        except Exception:
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
        """收藏/取消收藏。sha256 应为完整 64 位哈希（精确匹配）。

        注意：必须精确匹配完整 sha256，不能用前缀 LIKE —— 否则短前缀会误中多张图，
        或在某些边界下把「设置某张」变成「更新多张」，导致收藏计数与预期不符。
        """
        if not sha256:
            return False
        conn = self._conn_get()
        try:
            conn.execute(
                "UPDATE images SET starred=? WHERE sha256=?",
                (1 if on else 0, sha256),
            )
            conn.commit()
            return True
        except Exception as e:
            logger.warning(f"[图库] 收藏失败: {e}")
            return False

    def delete(self, sha256: str) -> bool:
        """软删除：移入回收站（标记 deleted=1），不真删文件/记录。

        收藏图不允许移入回收站。回收站内调用 purge 才是真正删除。
        """
        if not sha256:
            return False
        conn = self._conn_get()
        row = self._row(sha256)
        if not row:
            return False
        if row["deleted"]:
            return True  # 已在回收站
        # 收藏图不允许删除
        if row["starred"]:
            return False
        try:
            conn.execute(
                "UPDATE images SET deleted=1, deleted_at=? WHERE sha256=?",
                (time.time(), row["sha256"]),
            )
            conn.commit()
            return True
        except Exception as e:
            logger.warning(f"[图库] 移入回收站失败: {e}")
            return False

    def restore(self, sha256: str) -> bool:
        """从回收站恢复（deleted=0）。"""
        if not sha256:
            return False
        conn = self._conn_get()
        try:
            conn.execute(
                "UPDATE images SET deleted=0, deleted_at=NULL WHERE sha256=?",
                (sha256,),
            )
            conn.commit()
            return True
        except Exception as e:
            logger.warning(f"[图库] 恢复失败: {e}")
            return False

    def purge(self, sha256: str) -> bool:
        """真正删除（从回收站彻底清除）：删除文件 + 记录 + 标签。"""
        if not sha256:
            return False
        conn = self._conn_get()
        row = self._row(sha256)
        if not row:
            return False
        p = self._path_of_row(row)
        try:
            if p.exists():
                p.unlink()
        except OSError as e:
            logger.warning(f"[图库] 删除文件失败: {e}")
        try:
            conn.execute("DELETE FROM image_tags WHERE sha256=?", (row["sha256"],))
            conn.execute("DELETE FROM images WHERE sha256=?", (row["sha256"],))
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
            total = conn.execute(
                "SELECT COUNT(*) c FROM images WHERE deleted=0"
            ).fetchone()["c"]
            starred = conn.execute(
                "SELECT COUNT(*) c FROM images WHERE starred=1 AND deleted=0"
            ).fetchone()["c"]
            tagged = conn.execute(
                "SELECT COUNT(DISTINCT t.sha256) c FROM image_tags t "
                "JOIN images i ON i.sha256=t.sha256 WHERE i.deleted=0"
            ).fetchone()["c"]
            gen = conn.execute(
                "SELECT COUNT(*) c FROM images WHERE source=? AND deleted=0",
                (SRC_GEN,),
            ).fetchone()["c"]
            ref = conn.execute(
                "SELECT COUNT(*) c FROM images WHERE source=? AND deleted=0",
                (SRC_REF,),
            ).fetchone()["c"]
            user = conn.execute(
                "SELECT COUNT(*) c FROM images WHERE source=? AND deleted=0",
                (SRC_USER,),
            ).fetchone()["c"]
            trash_count = conn.execute(
                "SELECT COUNT(*) c FROM images WHERE deleted=1"
            ).fetchone()["c"]
        except Exception as e:
            logger.warning(f"[图库] 统计失败: {e}")
            return {"enabled": self.enabled()}
        # 计算占用空间（仅统计 gallery/ 与 refs/；有效占用排除回收站图）
        try:
            active_size, trash_size = self._compute_sizes(conn)
        except Exception:
            active_size, trash_size = 0, 0
        return {
            "enabled": self.enabled(),
            "total": total,
            "starred": starred,
            "tagged": tagged,
            "gen": gen,
            "ref": ref,
            "user": user,
            "size_mb": round(active_size / 1024 / 1024, 2),
            "trash_size_mb": round(trash_size / 1024 / 1024, 2),
            "max_total_mb": int(self._cfg("max_total_mb", 2048)),
            "trash_count": trash_count,
        }

    # ------------------------------------------------------------------ #
    # 用户生图统计（WebUI「统计」页）
    # ------------------------------------------------------------------ #
    def user_ranking(self, days: int | None = None, limit: int = 50,
                     merge_alsoknown: list[str] | None = None) -> dict:
        """按用户统计生图数量排行（只统计成功生成的成品图 source='gen' 且 status=0）。

        days: None=全部；0=今天（自然日，本地时区）；其他正整数=最近 N 天（含今天）。
        merge_alsoknown: 可选。给出一组「其他插件/别名」名称（如 ["PrivateCompanion"]），
        命中 user_name 的这些记录会被合并成一行（user_id 用逗号拼接、count 求和），
        便于把同一插件/非真人来源的分散记录整合。传空列表/None 则不合并。
        返回 {"scope": str, "total": int, "rows": [{user_id, user_name, count, rank}]}
        """
        if not self.enabled() or not _HAS_SQLITE:
            return {"scope": "all", "total": 0, "rows": []}
        conn = self._conn_get()
        try:
            since = self._stats_since_ts(days)
            params: list = []
            where = "source=? AND status=0 AND deleted=0"
            params.append(SRC_GEN)
            if since is not None:
                where += " AND created_at>=?"
                params.append(since)
            rows = conn.execute(
                f"SELECT user_id, MAX(user_name) AS user_name, COUNT(*) AS c, MAX(created_at) AS last_ts FROM images "
                f"WHERE {where} GROUP BY user_id ORDER BY c DESC, MAX(created_at) DESC "
                f"LIMIT ?",
                (*params, int(limit)),
            ).fetchall()
            total = conn.execute(
                f"SELECT COUNT(*) AS c FROM images WHERE {where}",
                tuple(params),
            ).fetchone()["c"]
            ranked = []
            for r in rows:
                uid = r["user_id"] or ""
                uname = (r["user_name"] or "").strip() or uid or "未知用户"
                ranked.append({
                    "user_id": uid,
                    "user_name": uname,
                    "count": int(r["c"]),
                    "last_ts": float(r["last_ts"] or 0),
                })
            # 可选：把「其他插件/别名」命中的记录合并成一行
            if merge_alsoknown:
                names = {str(x).strip() for x in merge_alsoknown if str(x).strip()}
                owner_rows = []
                merged_count = 0
                merged_ids = []
                merged_counts: dict[str, int] = {}
                merged_last = 0.0
                for r in ranked:
                    if r["user_name"] in names:
                        merged_count += r["count"]
                        if r["user_id"]:
                            merged_ids.append(r["user_id"])
                            merged_counts[r["user_id"]] = merged_counts.get(r["user_id"], 0) + r["count"]
                        if r["last_ts"] > merged_last:
                            merged_last = r["last_ts"]
                    else:
                        owner_rows.append(r)
                if merged_count > 0:
                    disp_name = next(iter(names))
                    merged_ids_dedup = list(dict.fromkeys(merged_ids))
                    owner_rows.append({
                        "user_id": ",".join(merged_ids_dedup) if merged_ids_dedup else "",
                        "user_ids": merged_ids_dedup,
                        "user_id_counts": merged_counts,
                        "user_name": disp_name,
                        "count": merged_count,
                        "last_ts": merged_last,
                    })
                owner_rows.sort(key=lambda x: (-x["count"], -x["last_ts"]))
                ranked = owner_rows[: int(limit)]
            # 重新编号 rank
            out = []
            for i, r in enumerate(ranked, start=1):
                out.append({
                    "rank": i,
                    "user_id": r["user_id"],
                    "user_ids": r.get("user_ids") or ([r["user_id"]] if r["user_id"] else []),
                    "user_id_counts": r.get("user_id_counts") or {},
                    "user_name": r["user_name"],
                    "count": r["count"],
                    "last_ts": r.get("last_ts", 0),
                })
            scope = "all" if days is None else ("today" if days == 0 else f"{days}d")
            return {"scope": scope, "total": int(total), "rows": out}
        except Exception as e:
            logger.warning(f"[图库] 用户排行统计失败: {e}")
            return {"scope": "all", "total": 0, "rows": []}

    def hourly_trend(self, hours: int = 24) -> dict:
        """近 N 小时（默认 24 小时滚动窗口）用户生图数量面积图数据：按本地时区小时分桶。

        返回 {"scope": "24h", "buckets": [{"hour": "17:00", "ts": 秒, "count": n}]}
        桶从 (当前整点 - (hours-1) 小时) 开始，逐小时递增到当前整点，未生图的时段也补 0。
        例如现在是 17 点，24 小时窗口从昨天 17 点开始，到今天 17 点结束。
        """
        if not self.enabled() or not _HAS_SQLITE:
            return {"scope": "24h", "buckets": []}
        conn = self._conn_get()
        try:
            now = time.time()
            hours = max(1, min(int(hours), 24 * 7))
            end_ts = now
            start_ts = end_ts - hours * 3600.0
            rows = conn.execute(
                "SELECT created_at AS t FROM images "
                "WHERE source=? AND status=0 AND deleted=0 AND created_at>=? AND created_at<=? "
                "ORDER BY created_at ASC",
                (SRC_GEN, start_ts, end_ts),
            ).fetchall()
            # 统计每个整点桶的计数
            bucket_count: dict[int, int] = {}
            for r in rows:
                t = float(r["t"])
                bucket = int(t // 3600) * 3600
                bucket_count[bucket] = bucket_count.get(bucket, 0) + 1
            # 生成连续桶（含 0 值补全）：从 (当前整点 - (hours-1)) 到当前整点
            cur_hour = int(now // 3600) * 3600
            b0 = cur_hour - (hours - 1) * 3600
            b1 = cur_hour
            buckets = []
            b = b0
            while b <= b1:
                lt_b = time.localtime(b)
                buckets.append({
                    "hour": time.strftime("%H:00", lt_b),
                    "ts": b,
                    "count": bucket_count.get(b, 0),
                })
                b += 3600
            return {"scope": f"{hours}h", "buckets": buckets}
        except Exception as e:
            logger.warning(f"[图库] 小时趋势统计失败: {e}")
            return {"scope": "1d", "buckets": []}

    @staticmethod
    def _stats_since_ts(days: int | None) -> float | None:
        """根据天数参数返回统计起始时间戳（本地时区）。None=全部；0=今天 0 点；N=最近 N 天 0 点。"""
        if days is None:
            return None
        now = time.time()
        lt = time.localtime(now)
        day_start = time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday, 0, 0, 0, 0, 0, -1))
        if days == 0:
            return day_start
        return day_start - (max(1, int(days)) - 1) * 86400.0

    # ------------------------------------------------------------------ #
    # LRU 淘汰
    # ------------------------------------------------------------------ #
    def _compute_sizes(self, conn) -> tuple[int, int]:
        """统计占用空间。

        返回 (active_size, trash_size)：
        - active_size：gallery/ 与 refs/ 中「有效」图文件的大小（排除回收站）。
        - trash_size：回收站（deleted=1）图文件的大小（仍占磁盘，但已不可检索）。

        口径：以文件系统实际大小为准；回收站文件用数据库 deleted 标记识别，
        避免把用户已删除的图算进有效占用，使「当前占用」与实际可检索的图对得上。
        """
        active = 0
        trash = 0
        if not _HAS_SQLITE:
            # 无 SQLite 时无法区分回收站，退化为全量统计
            for d in (self.gallery_dir, self.refs_dir):
                if not d.exists():
                    continue
                for f in d.rglob("*"):
                    if f.is_file():
                        active += f.stat().st_size
            return active, 0
        # 收集回收站文件路径集合
        trash_paths = set()
        try:
            rows = conn.execute("SELECT * FROM images WHERE deleted=1").fetchall()
            for row in rows:
                try:
                    p = self._path_of_row(row)
                    trash_paths.add(str(p))
                except Exception:
                    pass
        except Exception as e:
            logger.warning(f"[图库] 统计回收站失败（按全量统计）: {e}")
            trash_paths = set()
        for d in (self.gallery_dir, self.refs_dir):
            if not d.exists():
                continue
            for f in d.rglob("*"):
                if f.is_file():
                    if str(f) in trash_paths:
                        trash += f.stat().st_size
                    else:
                        active += f.stat().st_size
        return active, trash

    def enforce_lru(self) -> int:
        """超 max_total_mb 时按创建时间升序淘汰图。优先淘汰回收站图，
        再淘汰非收藏、无标签图。返回删除数量。"""
        if not _HAS_SQLITE:
            return 0
        max_mb = int(self._cfg("max_total_mb", 2048))
        conn = self._conn_get()
        try:
            active_size, trash_size = self._compute_sizes(conn)
            size = active_size + trash_size  # 总占用（含回收站）
            max_bytes = max_mb * 1024 * 1024
            if size <= max_bytes:
                return 0
        except Exception as e:
            logger.warning(f"[图库] LRU 预检失败: {e}")
            return 0

        removed = 0
        # 第一轮：优先清空回收站图（用户已删除，应最先腾出空间）
        try:
            trash_rows = conn.execute(
                "SELECT * FROM images WHERE deleted=1 ORDER BY created_at ASC"
            ).fetchall()
        except Exception:
            trash_rows = []
        for row in trash_rows:
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
                conn.execute("DELETE FROM images WHERE sha256=?", (row["sha256"],))
                removed += 1
            except Exception:
                pass

        # 第二轮：仍超限则淘汰有效图（starred=0 且无标签），按 created_at 升序
        try:
            rows = conn.execute(
                """
                SELECT i.* FROM images i
                WHERE i.starred=0 AND i.deleted=0
                  AND NOT EXISTS (SELECT 1 FROM image_tags t WHERE t.sha256=i.sha256)
                ORDER BY i.created_at ASC
                """
            ).fetchall()
        except Exception:
            rows = []
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

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
import logging
import os
import secrets
import threading
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

# NSFW 检测器懒加载导入（兼容「相对导入 / 绝对导入」两种插件加载方式）
_NSFW_DET_MODULE = None


def _get_detector(threshold: float = 0.5):
    """获取 NSFW 检测器（模块级，兼容性导入 nsfw_detector）。"""
    global _NSFW_DET_MODULE
    if _NSFW_DET_MODULE is None:
        try:
            from . import nsfw_detector
            _NSFW_DET_MODULE = nsfw_detector
        except Exception:
            try:
                import nsfw_detector
                _NSFW_DET_MODULE = nsfw_detector
            except Exception as e:
                logger = logging.getLogger("astrbot_plugin_comfyui_anima.image_store")
                logger.warning(f"[图库] 无法导入 nsfw_detector（NSFW 检测不可用）: {e}")
                _NSFW_DET_MODULE = False
    if not _NSFW_DET_MODULE:
        return None
    try:
        return _NSFW_DET_MODULE.get_detector(threshold)
    except Exception:
        return None


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

    def __init__(self, data_dir: Path, cfg: dict | None = None,
                 cfg_provider=None) -> None:
        self.data_dir = Path(data_dir)
        self.gallery_dir = self.data_dir / "gallery"
        self.refs_dir = self.data_dir / "refs"
        self.gallery_dir.mkdir(parents=True, exist_ok=True)
        self.refs_dir.mkdir(parents=True, exist_ok=True)
        self.db_path = self.data_dir / "gallery.db"
        self.cfg = cfg or {}
        # 可选实时配置提供器：返回最新的 gallery 配置 dict（避免改动后需重启才生效）。
        # 提供后 _nsfw_cfg 等实时读取配置；未提供则用构造时的 cfg 快照。
        self._cfg_provider = cfg_provider if callable(cfg_provider) else None
        self._conn = None
        # 分享令牌内存兜底：同一进程内创建/校验必然一致，
        # 规避 SQLite 读写不一致导致的「分享链接已失效」（token 在库中查不到）。
        self._share_tokens_mem: dict[str, dict] = {}
        # 后台 NSFW 扫描任务状态（线程内更新，读取加锁）
        self._scan_lock = threading.Lock()
        self._scan_thread: "threading.Thread | None" = None
        self._scan_state: dict = {
            "running": False, "total": 0, "done": 0, "nsfw": 0,
            "started_at": None, "finished_at": None, "last_err": "",
        }
        self._init_db()

    # ------------------------------------------------------------------ #
    # 连接 / 建表
    # ------------------------------------------------------------------ #
    def _conn_get(self):
        if self._conn is None:
            self._conn = sqlite3.connect(str(self.db_path))
            self._conn.row_factory = sqlite3.Row
            # 开启 WAL：降低写入锁等待与 fsync 开销，读写并发更流畅
            try:
                self._conn.execute("PRAGMA journal_mode=WAL")
                self._conn.execute("PRAGMA synchronous=NORMAL")
            except Exception as e:  # pragma: no cover
                logger.warning(f"[图库] 开启 WAL 失败（不影响使用）: {e}")
        return self._conn

    def _init_db(self) -> None:
        if not _HAS_SQLITE:
            logger.warning("[图库] 环境无 sqlite3，图库功能不可用")
            return
        conn = self._conn_get()

        def _ddl(label: str, sql: str) -> None:
            """执行一条 DDL，成功打 INFO、失败打 WARNING（不中断后续建表）。"""
            try:
                conn.execute(sql)
                logger.info(f"[图库] 建 {label} 成功")
            except Exception as _e:
                logger.warning(f"[图库] 建 {label} 失败: {_e}")

        # 每张表 / 每个索引独立执行并打日志，任一步失败都不阻断，且失败信息直接可见。
        _ddl(
            "images 表",
            """CREATE TABLE IF NOT EXISTS images (
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
                is_global  INTEGER NOT NULL DEFAULT 0,
                session_id TEXT DEFAULT ''
            )""",
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
            ("is_global", "INTEGER NOT NULL DEFAULT 0"),
            ("session_id", "TEXT DEFAULT ''"),
            ("nsfw", "INTEGER NOT NULL DEFAULT 0"),
            ("nsfw_score", "REAL DEFAULT NULL"),
            ("nsfw_blur", "INTEGER DEFAULT NULL"),
            ("nsfw_checked", "INTEGER NOT NULL DEFAULT 0"),
        ):
            try:
                conn.execute(f"ALTER TABLE images ADD COLUMN {_col} {_type}")
            except Exception:
                pass
        _ddl(
            "image_tags 表",
            """CREATE TABLE IF NOT EXISTS image_tags (
                sha256 TEXT NOT NULL,
                tag     TEXT NOT NULL,
                PRIMARY KEY (sha256, tag)
            )""",
        )
        _ddl("idx_images_month", "CREATE INDEX IF NOT EXISTS idx_images_month ON images(month)")
        _ddl("idx_images_created", "CREATE INDEX IF NOT EXISTS idx_images_created ON images(created_at)")
        _ddl("idx_images_session", "CREATE INDEX IF NOT EXISTS idx_images_session ON images(session_id)")
        _ddl("idx_tags_tag", "CREATE INDEX IF NOT EXISTS idx_tags_tag ON image_tags(tag)")
        # 分享站：点赞（按用户+时间记录，不只计数）
        _ddl(
            "image_likes 表",
            """CREATE TABLE IF NOT EXISTS image_likes (
                sha256    TEXT NOT NULL,
                user_id   TEXT NOT NULL,
                user_name TEXT DEFAULT NULL,
                created_at REAL NOT NULL,
                PRIMARY KEY (sha256, user_id)
            )""",
        )
        # 分享站：收藏（按用户+时间记录）
        _ddl(
            "image_favorites 表",
            """CREATE TABLE IF NOT EXISTS image_favorites (
                sha256    TEXT NOT NULL,
                user_id   TEXT NOT NULL,
                user_name TEXT DEFAULT NULL,
                created_at REAL NOT NULL,
                PRIMARY KEY (sha256, user_id)
            )""",
        )
        # 分享站：临时访问令牌（带过期）
        _ddl(
            "share_tokens 表",
            """CREATE TABLE IF NOT EXISTS share_tokens (
                token      TEXT PRIMARY KEY,
                user_id    TEXT NOT NULL,
                user_name  TEXT DEFAULT NULL,
                created_at REAL NOT NULL,
                expire_at  REAL NOT NULL
            )""",
        )
        # 用户表：集中维护用户元数据（QQ 号、昵称、平台、首次使用 / 最后活跃）。
        # 无独立用户表时用户身份散落在各业务表（images/likes/favorites/share_tokens 的 user_id）。
        _ddl(
            "users 表",
            """CREATE TABLE IF NOT EXISTS users (
                user_id    TEXT PRIMARY KEY,
                user_name  TEXT DEFAULT NULL,
                platform   TEXT DEFAULT NULL,
                first_seen REAL NOT NULL,
                last_seen  REAL NOT NULL
            )""",
        )
        _ddl("idx_likes_sha", "CREATE INDEX IF NOT EXISTS idx_likes_sha ON image_likes(sha256)")
        _ddl("idx_fav_sha", "CREATE INDEX IF NOT EXISTS idx_fav_sha ON image_favorites(sha256)")
        _ddl("idx_share_user", "CREATE INDEX IF NOT EXISTS idx_share_user ON share_tokens(user_id)")
        # 迁移：分享令牌单 IP 绑定列（旧库无此列时 ALTER，已有则跳过）
        try:
            conn.execute("ALTER TABLE share_tokens ADD COLUMN bound_ip TEXT DEFAULT NULL")
        except Exception:
            pass  # 列已存在
        # 用户维度索引：用户登记回填（按 user_id 查最早活动）与用户统计使用
        _ddl("idx_images_user", "CREATE INDEX IF NOT EXISTS idx_images_user ON images(user_id)")
        _ddl("idx_likes_user", "CREATE INDEX IF NOT EXISTS idx_likes_user ON image_likes(user_id)")
        _ddl("idx_fav_user", "CREATE INDEX IF NOT EXISTS idx_fav_user ON image_favorites(user_id)")
        try:
            conn.commit()
        except Exception as _e:
            logger.warning(f"[图库] 初始化 commit 失败: {_e}")

    def close(self) -> None:
        if self._conn is not None:
            # WAL checkpoint：把 -wal 中未合并的数据合并回主库，避免停止/卸载时
            # 残留 WAL 文件导致数据库状态异常（建表/迁移被 SQLite 静默失败）。
            try:
                self._conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            except Exception:
                pass
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
    # NSFW 检测
    # ------------------------------------------------------------------ #
    def _nsfw_cfg(self) -> dict:
        # 优先实时读取配置（若提供 provider），避免改动阈值后需重启才生效
        cfg = self.cfg
        if self._cfg_provider is not None:
            try:
                cfg = self._cfg_provider() or {}
            except Exception:
                cfg = self.cfg
        g = cfg.get("nsfw") or {}
        return g if isinstance(g, dict) else {}

    def _nsfw_enabled(self) -> bool:
        return bool(self._nsfw_cfg().get("enabled", True))

    def _nsfw_threshold(self) -> float:
        try:
            return float(self._nsfw_cfg().get("threshold", 0.5))
        except (TypeError, ValueError):
            return 0.5

    def _nsfw_default_blur(self) -> bool:
        return bool(self._nsfw_cfg().get("blur_default", True))

    def scan_nsfw_start(self, only_unchecked: bool = True) -> dict:
        """后台启动「一键检测所有未检测图」。返回立即状态，扫描在后台线程执行。

        - 默认只扫 ``nsfw_checked=0`` 的未检测图；``only_unchecked=False`` 时全量重扫。
        - 若已在扫描，返回当前状态而非重复启动。
        """
        with self._scan_lock:
            if self._scan_state.get("running"):
                return dict(self._scan_state)
            if not self.enabled() or not _HAS_SQLITE:
                return {"running": False, "total": 0, "done": 0, "nsfw": 0,
                        "started_at": None, "finished_at": None, "last_err": "图库未启用"}
            if not self._nsfw_enabled():
                return {"running": False, "total": 0, "done": 0, "nsfw": 0,
                        "started_at": None, "finished_at": None, "last_err": "NSFW 检测已禁用"}
            det = _get_detector(self._nsfw_threshold())
            if det is None:
                return {"running": False, "total": 0, "done": 0, "nsfw": 0,
                        "started_at": None, "finished_at": None, "last_err": "NSFW 检测不可用（无法加载检测器）"}
            if not det.available():
                _err = getattr(det, "last_error", "") or "依赖或模型未就绪"
                return {"running": False, "total": 0, "done": 0, "nsfw": 0,
                        "started_at": None, "finished_at": None, "last_err": f"NSFW 检测不可用：{_err}"}
            self._scan_state = {
                "running": True, "total": 0, "done": 0, "nsfw": 0,
                "started_at": time.time(), "finished_at": None, "last_err": "",
            }
            t = threading.Thread(
                target=self._scan_nsfw_worker,
                args=(only_unchecked,),
                daemon=True,
            )
            self._scan_thread = t
            t.start()
            return dict(self._scan_state)

    def _scan_nsfw_worker(self, only_unchecked: bool) -> None:
        """后台线程执行扫描。用独立 SQLite 连接（避免跨线程共用连接）。"""
        # 在子线程内创建独立连接（check_same_thread=False 以支持多线程安全访问）
        try:
            wconn = sqlite3.connect(str(self.db_path), check_same_thread=False)
            wconn.row_factory = sqlite3.Row
            try:
                wconn.execute("PRAGMA journal_mode=WAL")
                wconn.execute("PRAGMA synchronous=NORMAL")
            except Exception as e:  # pragma: no cover
                logger.warning(f"[图库] 扫描线程开启 WAL 失败（不影响使用）: {e}")
        except Exception as e:
            self._set_scan_state({"running": False, "last_err": f"数据库连接失败: {e}"})
            return
        try:
            where = "deleted=0"
            if only_unchecked:
                where += " AND nsfw_checked=0"
            total = wconn.execute(
                "SELECT COUNT(*) AS c FROM images WHERE deleted=0 AND nsfw_checked=0"
            ).fetchone()["c"]
            self._set_scan_state({"total": int(total)})
            rows = wconn.execute(
                f"SELECT sha256, ext, month, source FROM images WHERE {where} "
                f"ORDER BY created_at ASC"
            ).fetchall()
            nsfw_cnt = 0
            done = 0
            det = _get_detector(self._nsfw_threshold())
            if det is None:
                raise RuntimeError("NSFW 检测器不可用")
            for r in rows:
                # 检查是否被停止（模块卸载/切换配置等场景）
                with self._scan_lock:
                    if not self._scan_state.get("running"):
                        break
                p = self._path_of_row(r)
                if not p.exists():
                    wconn.execute("UPDATE images SET nsfw_checked=1 WHERE sha256=?", (r["sha256"],))
                    done += 1
                    self._set_scan_state({"done": done, "nsfw": nsfw_cnt})
                    continue
                is_nsfw, score, avail = det.detect(str(p))
                if not avail:
                    # 模型中途失效：停止扫描，保留已扫结果
                    break
                done += 1
                if is_nsfw:
                    nsfw_cnt += 1
                wconn.execute(
                    "UPDATE images SET nsfw=?, nsfw_score=?, nsfw_checked=1 WHERE sha256=?",
                    (1 if is_nsfw else 0, score, r["sha256"]),
                )
                if done % 10 == 0:
                    wconn.commit()
                    self._set_scan_state({"done": done, "nsfw": nsfw_cnt})
            wconn.commit()
            self._set_scan_state({
                "running": False, "done": done, "nsfw": nsfw_cnt, "finished_at": time.time(),
            })
            logger.info(f"[图库] NSFW 后台扫描完成：{done}/{total} 张，NSFW {nsfw_cnt}")
        except Exception as e:
            logger.warning(f"[图库] NSFW 后台扫描失败: {e}")
            self._set_scan_state({"running": False, "last_err": str(e)})
        finally:
            try:
                wconn.close()
            except Exception:
                pass

    def _set_scan_state(self, patch: dict) -> None:
        with self._scan_lock:
            self._scan_state.update(patch)

    def scan_nsfw_progress(self) -> dict:
        """返回当前 NSFW 扫描进度。{running, total, done, nsfw, started_at, finished_at, last_err}"""
        with self._scan_lock:
            if not hasattr(self, "_scan_state"):
                return {"running": False, "total": 0, "done": 0, "nsfw": 0,
                        "started_at": None, "finished_at": None, "last_err": ""}
            return dict(self._scan_state)

    def check_nsfw(self, sha256: str) -> dict:
        """对单张图做一次 NSFW 检测，并把结果写回数据库。

        返回 ``{"ok": bool, "nsfw": bool, "nsfw_score": float|None, "available": bool, "msg": str}``。
        - ``available=False``：模型/依赖不可用或文件缺失，未写入（保持原状态）。
        - ``available=True``：检测完成并写入 nsfw/nsfw_score/nsfw_checked。
        """
        if not sha256:
            return {"ok": False, "nsfw": False, "nsfw_score": None, "available": False, "msg": "缺少 sha"}
        if not self.enabled() or not _HAS_SQLITE:
            return {"ok": False, "nsfw": False, "nsfw_score": None, "available": False, "msg": "图库未启用"}
        if not self._nsfw_enabled():
            return {"ok": False, "nsfw": False, "nsfw_score": None, "available": False, "msg": "NSFW 检测已禁用"}
        det = _get_detector(self._nsfw_threshold())
        if det is None:
            return {"ok": False, "nsfw": False, "nsfw_score": None, "available": False,
                    "msg": "NSFW 检测不可用（无法加载检测器）"}
        if not det.available():
            _err = getattr(det, "last_error", "") or "依赖或模型未就绪"
            return {"ok": False, "nsfw": False, "nsfw_score": None, "available": False,
                    "msg": f"NSFW 检测不可用：{_err}"}
        conn = self._conn_get()
        try:
            # 支持前缀匹配（前端传的 sha 可能是 sha256[:16] 内容寻址前缀）
            sha_prefix = sha256.strip()
            row = conn.execute(
                "SELECT sha256, ext, month, source FROM images WHERE sha256 LIKE ? "
                "ORDER BY sha256 LIMIT 1", (f"{sha_prefix}%",)
            ).fetchone()
            if not row:
                return {"ok": False, "nsfw": False, "nsfw_score": None, "available": False, "msg": "未找到该图"}
            full_sha = row["sha256"]
            p = self._path_of_row(row)
            if not p.exists():
                return {"ok": False, "nsfw": False, "nsfw_score": None, "available": False, "msg": "图片文件不存在"}
            is_nsfw, score, avail = det.detect(str(p))
            if not avail:
                return {"ok": False, "nsfw": False, "nsfw_score": None, "available": False, "msg": "检测失败"}
            conn.execute(
                "UPDATE images SET nsfw=?, nsfw_score=?, nsfw_checked=1 WHERE sha256=?",
                (1 if is_nsfw else 0, score, full_sha),
            )
            conn.commit()
            return {"ok": True, "nsfw": is_nsfw, "nsfw_score": score, "available": True, "msg": "检测完成"}
        except Exception as e:
            logger.warning(f"[图库] 单图 NSFW 检测失败: {e}")
            return {"ok": False, "nsfw": False, "nsfw_score": None, "available": False, "msg": f"检测失败: {e}"}

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
        on_dedup=None,
    ) -> str | None:
        """归档一张图（移动转正，内容寻址去重）。

        返回**归档后文件的最终绝对路径**（落盘后的 gallery/refs 路径，或命中去重时
        已存在文件的路径）；归档不可用/失败时返回 None。注意：本方法会把 src_path
        **移动**到永久目录，因此调用方必须用返回值作为后续发送/上报所用的路径，
        不要再使用已被移动的旧 src_path。

        on_dedup: 可选回调，去重命中（未新增行，仅计数+1）时调用 on_dedup(sha, use_count)。
        """
        if not self.enabled():
            return None
        if not _HAS_SQLITE:
            return None
        if not src_path or not os.path.exists(src_path):
            logger.warning(f"[图库] 归档失败：源文件不存在 {src_path}")
            return None
        # 用户登记：出图/入库时记录用户元数据（首次使用即创建用户）
        if user_id:
            self.ensure_user(user_id, user_name)
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
                    # 业务日志：去重命中（图库/出图记录不新增行，但调用方可能仍按次数计数）
                    logger.info(
                        f"[图库] 去重命中 sha256={sha[:16]} 已存在记录(use_count 原={row['use_count']})，"
                        f"本次不插入新行（图库/出图记录仅显示 1 条，但调用方计数仍 +1）"
                    )
                    if callable(on_dedup):
                        try:
                            on_dedup(sha, row["use_count"])
                        except Exception:
                            pass
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
                logger.info(
                    f"[图库] 去重命中 sha256={sha[:16]} use_count 自 {row['use_count']} → {row['use_count'] + 1}（仅计数+1，未新增记录）"
                )
                if callable(on_dedup):
                    try:
                        on_dedup(sha, row["use_count"] + 1)
                    except Exception:
                        pass
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

        # NSFW 检测：归档时自动打标（模型不可用/失败则标记 nsfw_checked=0，不阻塞归档）
        _nsfw, _nsfw_score, _nsfw_checked = 0, None, 0
        try:
            if self._nsfw_enabled():
                _det = _get_detector(self._nsfw_threshold())
                if _det is None:
                    raise RuntimeError("NSFW 检测器不可用")
                _is_nsfw, _score, _avail = _det.detect(str(dest))
                if _avail:
                    _nsfw = 1 if _is_nsfw else 0
                    _nsfw_score = _score
                    _nsfw_checked = 1
        except Exception as _ne:
            logger.warning(f"[图库] NSFW 检测异常（忽略，不阻塞归档）: {_ne}")
            _nsfw, _nsfw_score, _nsfw_checked = 0, None, 0

        try:
            conn.execute(
                """
                INSERT INTO images
                (sha256, ext, month, prompt, prompt_raw, workflow, loras,
                 seed, w, h, denoise, is_img2img, ref_sha256, source,
                 use_count, starred, created_at, size_bytes, cost_sec,
                 user_id, user_name, session_id, trigger_msg, status,
                 nsfw, nsfw_score, nsfw_blur, nsfw_checked)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,1,0,?,?,?,?,?,?,?,?,
                        ?,?,?,?)
                """,
                (
                    sha, ext, (dest.parent.name or time.strftime("%Y-%m")),
                    prompt, prompt_raw, workflow, loras_json,
                    seed, w, h, denoise,
                    1 if is_img2img else 0, ref_sha256 or "", source,
                    time.time(), size_bytes, cost_sec,
                    user_id or "", user_name or "", session_id or "", trigger_msg or "", status,
                    _nsfw, _nsfw_score, None, _nsfw_checked,
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
        必须传 user_id，否则会成为"无主图"串给其他用户。
        归档成功即自动加入收藏列表（starred=1），使「帮我收藏一下」/「/图库 收藏」同时
        完成「入库 + 收藏 + 打标签」三步。"""
        _final = self.archive_image(src_path, source=SRC_USER, user_id=user_id, user_name=user_name, session_id=session_id)
        if not _final:
            return None
        # 从最终路径反算 sha（与归档时一致），供调用方做收藏/召回标识。
        sha = _sha256_of(_final)
        if sha:
            # 加入收藏列表（starred=1），收藏图永不参与 LRU 淘汰
            try:
                self.star(sha, 1)
            except Exception as _se:
                logger.warning(f"[图库] 归档后收藏失败（不影响归档）: {_se}")
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
        except Exception as _e:
            logger.warning(f"[图库] 设置可见性失败: {_e}")
            return False

    def set_global(self, sha256: str, on: bool) -> bool:
        """设置图片「全局」：on=True 后，任何群聊的列表/搜索都能看到这张图（跨会话共享）。
        与「公开」的区别：全局图他人可见但不可检索/发送（仅作者/管理员可发图），
        公开图他人可检索/发送。返回是否成功。"""
        if not self.enabled() or not _HAS_SQLITE:
            return False
        conn = self._conn_get()
        try:
            cur = conn.execute(
                "UPDATE images SET is_global=? WHERE sha256 LIKE ?",
                (1 if on else 0, sha256 + "%"),
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
            "is_global": bool(row["is_global"]) if "is_global" in row.keys() else False,
            "nsfw": bool(row["nsfw"]) if "nsfw" in row.keys() else False,
            "nsfw_score": row["nsfw_score"] if "nsfw_score" in row.keys() else None,
            "nsfw_blur": row["nsfw_blur"] if "nsfw_blur" in row.keys() else None,
            "nsfw_checked": bool(row["nsfw_checked"]) if "nsfw_checked" in row.keys() else False,
            "tags": self.tags_of(row["sha256"]),
        }

    # ------------------------------------------------------------------ #
    # 分享站：点赞 / 收藏（按用户+时间记录） / 分享令牌 / 世界·图库·收藏·个人中心
    # ------------------------------------------------------------------ #
    def like(self, sha256: str, user_id: str, user_name: str = None) -> bool:
        if not sha256 or not user_id or not self.enabled() or not _HAS_SQLITE:
            return False
        try:
            self.ensure_user(user_id, user_name)
            conn = self._conn_get()
            conn.execute(
                "INSERT OR REPLACE INTO image_likes(sha256,user_id,user_name,created_at) VALUES(?,?,?,?)",
                (sha256, user_id, user_name, time.time()),
            )
            conn.commit()
            return True
        except Exception as e:
            logger.warning(f"[图库] 点赞失败: {e}")
            return False

    def unlike(self, sha256: str, user_id: str) -> bool:
        if not sha256 or not user_id:
            return False
        try:
            conn = self._conn_get()
            conn.execute("DELETE FROM image_likes WHERE sha256=? AND user_id=?", (sha256, user_id))
            conn.commit()
            return True
        except Exception as e:
            logger.warning(f"[图库] 取消点赞失败: {e}")
            return False

    def is_liked(self, sha256: str, user_id: str) -> bool:
        if not sha256 or not user_id:
            return False
        try:
            conn = self._conn_get()
            row = conn.execute("SELECT 1 FROM image_likes WHERE sha256=? AND user_id=?", (sha256, user_id)).fetchone()
            return row is not None
        except Exception:
            return False

    def like_count(self, sha256: str) -> int:
        if not sha256:
            return 0
        try:
            conn = self._conn_get()
            row = conn.execute("SELECT COUNT(*) c FROM image_likes WHERE sha256=?", (sha256,)).fetchone()
            return int(row["c"]) if row else 0
        except Exception:
            return 0

    def favorite(self, sha256: str, user_id: str, user_name: str = None) -> bool:
        if not sha256 or not user_id or not self.enabled() or not _HAS_SQLITE:
            return False
        try:
            self.ensure_user(user_id, user_name)
            conn = self._conn_get()
            conn.execute(
                "INSERT OR REPLACE INTO image_favorites(sha256,user_id,user_name,created_at) VALUES(?,?,?,?)",
                (sha256, user_id, user_name, time.time()),
            )
            conn.commit()
            return True
        except Exception as e:
            logger.warning(f"[图库] 收藏失败: {e}")
            return False

    def unfavorite(self, sha256: str, user_id: str) -> bool:
        if not sha256 or not user_id:
            return False
        try:
            conn = self._conn_get()
            conn.execute("DELETE FROM image_favorites WHERE sha256=? AND user_id=?", (sha256, user_id))
            conn.commit()
            return True
        except Exception as e:
            logger.warning(f"[图库] 取消收藏失败: {e}")
            return False

    def is_favorited(self, sha256: str, user_id: str) -> bool:
        if not sha256 or not user_id:
            return False
        try:
            conn = self._conn_get()
            row = conn.execute("SELECT 1 FROM image_favorites WHERE sha256=? AND user_id=?", (sha256, user_id)).fetchone()
            return row is not None
        except Exception:
            return False

    def favorite_count(self, sha256: str) -> int:
        if not sha256:
            return 0
        try:
            conn = self._conn_get()
            row = conn.execute("SELECT COUNT(*) c FROM image_favorites WHERE sha256=?", (sha256,)).fetchone()
            return int(row["c"]) if row else 0
        except Exception:
            return 0

    def _share_flags(self, conn, user_id: str, shas: list):
        liked, fav = set(), set()
        if not user_id or not shas:
            return liked, fav
        try:
            ph = ",".join("?" * len(shas))
            for r in conn.execute(f"SELECT sha256 FROM image_likes WHERE user_id=? AND sha256 IN ({ph})", [user_id, *shas]):
                liked.add(r["sha256"])
            for r in conn.execute(f"SELECT sha256 FROM image_favorites WHERE user_id=? AND sha256 IN ({ph})", [user_id, *shas]):
                fav.add(r["sha256"])
        except Exception:
            pass
        return liked, fav

    def create_share_token(self, user_id: str, user_name: str = None, ttl_sec: int = 3600) -> str:
        now = time.time()
        # 复用：同一用户有效期内已存在的令牌直接返回，不重复生成（单链接约束）
        old = self._find_valid_share_token(user_id, now)
        if old:
            return old
        token = secrets.token_urlsafe(24)
        # 内存兜底：同一进程内创建/校验必然一致，规避 SQLite 读写不一致导致的「链接已失效」
        self._share_tokens_mem[token] = {
            "token": token,
            "user_id": user_id,
            "user_name": user_name,
            "created_at": now,
            "expire_at": now + ttl_sec,
            "bound_ip": None,  # 首次访问时绑定
        }
        # 顺带清理内存中已过期的令牌，避免长期累积
        if len(self._share_tokens_mem) > 500:
            for _k in [k for k, v in self._share_tokens_mem.items()
                       if (v.get("expire_at") or 0) < time.time()]:
                self._share_tokens_mem.pop(_k, None)
        # SQLite 持久化：独立 try，确保令牌一定写入库中（重启/多实例后也能查到）。
        # 绝不能与 ensure_user 放在同一 try——此前 ensure_user 异常会导致 INSERT 不执行，
        # 令牌只进内存，插件一重启就查不到 → 扫码「链接已失效」。
        try:
            conn = self._conn_get()
            conn.execute(
                "INSERT OR REPLACE INTO share_tokens(token,user_id,user_name,created_at,expire_at,bound_ip) VALUES(?,?,?,?,?,?)",
                (token, user_id, user_name, now, now + ttl_sec, None),
            )
            conn.commit()
        except Exception as e:
            logger.warning(f"[图库] 创建分享令牌 SQLite 写入失败（内存兜底仍可用）: {e}")
        # 用户登记：独立处理，绝不影响令牌写入
        try:
            self.ensure_user(user_id, user_name)
        except Exception:
            pass
        return token

    def _find_valid_share_token(self, user_id: str, now: float) -> str:
        """同用户有效期内已存在的令牌（内存优先，SQLite 兜底），供单链接复用。"""
        if not user_id:
            return ""
        for _k, v in self._share_tokens_mem.items():
            if (v.get("user_id") == user_id) and (v.get("expire_at") or 0) > now:
                return _k
        try:
            conn = self._conn_get()
            row = conn.execute(
                "SELECT token FROM share_tokens WHERE user_id=? AND expire_at>? "
                "ORDER BY created_at DESC LIMIT 1",
                (user_id, now),
            ).fetchone()
            if row:
                return row["token"]
        except Exception:
            pass
        return ""

    def verify_share_token(self, token: str, ip: str = "") -> tuple:
        """校验分享令牌 + 单 IP 绑定/核对。返回 (info, allowed)。
        首次访问绑定 IP；之后不同 IP 访问一律拒绝。"""
        info = self.get_share_token(token)
        if not info:
            return None, False
        bound = info.get("bound_ip")
        if bound:
            return info, (bound == ip)
        if not ip:
            return info, True  # 无法获取 IP 时放行（如纯内网场景），避免误杀
        # 首次访问：绑定当前 IP
        try:
            self._share_tokens_mem[token]["bound_ip"] = ip
        except Exception:
            pass
        try:
            conn = self._conn_get()
            conn.execute("UPDATE share_tokens SET bound_ip=? WHERE token=?", (ip, token))
            conn.commit()
        except Exception as e:
            logger.warning(f"[图库] 绑定分享令牌 IP 失败: {e}")
        info["bound_ip"] = ip
        return info, True

    def get_share_token(self, token: str) -> dict | None:
        if not token:
            return None
        now = time.time()
        # 内存兜底优先：同一进程内创建的令牌必然命中，规避 SQLite 读写不一致
        mem = self._share_tokens_mem.get(token)
        if mem is not None:
            if (mem.get("expire_at") or 0) < now:
                self._share_tokens_mem.pop(token, None)
                return None
            return dict(mem)
        try:
            conn = self._conn_get()
            row = conn.execute(
                "SELECT token,user_id,user_name,created_at,expire_at,bound_ip FROM share_tokens WHERE token=?", (token,)
            ).fetchone()
            if not row:
                # 诊断：内存未命中 + SQLite 查无此令牌，说明令牌未持久化（或校验方与创建方非同一实例）
                logger.warning(
                    f"[图库] 分享令牌校验失败：内存与SQLite均无此令牌 "
                    f"token_head={token[:8]} token_len={len(token)} 内存令牌数={len(self._share_tokens_mem)}"
                )
                return None
            if (row["expire_at"] or 0) < time.time():
                try:
                    conn.execute("DELETE FROM share_tokens WHERE token=?", (token,))
                    conn.commit()
                except Exception:
                    pass
                return None
            return {
                "token": row["token"],
                "user_id": row["user_id"],
                "user_name": row["user_name"],
                "created_at": row["created_at"],
                "expire_at": row["expire_at"],
                "bound_ip": row["bound_ip"] if "bound_ip" in row.keys() else None,
            }
        except Exception as e:
            logger.warning(f"[图库] 读取分享令牌失败: {e}")
            return None

    def invalidate_share_token(self, token: str) -> None:
        if not token:
            return
        self._share_tokens_mem.pop(token, None)
        try:
            conn = self._conn_get()
            conn.execute("DELETE FROM share_tokens WHERE token=?", (token,))
            conn.commit()
        except Exception:
            pass

    def _share_enrich(self, conn, rows, user_id: str):
        shas = [r["sha256"] for r in rows]
        liked, fav = self._share_flags(conn, user_id, shas)
        liked_cnt, fav_cnt = {}, {}
        if shas:
            ph = ",".join("?" * len(shas))
            for r in conn.execute(f"SELECT sha256, COUNT(*) c FROM image_likes WHERE sha256 IN ({ph}) GROUP BY sha256", shas):
                liked_cnt[r["sha256"]] = int(r["c"])
            for r in conn.execute(f"SELECT sha256, COUNT(*) c FROM image_favorites WHERE sha256 IN ({ph}) GROUP BY sha256", shas):
                fav_cnt[r["sha256"]] = int(r["c"])
        out = []
        for r in rows:
            d = self._row_to_dict(r)
            d["like_count"] = liked_cnt.get(d["sha256"], 0)
            d["favorite_count"] = fav_cnt.get(d["sha256"], 0)
            d["liked"] = d["sha256"] in liked
            d["favorited"] = d["sha256"] in fav
            out.append(d)
        return out

    def world_list(self, user_id: str = "", limit: int = 40, offset: int = 0) -> dict:
        """世界：全部公开图，按热度(点赞+收藏)降序、再生成时间倒序。"""
        if not self.enabled() or not _HAS_SQLITE:
            return {"images": [], "total": 0}
        conn = self._conn_get()
        where = "i.is_public=1 AND i.deleted=0 AND i.status=0"
        base = "FROM images i WHERE " + where
        total = 0
        try:
            row = conn.execute(f"SELECT COUNT(*) c {base}").fetchone()
            total = int(row["c"]) if row else 0
        except Exception:
            pass
        rows = []
        try:
            sql = (
                "SELECT i.*, "
                "(SELECT COUNT(*) FROM image_likes l WHERE l.sha256=i.sha256) AS lc, "
                "(SELECT COUNT(*) FROM image_favorites f WHERE f.sha256=i.sha256) AS fc "
                f"{base} ORDER BY (lc+fc) DESC, i.created_at DESC LIMIT ? OFFSET ?"
            )
            rows = conn.execute(sql, (int(limit), int(offset))).fetchall()
        except Exception as e:
            logger.warning(f"[图库] 世界列表失败: {e}")
        return {"images": self._share_enrich(conn, rows, user_id), "total": total}

    def gallery_list(self, user_id: str, visibility: str = "all", limit: int = 40, offset: int = 0) -> dict:
        """图库：本人生成的图（不含回收站），按时间倒序。visibility: all/public/private。"""
        if not self.enabled() or not _HAS_SQLITE or not user_id:
            return {"images": [], "total": 0}
        conn = self._conn_get()
        where = "i.user_id=? AND i.deleted=0 AND i.status=0"
        args = [user_id]
        if visibility == "public":
            where += " AND i.is_public=1"
        elif visibility == "private":
            where += " AND i.is_public=0"
        base = "FROM images i WHERE " + where
        total = 0
        try:
            row = conn.execute(f"SELECT COUNT(*) c {base}", args).fetchone()
            total = int(row["c"]) if row else 0
        except Exception:
            pass
        rows = []
        try:
            sql = (
                "SELECT i.*, "
                "(SELECT COUNT(*) FROM image_likes l WHERE l.sha256=i.sha256) AS lc, "
                "(SELECT COUNT(*) FROM image_favorites f WHERE f.sha256=i.sha256) AS fc "
                f"{base} ORDER BY i.created_at DESC LIMIT ? OFFSET ?"
            )
            rows = conn.execute(sql, args + [int(limit), int(offset)]).fetchall()
        except Exception as e:
            logger.warning(f"[图库] 图库列表失败: {e}")
        return {"images": self._share_enrich(conn, rows, user_id), "total": total}

    def favorites_list(self, user_id: str, limit: int = 60, offset: int = 0) -> dict:
        """收藏：我收藏的图（跨用户）。返回含 owner_is_me 标记。"""
        if not self.enabled() or not _HAS_SQLITE or not user_id:
            return {"images": [], "total": 0}
        conn = self._conn_get()
        base = (
            "FROM image_favorites f JOIN images i ON i.sha256=f.sha256 "
            "WHERE f.user_id=? AND i.deleted=0 AND i.status=0 "
            # 他人收藏的图必须是公开的才展示；自己收藏的图无论公私都可看。
            # 否则作者转私有后，收藏列表仍会列出该图，但 img 接口对非 owner 私有图 403 → 破图。
            "AND (i.is_public=1 OR i.user_id=?)"
        )
        args = [user_id, user_id]
        total = 0
        try:
            row = conn.execute(f"SELECT COUNT(*) c {base}", args).fetchone()
            total = int(row["c"]) if row else 0
        except Exception:
            pass
        rows = []
        try:
            sql = (
                "SELECT i.*, "
                "(SELECT COUNT(*) FROM image_likes l WHERE l.sha256=i.sha256) AS lc, "
                "(SELECT COUNT(*) FROM image_favorites f2 WHERE f2.sha256=i.sha256) AS fc "
                f"{base} ORDER BY f.created_at DESC LIMIT ? OFFSET ?"
            )
            rows = conn.execute(sql, args + [int(limit), int(offset)]).fetchall()
        except Exception as e:
            logger.warning(f"[图库] 收藏列表失败: {e}")
        imgs = self._share_enrich(conn, rows, user_id)
        for d in imgs:
            d["owner_is_me"] = (d.get("user_id") == user_id)
        return {"images": imgs, "total": total}

    def recycle_list(self, user_id: str, limit: int = 100, offset: int = 0) -> list:
        if not self.enabled() or not _HAS_SQLITE or not user_id:
            return []
        conn = self._conn_get()
        rows = []
        try:
            rows = conn.execute(
                "SELECT * FROM images WHERE user_id=? AND deleted=1 AND status=0 ORDER BY deleted_at DESC LIMIT ? OFFSET ?",
                (user_id, int(limit), int(offset)),
            ).fetchall()
        except Exception as e:
            logger.warning(f"[图库] 回收站列表失败: {e}")
        return [self._row_to_dict(r) for r in rows]

    def profile_stats(self, user_id: str) -> dict:
        if not self.enabled() or not _HAS_SQLITE or not user_id:
            return {"total": 0, "public": 0, "private": 0, "favorites": 0,
                    "likes_given": 0, "likes_received": 0, "recycle": 0}
        conn = self._conn_get()

        def _c(sql, a=None):
            try:
                r = conn.execute(sql, a or []).fetchone()
                return int(r["c"]) if r else 0
            except Exception:
                return 0

        own = "user_id=? AND deleted=0 AND status=0"
        total = _c(f"SELECT COUNT(*) c FROM images WHERE {own}", [user_id])
        public = _c(f"SELECT COUNT(*) c FROM images WHERE {own} AND is_public=1", [user_id])
        private = _c(f"SELECT COUNT(*) c FROM images WHERE {own} AND is_public=0", [user_id])
        recycle = _c("SELECT COUNT(*) c FROM images WHERE user_id=? AND deleted=1 AND status=0", [user_id])
        favorites = _c("SELECT COUNT(*) c FROM image_favorites WHERE user_id=?", [user_id])
        likes_given = _c("SELECT COUNT(*) c FROM image_likes WHERE user_id=?", [user_id])
        likes_received = _c(
            "SELECT COUNT(*) c FROM image_likes WHERE sha256 IN (SELECT sha256 FROM images WHERE user_id=?)",
            [user_id],
        )
        user = self.get_user(user_id)
        return {"total": total, "public": public, "private": private, "favorites": favorites,
                "likes_given": likes_given, "likes_received": likes_received, "recycle": recycle,
                "user": user}

    def ensure_user(self, user_id: str, user_name: str = None, platform: str = "") -> None:
        """用户登记：首次出现时创建用户记录，之后更新昵称与最后活跃时间。

        用户第一次使用指令（出图 / 点赞 / 收藏 / 发 /萌绘）时由各业务入口调用，
        集中维护 users 表；业务数据的归属仍由各表自身的 user_id 字段承载。

        first_seen 回填：首次登记该用户时，取其历史出图 / 点赞 / 收藏中的最早时间
        作为首次使用时间（兼容用户表上线前的存量数据）；无历史则用当前时间。
        已登记用户若发现更早的历史时间，也一并回填校正。
        """
        if not user_id:
            return
        try:
            conn = self._conn_get()
            now = time.time()
            existed = conn.execute("SELECT first_seen FROM users WHERE user_id=?", (user_id,)).fetchone()
            # 历史最早活动时间：出图 / 点赞 / 收藏 三者取最早
            hist = conn.execute(
                "SELECT MIN(t) AS t FROM ("
                " SELECT created_at AS t FROM images WHERE user_id=?"
                " UNION ALL SELECT created_at FROM image_likes WHERE user_id=?"
                " UNION ALL SELECT created_at FROM image_favorites WHERE user_id=?"
                ")",
                (user_id, user_id, user_id),
            ).fetchone()
            hist_ts = hist["t"] if hist and hist["t"] else None
            if existed is None:
                first = hist_ts if hist_ts is not None else now
                conn.execute(
                    "INSERT OR IGNORE INTO users(user_id,user_name,platform,first_seen,last_seen) VALUES(?,?,?,?,?)",
                    (user_id, user_name, platform or "", first, now),
                )
            else:
                cur_first = existed["first_seen"] or 0
                if hist_ts is not None and hist_ts < cur_first:
                    conn.execute(
                        "UPDATE users SET user_name=COALESCE(?,user_name), platform=COALESCE(?,platform), "
                        "last_seen=?, first_seen=? WHERE user_id=?",
                        (user_name, platform or "", now, hist_ts, user_id),
                    )
                else:
                    conn.execute(
                        "UPDATE users SET user_name=COALESCE(?,user_name), platform=COALESCE(?,platform), "
                        "last_seen=? WHERE user_id=?",
                        (user_name, platform or "", now, user_id),
                    )
            conn.commit()
        except Exception as e:
            logger.warning(f"[图库] 用户登记失败: {e}")

    def get_user(self, user_id: str) -> dict | None:
        """读取用户元数据（users 表）。"""
        if not user_id:
            return None
        try:
            conn = self._conn_get()
            row = conn.execute("SELECT * FROM users WHERE user_id=?", (user_id,)).fetchone()
            if not row:
                return None
            return {
                "user_id": row["user_id"],
                "user_name": row["user_name"],
                "platform": row["platform"],
                "first_seen": row["first_seen"],
                "last_seen": row["last_seen"],
            }
        except Exception:
            return None

    def set_public(self, sha256: str, on: bool, owner: str = "") -> bool:
        if not sha256:
            return False
        try:
            conn = self._conn_get()
            if owner:
                r = conn.execute("SELECT 1 FROM images WHERE sha256=? AND user_id=?", (sha256, owner)).fetchone()
                if not r:
                    return False
            conn.execute("UPDATE images SET is_public=? WHERE sha256=?", (1 if on else 0, sha256))
            conn.commit()
            return True
        except Exception as e:
            logger.warning(f"[图库] 设置公开失败: {e}")
            return False

    def recycle(self, sha256: str, owner: str = "") -> bool:
        """移到回收站（分享图库删除）。仅本人可操作。"""
        if not sha256:
            return False
        try:
            conn = self._conn_get()
            if owner:
                r = conn.execute("SELECT 1 FROM images WHERE sha256=? AND user_id=?", (sha256, owner)).fetchone()
                if not r:
                    return False
            conn.execute("UPDATE images SET deleted=1, deleted_at=? WHERE sha256=? AND deleted=0", (time.time(), sha256))
            conn.commit()
            return True
        except Exception as e:
            logger.warning(f"[图库] 移入回收站失败: {e}")
            return False

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
            # 公开图(is_public)与全局图(is_global)所有人可见；私有/非全局图仅本人。
            where += " AND (is_public=1 OR is_global=1 OR user_id=?)"
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
        nsfw: str = "",
        tag: str = "",
    ) -> int:
        """与 search 相同的过滤条件，返回命中的总条数（用于 WebUI 分页显示 total）。
        owner: 用户隔离标识，与 search 保持一致。
        session: 会话ID；非空时额外按 session_id 过滤（用于「仅当前会话」视图，
        cross_session=false 场景）。注意：这只是检索范围，不改变权限——
        owner（user_id 归属）与 is_public 过滤始终保留。
        nsfw: ""=不过滤；"0"=仅常规；"1"=仅NSFW。
        tag: 按标签精确筛选，与 search 保持一致。"""
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
            # 会话范围过滤：非空时统计「当前会话内的图」「公开图(is_public=1)」或
            # 「全局图(is_global=1)」。公开/全局图跨会话出现在列表/搜索里（开放到群聊/全局共享）。
            # 仅作为检索范围缩小，不替代 owner 权限过滤。
            sql += " AND (session_id=? OR is_public=1 OR is_global=1)"
            args.append(session)
        if keyword and keyword.strip():
            kw = f"%{keyword.strip()}%"
            sql += (
                " AND (prompt LIKE ? OR prompt_raw LIKE ? OR workflow LIKE ?"
                " OR sha256 IN (SELECT sha256 FROM image_tags WHERE tag LIKE ?))"
            )
            args += [kw, kw, kw, kw]
        if tag and tag.strip():
            sql += " AND EXISTS (SELECT 1 FROM image_tags t WHERE t.sha256=images.sha256 AND t.tag=?)"
            args.append(tag.strip())
        if type:
            sql += " AND source=?"
            args.append(type)
        if starred_only:
            sql += " AND starred=1"
        if user_filter and user_filter.strip():
            uf = f"%{user_filter.strip()}%"
            sql += " AND (user_id LIKE ? OR user_name LIKE ?)"
            args += [uf, uf]
        nsfw_f = str(nsfw or "").strip()
        if nsfw_f == "0":
            sql += " AND nsfw=0"
        elif nsfw_f == "1":
            sql += " AND nsfw=1"
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
        nsfw: str = "",
        tag: str = "",
    ) -> list[dict]:
        """按 prompt LIKE 检索（中文优先）。type: gen/ref/user/None(全部)。
        trash=True 时只查已移入回收站(deleted=1)的图片；否则默认只看未删除的。
        owner: 用户隔离标识。传入当前用户ID后，只检索该用户的图片（含历史无主图，
        即 user_id 为空或相等的），避免跨用户串图。
        session: 会话ID；非空时额外按 session_id 过滤（用于「仅当前会话」视图，
        cross_session=false 场景）。这是检索范围缩小，不改变 owner 权限语义。
        nsfw: ""=不过滤；"0"=仅常规（nsfw=0）；"1"=仅NSFW（nsfw=1）。
        tag: 按标签精确筛选。非空时仅返回带该标签的图片（精确匹配 image_tags.tag）。
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
            # 会话范围过滤：非空时检索「当前会话内的图」「公开图(is_public=1)」或
            # 「全局图(is_global=1)」。公开/全局图跨会话出现在列表/搜索里（开放到群聊/全局共享）。
            # 仅缩小检索范围，不替代 owner（user_id）权限隔离。
            sql += " AND (session_id=? OR is_public=1 OR is_global=1)"
            args.append(session)
        if keyword and keyword.strip():
            kw = f"%{keyword.strip()}%"
            sql += (
                " AND (prompt LIKE ? OR prompt_raw LIKE ? OR workflow LIKE ?"
                " OR sha256 IN (SELECT sha256 FROM image_tags WHERE tag LIKE ?))"
            )
            args += [kw, kw, kw, kw]
        if tag and tag.strip():
            sql += " AND EXISTS (SELECT 1 FROM image_tags t WHERE t.sha256=images.sha256 AND t.tag=?)"
            args.append(tag.strip())
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
        nsfw_f = str(nsfw or "").strip()
        if nsfw_f == "0":
            sql += " AND nsfw=0"
        elif nsfw_f == "1":
            sql += " AND nsfw=1"
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

    def set_nsfw_blur(self, sha256: str, on: int = 1) -> bool:
        """单图设置「NSFW 模糊」覆盖（nsfw_blur 字段）。on=1 模糊，on=0 不模糊。

        nsfw_blur 为 NULL 时表示跟随全局默认；显式设置为 0/1 表示单图强制不模糊/模糊。
        """
        if not sha256:
            return False
        conn = self._conn_get()
        try:
            # 支持前缀匹配：先解析完整 sha，避免前缀 UPDATE 命中多条
            row = conn.execute(
                "SELECT sha256 FROM images WHERE sha256 LIKE ? ORDER BY sha256 LIMIT 1",
                (f"{sha256.strip()}%",),
            ).fetchone()
            if not row:
                return False
            conn.execute(
                "UPDATE images SET nsfw_blur=? WHERE sha256=?",
                (1 if on else 0, row["sha256"]),
            )
            conn.commit()
            return True
        except Exception as e:
            logger.warning(f"[图库] 设置 NSFW 模糊失败: {e}")
            return False

    def clear_nsfw_blur(self, sha256: str) -> bool:
        """清除单图的 NSFW 模糊覆盖，恢复跟随全局默认（置 NULL）。"""
        if not sha256:
            return False
        conn = self._conn_get()
        try:
            row = conn.execute(
                "SELECT sha256 FROM images WHERE sha256 LIKE ? ORDER BY sha256 LIMIT 1",
                (f"{sha256.strip()}%",),
            ).fetchone()
            if not row:
                return False
            conn.execute("UPDATE images SET nsfw_blur=NULL WHERE sha256=?", (row["sha256"],))
            conn.commit()
            return True
        except Exception as e:
            logger.warning(f"[图库] 清除 NSFW 模糊失败: {e}")
            return False

    def set_nsfw(self, sha256: str, on: int = 1) -> bool:
        """人工直接标记/取消单图的 NSFW（误判纠正），绕过自动检测模型。

        - ``on=1``：标记为 NSFW；``on=0``：取消 NSFW。
        - 同时 ``nsfw_checked=1``：表示人工已确认，避免后续一键扫描把人工标记覆盖回去
          （扫描的 where 默认只扫 ``nsfw_checked=0``，已人工确认的图不会被重扫）。
        - 不修改 ``nsfw_score``（人工标记无检测分数）。
        返回是否成功（False = 未找到该图）。
        """
        if not sha256:
            return False
        conn = self._conn_get()
        try:
            row = conn.execute(
                "SELECT sha256 FROM images WHERE sha256 LIKE ? ORDER BY sha256 LIMIT 1",
                (f"{sha256.strip()}%",),
            ).fetchone()
            if not row:
                return False
            conn.execute(
                "UPDATE images SET nsfw=?, nsfw_checked=1 WHERE sha256=?",
                (1 if on else 0, row["sha256"]),
            )
            conn.commit()
            return True
        except Exception as e:
            logger.warning(f"[图库] 人工标记 NSFW 失败: {e}")
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
            try:
                nsfw_count = conn.execute(
                    "SELECT COUNT(*) c FROM images WHERE nsfw=1 AND deleted=0"
                ).fetchone()["c"]
            except Exception:
                nsfw_count = 0
            try:
                nsfw_unchecked = conn.execute(
                    "SELECT COUNT(*) c FROM images WHERE nsfw_checked=0 AND deleted=0"
                ).fetchone()["c"]
            except Exception:
                nsfw_unchecked = 0
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
            "nsfw_count": int(nsfw_count),
            "nsfw_unchecked": int(nsfw_unchecked),
        }

    def workflow_stats(self, top: int = 3, days: int = 0) -> list[dict]:
        """按工作流统计指定时间范围内的成功出图数量与平均耗时，按数量倒序取前 top 个。

        只统计成功生成的成品图（source='gen' 且 status=0 且未删除）。
        days 语义与 user_ranking 一致：None=全部，0=今天，N=最近 N 天。
        返回 [{workflow, count, avg_sec}]；无记录或异常时返回空列表。
        供「/绘图统计」指令使用。
        """
        if not self.enabled() or not _HAS_SQLITE:
            return []
        conn = self._conn_get()
        try:
            since_ts = self._stats_since_ts(days)
            sql = (
                "SELECT workflow, COUNT(*) AS c, AVG(cost_sec) AS avg_sec "
                "FROM images "
                "WHERE source=? AND status=0 AND deleted=0 "
                "AND workflow IS NOT NULL AND workflow<>'' "
            )
            params: list = [SRC_GEN]
            if since_ts is not None:
                sql += " AND created_at>=? "
                params.append(since_ts)
            sql += "GROUP BY workflow ORDER BY c DESC, MAX(created_at) DESC LIMIT ?"
            params.append(int(max(1, top)))
            rows = conn.execute(sql, tuple(params)).fetchall()
            return [
                {
                    "workflow": r["workflow"],
                    "count": int(r["c"]),
                    "avg_sec": round(float(r["avg_sec"] or 0), 1),
                }
                for r in rows
            ]
        except Exception as e:
            logger.warning(f"[图库] 工作流统计失败: {e}")
            return []

    def range_stats(self, start_ts: float | None = None, end_ts: float | None = None, top: int = 5) -> dict:
        """统计指定时间区间 ``[start_ts, end_ts)`` 内的成功成品图。

        只统计成功生成的成品图（source='gen' 且 status=0 且未删除）。
        ``start_ts``/``end_ts`` 为本地时间戳，None 表示不设边界（end_ts 默认 now）。
        返回 ``{"total": int, "workflows": [{workflow, count, avg_sec}]}``；
        无记录或异常时 total=0、workflows=[]。供「/绘图统计」指令按昨天/周等范围统计。
        """
        if not self.enabled() or not _HAS_SQLITE:
            return {"total": 0, "workflows": []}
        conn = self._conn_get()
        try:
            base = "source=? AND status=0 AND deleted=0"
            params: list = [SRC_GEN]
            if start_ts is not None:
                base += " AND created_at>=?"
                params.append(start_ts)
            if end_ts is not None:
                base += " AND created_at<?"
                params.append(end_ts)
            total = conn.execute(
                f"SELECT COUNT(*) AS c FROM images WHERE {base}", tuple(params)
            ).fetchone()["c"]
            sql = (
                f"SELECT workflow, COUNT(*) AS c, AVG(cost_sec) AS avg_sec "
                f"FROM images WHERE {base} AND workflow IS NOT NULL AND workflow<>'' "
                f"GROUP BY workflow ORDER BY c DESC, MAX(created_at) DESC LIMIT ?"
            )
            rows = conn.execute(sql, (*params, int(max(1, top)))).fetchall()
            workflows = [
                {
                    "workflow": r["workflow"],
                    "count": int(r["c"]),
                    "avg_sec": round(float(r["avg_sec"] or 0), 1),
                }
                for r in rows
            ]
            return {"total": int(total), "workflows": workflows}
        except Exception as e:
            logger.warning(f"[图库] 区间统计失败: {e}")
            return {"total": 0, "workflows": []}

    # ------------------------------------------------------------------ #
    # 用户生图统计（WebUI「统计」页）
    # ------------------------------------------------------------------ #
    def user_ranking(self, days: int | None = None, limit: int = 50,
                     merge_alsoknown: list[str] | None = None,
                     start_ts: float | None = None, end_ts: float | None = None) -> dict:
        """按用户统计生图数量排行（只统计成功生成的成品图 source='gen' 且 status=0）。

        days: None=全部；0=今天（自然日，本地时区）；其他正整数=最近 N 天（含今天）。
        start_ts/end_ts: 可选。显式起始/结束时间戳（end 不含），提供时优先级高于
        ``days``，用于「昨天」等精确区间统计。merge_alsoknown: 可选。给出一组
        「其他插件/别名」名称（如 ["PrivateCompanion"]），命中 user_name 的这些记录
        会被合并成一行（user_id 用逗号拼接、count 求和），便于把同一插件/非真人来源的
        分散记录整合。传空列表/None 则不合并。
        返回 {"scope": str, "total": int, "rows": [{user_id, user_name, count, rank}]}
        """
        if not self.enabled() or not _HAS_SQLITE:
            return {"scope": "all", "total": 0, "rows": []}
        conn = self._conn_get()
        try:
            since = start_ts if start_ts is not None else self._stats_since_ts(days)
            params: list = []
            where = "source=? AND status=0 AND deleted=0"
            params.append(SRC_GEN)
            if since is not None:
                where += " AND created_at>=?"
                params.append(since)
            if end_ts is not None:
                where += " AND created_at<?"
                params.append(float(end_ts))
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

"""基础工作流库存储（v7.0.0）。

「基础工作流」= 上传的 ComfyUI API 格式 JSON + 解析注记（workflow_parser），
是出图工作流配置的引用来源，**替代**手动往 workflow/ 目录拷文件的做法。

文件关联：上传的原始文件落盘到 data_dir/workflow_uploads/（永不修改），
数据库记录与其一一对应（stored_file + sha256）；解析注记从该文件生成，
解析逻辑升级后可对存量记录一键重解析。

沿用插件惯例：独立 SQLite（data_dir/workflow.db），WAL + 缺列迁移。
"""

from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
import time
from pathlib import Path

try:  # 包内加载（AstrBot 以 astrbot_plugin_comfyui_anima.workflow_store 加载）
    from .workflow_parser import PARSER_VERSION, parse_workflow
except ImportError:  # 脚本/单文件加载兜底
    from workflow_parser import PARSER_VERSION, parse_workflow

logger = logging.getLogger(__name__)


class WorkflowStore:
    """基础工作流库。单线程事件循环使用。"""

    def __init__(self, data_dir: Path) -> None:
        self.data_dir = Path(data_dir)
        try:
            self.data_dir.mkdir(parents=True, exist_ok=True)
        except Exception:
            pass
        self.uploads_dir = self.data_dir / "workflow_uploads"
        try:
            self.uploads_dir.mkdir(parents=True, exist_ok=True)
        except Exception:
            pass
        self.db_path = self.data_dir / "workflow.db"
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
                logger.warning(f"[基础工作流] 开启 WAL 失败: {e}")
        return self._conn

    def _ensure_columns(self, table: str, cols: "dict[str, str]") -> None:
        """缺列则补（幂等），CharacterStore 同款迁移助手。"""
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
                    logger.info(f"【基础工作流】 迁移：{table} 补列 {_col}")
                except Exception as e:
                    if "duplicate column" not in str(e).lower():
                        logger.warning(f"【基础工作流】 迁移补列 {table}.{_col} 失败: {e}")
            conn.commit()
        except Exception as e:
            logger.warning(f"【基础工作流】 迁移检查失败（{table}）: {e}")

    def _init_db(self) -> None:
        conn = self._conn_get()
        conn.execute(
            """CREATE TABLE IF NOT EXISTS base_workflows (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                name         TEXT NOT NULL,
                file_name    TEXT DEFAULT '',
                stored_file  TEXT DEFAULT '',
                sha256       TEXT DEFAULT '',
                wf_json      TEXT NOT NULL DEFAULT '',
                roles_json   TEXT DEFAULT '{}',
                parse_ok     INTEGER NOT NULL DEFAULT 0,
                parse_msg    TEXT DEFAULT '',
                civitai_url  TEXT DEFAULT '',
                image        TEXT DEFAULT '',
                description  TEXT DEFAULT '',
                basemodel_id INTEGER NOT NULL DEFAULT 0,
                created_at   REAL NOT NULL DEFAULT 0,
                updated_at   REAL NOT NULL DEFAULT 0
            )"""
        )
        self._ensure_columns("base_workflows", {
            "civitai_url": "TEXT DEFAULT ''",
            "image": "TEXT DEFAULT ''",
            "description": "TEXT DEFAULT ''",
            "basemodel_id": "INTEGER NOT NULL DEFAULT 0",
            # v7.7.99：记录该行注记由哪个解析器版本算出（0/缺失 = 老数据）
            "parser_ver": "INTEGER NOT NULL DEFAULT 0",
        })
        conn.commit()
        self._reparse_outdated()

    @staticmethod
    def _row_to_dict(row, with_json: bool = False) -> dict:
        d = dict(row)
        try:
            d["roles"] = json.loads(d.get("roles_json") or "{}")
        except Exception:
            d["roles"] = {}
        d["parse_ok"] = bool(d.get("parse_ok"))
        if not with_json:
            d.pop("wf_json", None)
        d.pop("roles_json", None)
        return d

    # ------------------------------------------------------------------ #
    def list_all(self) -> list[dict]:
        conn = self._conn_get()
        rows = conn.execute(
            "SELECT * FROM base_workflows ORDER BY updated_at DESC"
        ).fetchall()
        return [self._row_to_dict(r) for r in rows]

    def get(self, wf_id: int, with_json: bool = False) -> dict | None:
        conn = self._conn_get()
        row = conn.execute(
            "SELECT * FROM base_workflows WHERE id=?", (int(wf_id),)
        ).fetchone()
        if not row:
            return None
        rec = self._row_to_dict(row, with_json)
        # v7.7.99：注记由旧版解析器算出时**就地重算**再返回。
        # 出图（含第三方插件调用）走的就是这里，所以规则升级后第一次出图就会拿到
        # 正确节点，不会出现「提示词写到旧节点、日志一切正常、画面不理会描述」。
        if with_json and self._is_stale(rec):
            rec = self._reparse_record(rec)
        return rec

    # ---- 解析注记版本（v7.7.99）---------------------------------------- #
    @staticmethod
    def _is_stale(rec: dict) -> bool:
        try:
            return int(rec.get("parser_ver") or 0) < PARSER_VERSION
        except (TypeError, ValueError):
            return True

    def _reparse_record(self, rec: dict) -> dict:
        """按当前解析器重算一条记录的注记并写库，返回刷新后的记录副本。"""
        wf_id = int(rec.get("id") or 0)
        try:
            prompt = json.loads(rec.get("wf_json") or "{}")
        except Exception as e:
            logger.warning(f"【基础工作流】 自动重解析失败（JSON 无法解析）: {e}")
            return rec
        roles, errors = parse_workflow(prompt)
        now = time.time()
        try:
            conn = self._conn_get()
            conn.execute(
                "UPDATE base_workflows SET roles_json=?, parse_ok=?, parse_msg=?, "
                "parser_ver=?, updated_at=? WHERE id=?",
                (json.dumps(roles or {}, ensure_ascii=False), 0 if errors else 1,
                 "；".join(errors), PARSER_VERSION, now, wf_id),
            )
            conn.commit()
        except Exception as e:
            logger.warning(f"【基础工作流】 自动重解析写库失败: {e}")
        out = dict(rec)
        out["roles"] = roles or {}
        out["parse_ok"] = not errors
        out["parse_msg"] = "；".join(errors)
        out["parser_ver"] = PARSER_VERSION
        out["updated_at"] = now
        return out

    def _reparse_outdated(self) -> None:
        """启动时把存量记录一次性升到当前解析器版本（v7.7.99）。

        只处理 parser_ver 落后的行；解析规则没变时（版本相同）一次 UPDATE 都不做。
        """
        try:
            conn = self._conn_get()
            rows = conn.execute(
                "SELECT id, parser_ver FROM base_workflows"
            ).fetchall()
            stale = [
                int(r["id"]) for r in rows
                if int((r["parser_ver"] if r["parser_ver"] is not None else 0)) < PARSER_VERSION
            ]
        except Exception as e:
            logger.warning(f"【基础工作流】 解析版本检查失败（忽略）: {e}")
            return
        if not stale:
            return
        done = 0
        for _id in stale:
            try:
                row = self._conn_get().execute(
                    "SELECT * FROM base_workflows WHERE id=?", (_id,)
                ).fetchone()
                if not row:
                    continue
                self._reparse_record(self._row_to_dict(row, True))
                done += 1
            except Exception as e:
                logger.warning(f"【基础工作流】 重解析 #{_id} 失败: {e}")
        if done:
            logger.info(
                f"【基础工作流】 解析器升级（v{PARSER_VERSION}）→ 已自动重解析 "
                f"{done} 条存量工作流（节点注记已刷新，无需手动点「重新解析」）"
            )

    # ------------------------------------------------------------------ #
    def match_by_filename(self, file_name: str) -> dict | None:
        """按「原始 JSON 文件名」匹配基础工作流（旧版工作流转新版时用，v7.4.0）。

        归一化：去首尾空白、去 .json 扩展名、大小写不敏感。
        命中优先级（高分优先，同分先到先得）：
          3 = file_name 完全一致（导入时存的是上传时的原始文件名）
          2 = file_name 去扩展名后一致
          1 = 显示名 name 一致（导入时 name = 文件名 stem）
        返回命中的记录 dict，未命中返回 None。
        """
        target = str(file_name or "").strip()
        if not target:
            return None
        t_full = target.lower()
        t_stem = (Path(target).stem or target).lower()
        best, best_score = None, 0
        for rec in self.list_all():
            _fn = str(rec.get("file_name") or "").strip().lower()
            _nm = str(rec.get("name") or "").strip().lower()
            score = 0
            if _fn and _fn == t_full:
                score = 3
            elif _fn and (Path(_fn).stem or _fn) == t_stem:
                score = 2
            elif _nm and _nm in (t_stem, t_full):
                score = 1
            if score > best_score:
                best, best_score = rec, score
        return best

    # ------------------------------------------------------------------ #
    @staticmethod
    def _unique_name(conn, name: str) -> str:
        """同名（忽略大小写）已存在时，加 ` (2)` / ` (3)` 直到不冲突（v7.7.15）。"""
        for i in range(2, 100):
            cand = f"{name} ({i})"
            if not conn.execute(
                "SELECT 1 FROM base_workflows WHERE name=? COLLATE NOCASE", (cand,)
            ).fetchone():
                return cand
        return f"{name} ({int(time.time())})"

    def import_json(self, name: str, json_text: str, original_filename: str = "",
                    force: bool = False) -> tuple[int | None, dict | None, str | None]:
        """上传/更新基础工作流（兼容包装：只要 (id, roles, error) 的调用方用这个）。

        流程：解析 JSON → 解析校验（失败拒绝）→ 原始文件落盘 → 入库（或更新同名）。
        """
        wf_id, roles, err, _info = self.import_json_ex(
            name, json_text, original_filename, force
        )
        return wf_id, roles, err

    def import_json_ex(self, name: str, json_text: str, original_filename: str = "",
                       force: bool = False
                       ) -> tuple[int | None, dict | None, str | None, dict]:
        """上传基础工作流，返回 (id, roles, error, info)。

        入库规则（v7.7.15 修复「连续上传第二个文件把上一个覆盖掉」）：
          · 同名 + **同一个文件名**（或两边都没文件名）→ **覆盖更新**（重复上传同一文件的新版本）；
          · 同名但**明显是另一个文件**（两边文件名都非空且不同）→ **新增**，名字去重成
            `xxx (2)` —— 不同工作流撞名时不再把旧记录（连同封面 / 底模关联）一起冲掉；
          · 不同名 → 新增。

        `info = {"updated", "renamed", "name", "id", "msg"}`：供 WebUI 明确提示「更新」还是「新增」。
        """
        name = (name or "").strip()
        if not name:
            return None, None, "名称不能为空", {}
        try:
            prompt = json.loads(json_text)
        except Exception as e:
            return None, None, f"JSON 解析失败: {e}", {}
        roles, errors = parse_workflow(prompt)
        if errors and not force:
            return None, None, "；".join(errors), {}

        # 原始文件落盘（文件关联：原始文件是唯一真相源头，永不再修改）
        sha = hashlib.sha256(json_text.encode("utf-8")).hexdigest()[:16]
        orig_stem = Path(original_filename or name).stem or "workflow"
        safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in orig_stem)[:60] or "workflow"
        stored_name = f"{safe}_{sha}{'.json'}"
        stored_path = self.uploads_dir / stored_name
        try:
            stored_path.write_text(json_text, encoding="utf-8")
        except Exception as e:
            return None, None, f"原始文件落盘失败: {e}", {}

        now = time.time()
        conn = self._conn_get()
        # 同名（显示名）已存在 → 视情况「更新」或「改名新增」
        row = conn.execute(
            "SELECT id, file_name FROM base_workflows WHERE name=? COLLATE NOCASE", (name,)
        ).fetchone()
        _want_fn = str(original_filename or "").strip()
        _old_fn = str(row["file_name"] or "").strip() if row else ""
        renamed = False
        if row and _want_fn and _old_fn and _want_fn != _old_fn:
            # 名字撞了但上传的是**另一个文件** → 不覆盖，改名新增（否则旧记录会被静默冲掉）
            _dup_name = name
            name = self._unique_name(conn, name)
            row = None
            renamed = True
            logger.info(
                f"【基础工作流】 名称「{_dup_name}」已被另一份文件占用"
                f"（{_old_fn} ≠ {_want_fn}）→ 本次新增为「{name}」，不覆盖旧记录"
            )
        updated = False
        if row:
            conn.execute(
                """UPDATE base_workflows SET file_name=?, stored_file=?, sha256=?, wf_json=?,
                   roles_json=?, parse_ok=?, parse_msg=?, parser_ver=?, updated_at=? WHERE id=?""",
                (original_filename or "", stored_name, sha, json_text,
                 json.dumps(roles or {}, ensure_ascii=False), 0 if errors else 1,
                 "；".join(errors), PARSER_VERSION, now, row["id"]),
            )
            wf_id = int(row["id"])
            updated = True
            msg = "已覆盖更新同名基础工作流"
        else:
            cur = conn.execute(
                """INSERT INTO base_workflows (name, file_name, stored_file, sha256, wf_json,
                   roles_json, parse_ok, parse_msg, parser_ver, created_at, updated_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                (name, original_filename or "", stored_name, sha, json_text,
                 json.dumps(roles or {}, ensure_ascii=False), 0 if errors else 1,
                 "；".join(errors), PARSER_VERSION, now, now),
            )
            wf_id = int(cur.lastrowid)
            msg = (f"同名工作流已存在，已新增为「{name}」" if renamed else "已新增基础工作流")
        conn.commit()
        return wf_id, roles, None, {
            "updated": updated, "renamed": renamed, "name": name, "id": wf_id, "msg": msg,
        }

    def update_meta(self, wf_id: int, fields: dict) -> str | None:
        """更新元数据字段（白名单：name / civitai_url / image / description / basemodel_id）。"""
        allow = {
            "name": "name",
            "civitai_url": "civitai_url",
            "image": "image",
            "description": "description",
            "basemodel_id": "basemodel_id",
        }
        sets, vals = [], []
        for k, v in (fields or {}).items():
            if k == "basemodel_id":
                try:
                    sets.append("basemodel_id=?")
                    vals.append(int(v or 0))
                except (TypeError, ValueError):
                    pass
            elif k in allow:
                sets.append(f"{allow[k]}=?")
                vals.append(str(v or "").strip())
        if not sets:
            return "没有可更新的字段"
        try:
            conn = self._conn_get()
            sets.append("updated_at=?")
            vals.append(time.time())
            vals.append(int(wf_id))
            conn.execute(f"UPDATE base_workflows SET {', '.join(sets)} WHERE id=?", vals)
            conn.commit()
            return None
        except Exception as e:
            return str(e)

    def reparse(self, wf_id: int) -> tuple[dict | None, str | None]:
        """用当前版本的解析器重跑存量记录（手动「重新解析」按钮）。

        v7.7.99：改为直读原始行再走 `_reparse_record`（顺带写 parser_ver）——
        此前经 `get(with_json=True)` 拿记录，而 get 现在会对落后版本自动重算，
        等于同一条解析跑两遍。
        """
        row = self._conn_get().execute(
            "SELECT * FROM base_workflows WHERE id=?", (int(wf_id),)
        ).fetchone()
        if not row:
            return None, "记录不存在"
        rec = self._row_to_dict(row, with_json=True)
        try:
            json.loads(rec.get("wf_json") or "{}")
        except Exception as e:
            return None, f"JSON 解析失败: {e}"
        fresh = self._reparse_record(rec)
        err = fresh.get("parse_msg") or None
        return (fresh.get("roles") or None), err

    def delete(self, wf_id: int, referenced_names: list[str] | None = None) -> str | None:
        """删除。referenced_names 非空（有实例引用）时拒绝删除（删除保护）。"""
        if referenced_names:
            return f"该基础工作流正被以下出图工作流引用，无法删除：{'、'.join(referenced_names)}"
        try:
            conn = self._conn_get()
            conn.execute("DELETE FROM base_workflows WHERE id=?", (int(wf_id),))
            conn.commit()
            return None
        except Exception as e:
            return str(e)

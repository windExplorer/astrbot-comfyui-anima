"""在线生图工作台（studio）：WebUI 侧直接出图的任务管理与提示词模板库。

v7.9.0 新增顶层模块。设计口径见 docs/TODO-在线生图.md，要点：

- 生图执行**不另起炉灶**：任务协程复用 ``main._do_draw(external=True,
  source=webui_studio)``，与聊天侧共用同一条出图链路（LLM 润色分发 /
  LoRA 注入 / 尺寸开关 / 参考图上传 / NSFW / 图库归档 / 出图记录）。
  WebUI 层只做「参数收集 + 任务管理 + 结果展示」。
- 控制台生图**不判聊天白/黑名单、不占聊天配额**（``_do_draw`` 按 source
  放行，main.py；WebUI 本身有独立访问口令保护），图库归属记
  ``plugin:webui_studio``。
- 任务历史仅内存（重启清空，UI 有提示）；成品图永久在图库，前端凭 sha
  走 ``/img/{sha}``（独立通道）或 ``gallery/thumb``（内嵌通道）展示。
- 两条 WebUI 通道共用本模块：``webui_api.WebUIApi`` 方法一行委托 +
  ``standalone_webui`` adapter 复刻（与 lora/character 端点同一套路）。
"""

from __future__ import annotations

import asyncio
import base64
import json
import os
import random
import re
import shutil
import sqlite3
import time
import uuid
from pathlib import Path

try:
    from astrbot.api import logger
except ImportError:  # pragma: no cover - 非 AstrBot 环境
    import logging

    logger = logging.getLogger("studio_api")

# ────────────────────────────────────────────────────────────────── #
# 常量
# ────────────────────────────────────────────────────────────────── #

# 生图来源标识：_do_draw(external=True) 时图库归属记 plugin:<source>，
# 同时是 main.py 准入放行的判定键（跳过聊天白/黑名单与配额）。
SOURCE_WEBUI_STUDIO = "webui_studio"

# 任务历史上限（内存）。重启清空；成品图在图库不受影响。
JOB_HISTORY_LIMIT = 50
# 参考图登记上限（data/temp/studio_ref_*）。超出按最旧淘汰并删除文件。
REF_KEEP_LIMIT = 40
# 参考图最长保留时长（秒）：提交任务后源文件已被 ComfyUI 复制/归档，
# 超时未使用的登记视为残留。
REF_TTL_SECONDS = 2 * 3600
# 单张参考图解码后大小上限（与独立通道 _MAX_UPLOAD_BYTES 的 base64 余量一致）。
REF_MAX_BYTES = 12 * 1024 * 1024

_ALLOWED_MIME_EXT = {
    "image/png": "png",
    "image/jpeg": "jpg",
    "image/webp": "webp",
    "image/gif": "gif",
}

_LLM_PROMPT_MODES = ("expand", "translate", "anima", "natural", "qwen_edit")

# 采样器/调度器枚举缓存：{server_key: {samplers, schedulers}}（/object_info 很大，绝不反复拉）
_SAMPLERS_CACHE: dict[str, dict] = {}

# 「AI 想画面」自由发挥时的题材建议池（每次随机挑一个，让重复点击的画面不重样）
_IMAGINE_THEMES = (
    "纯风景（无人）", "一个女孩", "多个角色", "动漫角色", "游戏角色", "动物萌宠",
    "都市夜景", "和风庭院", "科幻机械", "奇幻魔法", "日常生活", "神话生物",
    "四季变换", "美食静物", "古风侠客", "星空宇宙",
)


# ────────────────────────────────────────────────────────────────── #
# 内置提示词模板（首次初始化播种；reseed 补齐缺失项）
# ────────────────────────────────────────────────────────────────── #

_TEMPLATE_SEEDS: list[dict] = [
    # 画质
    {"category": "画质", "name": "基础画质", "content": "masterpiece, best quality, amazing quality, very aesthetic, absurdres"},
    {"category": "画质", "name": "高精细", "content": "ultra-detailed, intricate details, detailed skin, detailed eyes, highres"},
    {"category": "画质", "name": "胶片质感", "content": "film grain, soft focus, analog photo feel, muted colors"},
    {"category": "画质", "name": "干净利落", "content": "clean sharp lines, crisp shading, high contrast"},
    # 视角构图
    {"category": "视角构图", "name": "正面半身", "content": "upper body, looking at viewer, face focus"},
    {"category": "视角构图", "name": "全身立绘", "content": "full body, standing, solo, simple background"},
    {"category": "视角构图", "name": "面部特写", "content": "close-up, face focus, depth of field, bokeh"},
    {"category": "视角构图", "name": "俯视", "content": "from above"},
    {"category": "视角构图", "name": "仰视", "content": "from below"},
    {"category": "视角构图", "name": "回眸背影", "content": "from behind, looking back, turned around"},
    {"category": "视角构图", "name": "动态构图", "content": "dynamic angle, foreshortening, action pose, motion blur"},
    # 光影氛围
    {"category": "光影氛围", "name": "黄昏逆光", "content": "sunset, backlighting, orange sky, rim lighting"},
    {"category": "光影氛围", "name": "夜景霓虹", "content": "night, neon lights, city lights, reflective ground"},
    {"category": "光影氛围", "name": "室内柔光", "content": "indoors, soft lighting, warm colors, window light"},
    {"category": "光影氛围", "name": "星空月夜", "content": "night sky, starry sky, moonlight, glowing particles"},
    {"category": "光影氛围", "name": "雨天", "content": "rain, wet ground, puddle reflections, holding umbrella"},
    {"category": "光影氛围", "name": "樱花", "content": "cherry blossoms, falling petals, spring, soft pink palette"},
    # 服装配饰
    {"category": "服装配饰", "name": "水手服", "content": "sailor school uniform, pleated skirt"},
    {"category": "服装配饰", "name": "和风", "content": "kimono, wide sleeves, japanese clothes, floral print"},
    {"category": "服装配饰", "name": "休闲便装", "content": "casual, hoodie, denim shorts, sneakers"},
    {"category": "服装配饰", "name": "裙装", "content": "dress, frills, lace trim, ribbon"},
    {"category": "服装配饰", "name": "冬装", "content": "winter coat, scarf, gloves, snow"},
    # 表情动作
    {"category": "表情动作", "name": "开心微笑", "content": "smile, open mouth, happy, sparkling eyes"},
    {"category": "表情动作", "name": "害羞脸红", "content": "blush, embarrassed, looking away, fidgeting"},
    {"category": "表情动作", "name": "惊讶", "content": "surprised, wide eyes, open mouth"},
    {"category": "表情动作", "name": "睡觉", "content": "sleeping, lying on bed, closed eyes, blanket"},
    {"category": "表情动作", "name": "吃东西", "content": "eating, holding food, chewing, happy expression"},
    {"category": "表情动作", "name": "托腮", "content": "resting chin on hands, elbow on table, gentle smile"},
    # 风格画面
    {"category": "风格画面", "name": "水彩画风", "content": "watercolor (medium), painting (medium), soft edges, paper texture"},
    {"category": "风格画面", "name": "赛博朋克", "content": "cyberpunk, futuristic city, hologram, techwear"},
    {"category": "风格画面", "name": "吉卜力风", "content": "lush scenery, soft colors, hand-drawn feel, detailed background"},
    {"category": "风格画面", "name": "简约背景", "content": "simple background, flat color, minimal"},
    # 负向模板
    {"category": "负向", "name": "通用画质负向", "is_negative": True,
     "content": "lowres, worst quality, bad anatomy, bad hands, extra digits, fewer digits, watermark, signature, jpeg artifacts"},
    {"category": "负向", "name": "多人防串", "is_negative": True,
     "content": "multiple views, multiple girls, 2girls, clone, doubled body"},
    {"category": "负向", "name": "文字杂物", "is_negative": True,
     "content": "text, logo, ui, speech bubble, username"},
]


# ────────────────────────────────────────────────────────────────── #
# 模板库（SQLite：data_dir/studio.db）
# ────────────────────────────────────────────────────────────────── #

class StudioStore:
    """预设提示词模板库。表结构稳定、按 (category, name) 补齐播种。"""

    def __init__(self, data_dir) -> None:
        self.db_path = Path(data_dir) / "studio.db"
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=10)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._conn() as conn:
            conn.execute(
                """CREATE TABLE IF NOT EXISTS studio_templates (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL,
                    category TEXT NOT NULL DEFAULT '',
                    content TEXT NOT NULL DEFAULT '',
                    is_negative INTEGER NOT NULL DEFAULT 0,
                    enabled INTEGER NOT NULL DEFAULT 1,
                    sort_order INTEGER NOT NULL DEFAULT 0,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL
                )"""
            )
            conn.commit()
        self.reseed_defaults()

    # ---- 查询 ------------------------------------------------------ #
    def list_templates(self, include_disabled: bool = True) -> list[dict]:
        sql = "SELECT * FROM studio_templates"
        if not include_disabled:
            sql += " WHERE enabled=1"
        sql += " ORDER BY sort_order ASC, id ASC"
        with self._conn() as conn:
            rows = conn.execute(sql).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["is_negative"] = bool(d.get("is_negative"))
            d["enabled"] = bool(d.get("enabled"))
            out.append(d)
        return out

    # ---- 写入 ------------------------------------------------------ #
    def save_template(self, data: dict) -> tuple[int | None, str | None]:
        """新增/更新一条模板。data: {id?, name, category, content, is_negative?, enabled?}。"""
        name = str(data.get("name") or "").strip()
        content = str(data.get("content") or "").strip()
        if not name:
            return None, "模板名称不能为空"
        if not content:
            return None, "模板内容不能为空"
        if len(content) > 2000:
            return None, "模板内容过长（≤2000 字符）"
        category = str(data.get("category") or "").strip() or "未分类"
        is_negative = 1 if data.get("is_negative") else 0
        enabled = 0 if data.get("enabled") is False else 1
        now = time.time()
        with self._conn() as conn:
            tid = data.get("id")
            if tid:
                try:
                    tid = int(tid)
                except (TypeError, ValueError):
                    return None, "id 不合法"
                cur = conn.execute(
                    "UPDATE studio_templates SET name=?, category=?, content=?, "
                    "is_negative=?, enabled=?, updated_at=? WHERE id=?",
                    (name, category, content, is_negative, enabled, now, tid),
                )
                if cur.rowcount <= 0:
                    return None, "模板不存在"
                conn.commit()
                return tid, None
            cur = conn.execute(
                "INSERT INTO studio_templates (name, category, content, is_negative, "
                "enabled, sort_order, created_at, updated_at) VALUES (?,?,?,?,?,"
                "(SELECT COALESCE(MAX(sort_order),0)+1 FROM studio_templates),?,?)",
                (name, category, content, is_negative, enabled, now, now),
            )
            conn.commit()
            return int(cur.lastrowid or 0), None

    def delete_template(self, tid) -> str | None:
        try:
            tid = int(tid)
        except (TypeError, ValueError):
            return "id 不合法"
        with self._conn() as conn:
            cur = conn.execute("DELETE FROM studio_templates WHERE id=?", (tid,))
            conn.commit()
            if cur.rowcount <= 0:
                return "模板不存在"
        return None

    def reseed_defaults(self) -> int:
        """补齐内置模板：(category, name) 不存在才插入，用户改动不覆盖。"""
        added = 0
        now = time.time()
        with self._conn() as conn:
            existing = {
                (r["category"], r["name"])
                for r in conn.execute("SELECT category, name FROM studio_templates").fetchall()
            }
            for i, item in enumerate(_TEMPLATE_SEEDS):
                key = (item.get("category") or "", item.get("name") or "")
                if key in existing:
                    continue
                conn.execute(
                    "INSERT INTO studio_templates (name, category, content, is_negative, "
                    "enabled, sort_order, created_at, updated_at) VALUES (?,?,?,?,1,?,?,?)",
                    (
                        item.get("name") or "",
                        item.get("category") or "",
                        item.get("content") or "",
                        1 if item.get("is_negative") else 0,
                        i,
                        now,
                        now,
                    ),
                )
                added += 1
            conn.commit()
        if added:
            logger.info(f"【生图工作台】 已补齐 {added} 条内置提示词模板")
        return added


# ────────────────────────────────────────────────────────────────── #
# 任务管理器（内存）
# ────────────────────────────────────────────────────────────────── #

class StudioJobManager:
    """在线生图任务：提交 → asyncio 协程跑 _do_draw(external=True) → 轮询取结果。

    并发模型：submit 即建 task、立即执行（不排队），ComfyUI 侧自身按队列
    串行；本机仅限制「等待中的任务」数量（避免手滑连点把 ComfyUI 队列塞爆）。
    """

    # 同一时刻允许处于 queued/running 的任务数上限
    MAX_ACTIVE = 4

    def __init__(self, plugin) -> None:
        self.plugin = plugin
        self._jobs: dict[str, dict] = {}   # job_id -> job（插入序即创建序）
        self._refs: dict[str, dict] = {}   # ref_id -> {"path": str, "ts": float}

    # ---------------- 参考图 ---------------- #
    def save_ref_data_url(self, data_url: str) -> tuple[str | None, str | None]:
        """保存前端上传的参考图（data URL）到临时目录，返回 (ref_id, err)。"""
        raw = str(data_url or "").strip()
        m = re.match(r"^data:image/([a-zA-Z0-9.+-]+);base64,(.+)$", raw, re.S)
        b64_body = ""
        ext = "png"
        if m:
            ext = m.group(1).lower()
            ext = {"jpg": "jpg", "jpeg": "jpg", "jpe": "jpg"}.get(ext, ext)
            if ext not in ("png", "jpg", "webp", "gif"):
                return None, "仅支持 png / jpg / webp / gif 图片"
            b64_body = m.group(2)
        else:
            # 兼容：裸 base64（默认按 png 处理）
            b64_body = raw
        if not b64_body:
            return None, "参考图内容为空"
        try:
            blob = base64.b64decode(b64_body, validate=False)
        except Exception as e:
            return None, f"参考图解码失败: {e}"
        if not blob:
            return None, "参考图内容为空"
        if len(blob) > REF_MAX_BYTES:
            return None, f"参考图过大（> {REF_MAX_BYTES // (1024 * 1024)}MB），请压缩后再传"
        temp_dir = getattr(self.plugin, "temp_dir", None)
        if temp_dir is None:
            return None, "插件临时目录不可用"
        temp_dir = Path(temp_dir)
        temp_dir.mkdir(parents=True, exist_ok=True)
        ref_id = uuid.uuid4().hex[:12]
        path = temp_dir / f"studio_ref_{ref_id}.{ext}"
        try:
            path.write_bytes(blob)
        except Exception as e:
            return None, f"参考图写入失败: {e}"
        self._prune_refs()
        self._refs[ref_id] = {"path": str(path), "ts": time.time()}
        return ref_id, None

    def save_ref_from_gallery(self, sha: str) -> tuple[str | None, str | None]:
        """把图库已有成品图复制一份当参考图（「用作图生图」接力）。"""
        g = getattr(self.plugin, "gallery", None)
        if g is None:
            return None, "图库不可用"
        sha = str(sha or "").strip()
        if not sha:
            return None, "缺少图片 sha"
        try:
            src = g.path_of(sha)
        except Exception as e:
            return None, f"读取图库记录失败: {e}"
        if not src or not os.path.exists(str(src)):
            return None, "图库里找不到这张图（可能已被清理）"
        temp_dir = Path(getattr(self.plugin, "temp_dir"))
        temp_dir.mkdir(parents=True, exist_ok=True)
        ref_id = uuid.uuid4().hex[:12]
        dst = temp_dir / f"studio_ref_{ref_id}{Path(str(src)).suffix or '.png'}"
        try:
            shutil.copy2(str(src), str(dst))
        except Exception as e:
            return None, f"复制参考图失败: {e}"
        self._prune_refs()
        self._refs[ref_id] = {"path": str(dst), "ts": time.time()}
        return ref_id, None

    def ref_path(self, ref_id: str) -> str | None:
        rec = self._refs.get(str(ref_id or "").strip())
        if not rec:
            return None
        p = rec.get("path") or ""
        return p if p and os.path.exists(p) else None

    def _prune_refs(self) -> None:
        now = time.time()
        stale = [
            rid for rid, rec in self._refs.items()
            if now - float(rec.get("ts") or 0) > REF_TTL_SECONDS
        ]
        for rid in stale:
            rec = self._refs.pop(rid, None)
            if rec:
                try:
                    os.remove(rec["path"])
                except OSError:
                    pass
        # 超量：按登记时间最旧淘汰
        while len(self._refs) > REF_KEEP_LIMIT:
            oldest = min(self._refs.items(), key=lambda kv: kv[1].get("ts") or 0)
            rec = self._refs.pop(oldest[0], None)
            if rec:
                try:
                    os.remove(rec["path"])
                except OSError:
                    pass

    # ---------------- 参数校验 ---------------- #
    def validate_params(self, params: dict) -> tuple[dict | None, str | None]:
        """把前端参数清洗净化成提交给 _do_draw 的安全形态。

        v7.9.1：模式只有 t2i / img2img 两种——「多参考」并入图生图（多参考本就是
        图生图的超集：`_do_draw` 拿到多张参考图时会自动探测工作流的 multi_image
        节点决定注入方式，单图工作流则取第一张）。历史字面量 multi_ref 仍接受，
        归一化为 img2img。
        """
        if not isinstance(params, dict):
            return None, "参数必须是对象"
        mode = str(params.get("mode") or "t2i").strip()
        if mode not in ("t2i", "img2img", "multi_ref"):
            return None, f"不支持的模式: {mode}"
        if mode == "multi_ref":
            mode = "img2img"  # 归一化（旧会话存档兼容）
        workflow = str(params.get("workflow") or "").strip()
        if not workflow:
            return None, "请选择工作流"
        positive = str(params.get("positive") or "").strip()
        if not positive:
            return None, "正向提示词不能为空"
        if len(positive) > 6000:
            return None, "正向提示词过长（≤6000 字符）"
        negative = str(params.get("negative") or "").strip()
        if len(negative) > 4000:
            return None, "负向提示词过长（≤4000 字符）"

        def _num(key, lo, hi, cast=float, default=None):
            v = params.get(key, default)
            if v is None or v == "":
                return None
            try:
                v = cast(v)
            except (TypeError, ValueError):
                raise ValueError(f"{key} 必须是数字")
            if not (lo <= v <= hi):
                raise ValueError(f"{key} 超出范围 [{lo}, {hi}]")
            return v

        try:
            width = _num("width", 64, 4096, int)
            height = _num("height", 64, 4096, int)
            seed = _num("seed", 0, 4294967295, int)
            steps = _num("steps", 1, 100, int)
            cfg = _num("cfg", 0, 30, float)
            denoise = _num("denoise", 0, 1, float)
        except ValueError as e:
            return None, str(e)
        if (width is None) != (height is None):
            return None, "宽高必须成对提供（或都留空跟随工作流默认）"

        sampler = str(params.get("sampler") or "").strip()[:64]
        noise_schedule = str(params.get("noise_schedule") or "").strip()[:64]
        custom_size = params.get("custom_size")
        if custom_size is not None:
            custom_size = bool(custom_size)
        # v7.9.7：llm_refine 概念移除——润色是提示词区里的即用即点动作按钮
        # （用户点 🪄 就地改写输入框），提交永远用输入框最终文本（raw_prompt=True）。

        loras_in = params.get("loras") or []
        if not isinstance(loras_in, list):
            return None, "loras 必须是数组"
        loras: list[dict] = []
        seen: set[str] = set()
        for item in loras_in[:8]:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name") or "").strip()
            if not name or name in seen:
                continue
            seen.add(name)
            weight = item.get("weight")
            if weight is not None and weight != "":
                try:
                    weight = float(weight)
                except (TypeError, ValueError):
                    return None, f"LoRA「{name}」权重必须是数字"
                if not (0 <= weight <= 1.5):
                    return None, f"LoRA「{name}」权重超出范围 [0, 1.5]"
            else:
                weight = None  # 用 LoRA 库里的默认权重
            preset = str(item.get("preset") or "").strip()[:64]
            loras.append({"name": name, "weight": weight, "preset": preset})

        ref_ids = params.get("ref_ids") or []
        if not isinstance(ref_ids, list):
            return None, "ref_ids 必须是数组"
        ref_ids = [str(x).strip() for x in ref_ids if str(x).strip()]
        if mode == "img2img" and not ref_ids:
            return None, "图生图需要至少 1 张参考图"
        # 参考图数量上限在 _do_draw 内按工作流条目再裁一次（取前 N 张）；
        # 1 张=普通图生图，>1 张且工作流支持 multi_image 时自动走多参考注入

        return {
            "mode": mode,
            "workflow": workflow,
            "positive": positive,
            "negative": negative,
            "width": width,
            "height": height,
            "seed": seed or None,  # 0 视为随机（与聊天侧 seed or None 口径一致）
            "steps": steps,
            "cfg": cfg,
            "denoise": denoise,
            "sampler": sampler,
            "noise_schedule": noise_schedule,
            "custom_size": custom_size,
            "loras": loras,
            "ref_ids": ref_ids,
        }, None

    # ---------------- 任务生命周期 ---------------- #
    def submit(self, params: dict) -> tuple[str | None, str | None]:
        clean, err = self.validate_params(params)
        if err:
            return None, err
        active = sum(
            1 for j in self._jobs.values() if j["state"] in ("queued", "running")
        )
        if active >= self.MAX_ACTIVE:
            return None, f"已有 {active} 个任务在排队/生成中，请稍候再提交"
        job_id = uuid.uuid4().hex[:12]
        job: dict = {
            "id": job_id,
            "state": "queued",
            "params": clean,
            "error": "",
            "images": [],      # [{"sha": str}]
            "paths": [],       # 内部：本地最终路径（不序列化）
            "stats": {},
            "created_at": time.time(),
            "started_at": None,
            "finished_at": None,
            "task": None,
        }
        self._jobs[job_id] = job
        # 历史上限：淘汰最旧（done/error/discarded 优先清，不够再清进行中信息行）
        while len(self._jobs) > JOB_HISTORY_LIMIT:
            victim = next(iter(self._jobs))
            vjob = self._jobs.get(victim)
            if vjob and vjob["state"] in ("queued", "running"):
                break  # 进行中的不淘汰（受 MAX_ACTIVE 保护，不会涨爆）
            self._jobs.pop(victim, None)
        job["task"] = asyncio.create_task(self._run(job))
        return job_id, None

    async def _run(self, job: dict) -> None:
        plugin = self.plugin
        clean = job["params"]
        job["state"] = "running"
        job["started_at"] = time.time()
        logger.info(
            f"【生图工作台】 任务 {job['id']} 开始：mode={clean['mode']} "
            f"workflow={clean['workflow']} loras={[l['name'] for l in clean['loras']]} "
            f"refs={len(clean['ref_ids'])} raw_prompt=True"
        )
        try:
            init_images: list[str] = []
            if clean["mode"] == "img2img":
                # v7.9.1：1..N 张全量交给 _do_draw——>1 张且工作流带 multi_image
                # 节点时自动走多参考注入，单图工作流由 _do_draw 取第一张并记日志。
                for rid in clean["ref_ids"]:
                    p = self.ref_path(rid)
                    if not p:
                        raise ValueError(
                            f"参考图（{rid}）已失效，请重新上传后再提交"
                        )
                    init_images.append(p)

            lora_map: dict[str, float | None] = {}
            lora_presets: dict[str, str] = {}
            for l in clean["loras"]:
                lora_map[l["name"]] = l["weight"]  # None → 用 LoRA 库默认权重
                if l.get("preset"):
                    lora_presets[l["name"]] = l["preset"]

            # _HeadlessEvent 每次从（可能已被热更的）main 模块现取，桩只提供
            # 安全读空值 + 无操作 send，_do_draw 产出 (None, path) 不发消息。
            try:
                from .main import _HeadlessEvent
            except ImportError:
                from main import _HeadlessEvent
            ev = _HeadlessEvent()
            stats: dict = {}
            gen = plugin._do_draw(
                ev,
                clean["workflow"],
                clean["positive"],
                clean["negative"],
                clean["width"],
                clean["height"],
                lora_map or None,
                lora_presets or None,
                clean["seed"],
                init_images=init_images or None,
                is_img2img=clean["mode"] != "t2i",
                denoise=clean["denoise"],
                # 固定走 ComfyUI 工作流链路：不传 ""（会被解析成 active_platform，
                # 用户全局切到 NAI 时控制台生图会意外改道第三方平台）。
                platform="comfyui",
                notify_pending=False,
                source=SOURCE_WEBUI_STUDIO,
                cfg=clean["cfg"],
                steps=clean["steps"],
                sampler=clean["sampler"] or None,
                noise_schedule=clean["noise_schedule"] or None,
                stats=stats,
                # v7.9.7：润色是提示词区里的即用即点动作，提交永远用输入框最终文本，
                # 不再有隐藏的提交后二次加工
                raw_prompt=True,
                custom_size=clean["custom_size"],
                external=True,
            )
            async for _node, img_path in gen:
                if img_path:
                    job["paths"].append(img_path)
            job["stats"] = dict(stats) if isinstance(stats, dict) else {}
            # 归档已完成：按文件内容算 sha，前端凭 sha 走 /img/{sha} 直链
            try:
                from .image_store import _sha256_of
            except ImportError:
                from image_store import _sha256_of
            for p in job["paths"]:
                job["images"].append({"sha": _sha256_of(p) or ""})
            if not job["images"]:
                job["state"] = "error"
                job["error"] = (
                    "本次没有产出图片（原因见 AstrBot 日志）"
                    + (
                        f"：被 NSFW 拦截 {job['stats'].get('nsfw_blocked', 0)} 张"
                        if job["stats"].get("nsfw_blocked")
                        else ""
                    )
                )
                logger.warning(f"【生图工作台】 任务 {job['id']} 未产出图片")
            else:
                job["state"] = "done"
                logger.info(
                    f"【生图工作台】 任务 {job['id']} 完成：{len(job['images'])} 张 "
                    f"耗时 {time.time() - job['started_at']:.1f}s"
                )
        except asyncio.CancelledError:
            job["state"] = "discarded"
            logger.info(f"【生图工作台】 任务 {job['id']} 已放弃等待")
        except Exception as e:
            job["state"] = "error"
            job["error"] = str(e) or e.__class__.__name__
            logger.warning(f"【生图工作台】 任务 {job['id']} 失败: {e}", exc_info=True)
        finally:
            job["finished_at"] = time.time()
            job["task"] = None

    def get(self, job_id: str) -> dict | None:
        return self._jobs.get(str(job_id or "").strip())

    def discard(self, job_id: str) -> str | None:
        job = self.get(job_id)
        if not job:
            return "任务不存在或已过期"
        if job["state"] not in ("queued", "running"):
            return None  # 已结束：幂等，前端直接移除
        job["state"] = "discarded"
        task = job.get("task")
        if task is not None:
            try:
                task.cancel()
            except Exception:
                pass
        return None

    def list_jobs(self, limit: int = 30) -> list[dict]:
        ids = list(self._jobs.keys())[-max(1, min(int(limit or 30), JOB_HISTORY_LIMIT)):]
        ids.reverse()
        return [self.serialize(self._jobs[i]) for i in ids if i in self._jobs]

    def serialize(self, job: dict) -> dict:
        now = time.time()
        started = job.get("started_at")
        finished = job.get("finished_at")
        if started and finished:
            elapsed = round(finished - started, 1)
        elif started:
            elapsed = round(now - started, 1)
        else:
            elapsed = None
        params = job.get("params") or {}
        return {
            "id": job["id"],
            "state": job["state"],
            "elapsed": elapsed,
            "error": job.get("error") or "",
            "images": [
                {"sha": img.get("sha") or ""}
                for img in (job.get("images") or [])
            ],
            "nsfw_blocked": int((job.get("stats") or {}).get("nsfw_blocked") or 0),
            # 参数回显（不含 ref_ids——临时文件可能已被清理，复用参数时重传）
            "params": {
                "mode": params.get("mode"),
                "workflow": params.get("workflow"),
                "positive": params.get("positive"),
                "negative": params.get("negative"),
                "width": params.get("width"),
                "height": params.get("height"),
                "seed": params.get("seed"),
                "steps": params.get("steps"),
                "cfg": params.get("cfg"),
                "denoise": params.get("denoise"),
                "sampler": params.get("sampler"),
                "noise_schedule": params.get("noise_schedule"),
                "custom_size": params.get("custom_size"),
                "loras": params.get("loras"),
            },
            "created_at": job.get("created_at"),
        }

    async def samplers_list(self, force: bool = False) -> dict:
        """采样器 / 调度器枚举（v7.9.5，右栏高级参数的下拉选项来源）。

        从 ComfyUI `/object_info` 的 KSampler 类节点定义里提取 `sampler_name`
        与 `scheduler` 的枚举列表——这是 ComfyUI 官方权威口径。逐台尝试直到
        拿到；按服务器 key 模块级缓存（/object_info 响应体很大，绝不反复拉）。
        """
        servers = self.plugin._servers() if hasattr(self.plugin, "_servers") else []
        try:
            from .comfyui_client import ComfyUIClient
        except ImportError:
            from comfyui_client import ComfyUIClient
        for s in servers:
            name = str(s.get("name") or s.get("url") or "?")
            try:
                skey = self.plugin._server_key(s)
            except Exception:
                skey = name
            cached = _SAMPLERS_CACHE.get(skey)
            if cached is not None and not force:
                return {**cached, "source": name}
            client = None
            try:
                client = self.plugin._build_client(s)
                info = await client.object_info()
                samplers: list[str] = []
                schedulers: list[str] = []
                if isinstance(info, dict):
                    for cid, node in info.items():
                        if not isinstance(node, dict):
                            continue
                        ct = str(cid or node.get("class_type") or "").lower()
                        if "ksampler" not in ct and "sampler" not in ct:
                            continue
                        req = ((node.get("input") or {}).get("required") or {})
                        if not samplers and isinstance(req.get("sampler_name"), (list, tuple)) \
                                and req["sampler_name"] and isinstance(req["sampler_name"][0], list):
                            samplers = [str(x) for x in req["sampler_name"][0]]
                        if not schedulers and isinstance(req.get("scheduler"), (list, tuple)) \
                                and req["scheduler"] and isinstance(req["scheduler"][0], list):
                            schedulers = [str(x) for x in req["scheduler"][0]]
                        if samplers and schedulers:
                            break
                if samplers:
                    payload = {"samplers": samplers, "schedulers": schedulers}
                    _SAMPLERS_CACHE[skey] = payload
                    return {**payload, "source": name}
            except Exception as e:
                logger.debug(f"【生图工作台】 服务器 {name} 采样器枚举获取失败: {e}")
            finally:
                if client is not None:
                    try:
                        await client.close()
                    except Exception:
                        pass
        return {"samplers": [], "schedulers": [], "source": ""}

    async def queue_status(self) -> dict:
        """各 ComfyUI 服务器的队列概览（v7.9.4 右栏「服务器状态」块）。

        逐台调 GET /queue 统计 running/pending 数量；单台失败不拖垮整体
        （ok=False 前端显示「查询失败」）。middle-station 返回缺字段时按 0 处理。
        """
        servers = self.plugin._servers() if hasattr(self.plugin, "_servers") else []
        try:
            from .comfyui_client import ComfyUIClient
        except ImportError:
            from comfyui_client import ComfyUIClient
        out: list[dict] = []
        for s in servers:
            name = str(s.get("name") or s.get("url") or "?")
            running: int | None = None
            pending: int | None = None
            ok = False
            client = None
            try:
                client = self.plugin._build_client(s)
                q = await client.get_queue()
                qrunning = q.get("queue_running") if isinstance(q, dict) else None
                qpending = q.get("queue_pending") if isinstance(q, dict) else None
                running = len(qrunning) if isinstance(qrunning, list) else 0
                pending = len(qpending) if isinstance(qpending, list) else 0
                ok = True
            except Exception as e:
                logger.debug(f"【生图工作台】 服务器 {name} 队列查询失败: {e}")
            finally:
                if client is not None:
                    try:
                        await client.close()
                    except Exception:
                        pass
            out.append({"name": name, "running": running, "pending": pending, "ok": ok})
        return {"servers": out}

    async def interrupt_all(self) -> dict:
        """尽力而为中断 ComfyUI 当前任务：对所有已配置服务器各发一次 /interrupt。

        ComfyUI 的中断是全局操作（可能打断非控制台发起的任务），调用方（前端）
        必须先二次确认。
        """
        servers = self.plugin._servers() if hasattr(self.plugin, "_servers") else []
        results: dict[str, bool] = {}
        try:
            from .comfyui_client import ComfyUIClient
        except ImportError:
            from comfyui_client import ComfyUIClient
        for s in servers:
            name = str(s.get("name") or s.get("url") or "?")
            client = None
            try:
                client = self.plugin._build_client(s)
                await client.interrupt()
                results[name] = True
            except Exception as e:
                logger.warning(f"【生图工作台】 中断服务器 {name} 失败: {e}")
                results[name] = False
            finally:
                if client is not None:
                    try:
                        await client.close()
                    except Exception:
                        pass
        return {"results": results}


# ────────────────────────────────────────────────────────────────── #
# 总管（main.py 挂载实例）
# ────────────────────────────────────────────────────────────────── #

class StudioManager:
    """在线生图工作台入口：模板库 + 任务管理器。"""

    def __init__(self, plugin) -> None:
        self.plugin = plugin
        self.store: StudioStore | None = None
        try:
            self.store = StudioStore(plugin.data_dir)
        except Exception as e:
            logger.warning(f"【生图工作台】 模板库初始化失败（模板功能不可用）: {e}", exc_info=True)
        self.jobs = StudioJobManager(plugin)

    # ---- 引导数据（GET studio/overview）--------------------------- #
    @staticmethod
    def _wf_img2img_capable(entry: dict, roles: dict | None, base_id: str) -> bool:
        """图生图能力判定（用户口径，与 waifu 插件 draw.workflow_img2img_capable 同一套）：
        ① 手填 image_node 显式可用；② 绑定基础工作流 → 看解析注记里的图节点；
        ③ 旧版未绑定 → 名字含「图生图」兜底。绑定但注记查询失败时按可用兜底（不误伤）。"""
        if str(entry.get("image_node") or "").strip():
            return True
        if base_id:
            if roles is None:
                return True
            return bool(str(roles.get("image_node") or "").strip())
        return "图生图" in str(entry.get("name") or "")

    def _resolve_server_display(self, entry: dict) -> str:
        """工作流绑定的服务器展示名（用户口径：展示名字，不是 key/ID）。

        绑定值优先按 `__template_key` 精确匹配（新版存 key），再按名字匹配
        （旧版存的是名字，与 main._resolve_server 同序）；解析不到（服务器被删）
        返回空串，前端不展示该角标。"""
        ref = str(entry.get("server_key") or entry.get("server_name") or "").strip()
        if not ref:
            return ""
        try:
            servers = self.plugin._servers() or []
            for s in servers:
                if str(s.get("__template_key") or "").strip() == ref:
                    return str(s.get("name") or ref)
            for s in servers:
                if str(s.get("name") or "").strip() == ref:
                    return str(s.get("name") or ref)
        except Exception:
            pass
        return ""

    def overview(self) -> dict:
        plugin = self.plugin
        wf_store = getattr(plugin, "workflow_store", None)
        workflows: list[dict] = []
        for entry in plugin._workflows():
            if not isinstance(entry, dict):
                continue
            w: dict = {
                "name": str(entry.get("name") or "").strip(),
                "enabled": bool(entry.get("enabled", True)),
                # v7.9.3：展示服务器「名字」而非 key/ID（按 __template_key→名字 双段解析）
                "server_name": self._resolve_server_display(entry),
                "default_width": entry.get("default_width") or None,
                "default_height": entry.get("default_height") or None,
                "default_positive": str(entry.get("default_positive") or "")[:600],
                "default_negative": str(entry.get("default_negative") or "")[:600],
                "loras_text": str(entry.get("loras_text") or ""),
                "size_switch": entry.get("size_switch"),
                "max_refs": plugin._img2img_max_refs(entry),
                "aliases": str(entry.get("aliases") or ""),
                "image": str(entry.get("image") or ""),
                "description": str(entry.get("description") or "")[:300],
                "kind": None,
                "base_name": "",
                "basemodel_name": "",
                "prompt_style": "",
                "is_anima": False,
                "multi_image": False,
                # v7.9.3：基础工作流解析出的采样默认值（UI 展示「工作流实际参数」用）
                "sampler_defaults": None,
                # v7.9.3：按底模兼容性筛好的可用 LoRA 名单（与聊天侧 _lora_matches_wf 同规则）
                "lora_names": [],
            }
            if not w["name"]:
                continue
            base_id = str(entry.get("base_id") or "").strip()
            rec = None
            roles: dict | None = None
            if base_id.isdigit() and wf_store is not None:
                try:
                    # with_json：采样默认值缺失时要用原始 JSON 现场提取（见下）
                    rec = wf_store.get(int(base_id), with_json=True)
                    roles = (rec or {}).get("roles") or {}
                except Exception:
                    rec = None
                    roles = None

            # ── 文生图 / 图生图识别（v7.9.3，waifu 同款三层口径）──────────
            # 旧判定只看 roles.kind，注记查不到就标「未知」；新版：
            if rec is not None:
                if roles.get("kind") == "upscale":
                    w["kind"] = "upscale"  # 纯放大图不进生图下拉
                    capable = True
                else:
                    capable = bool(str(roles.get("image_node") or "").strip())
                    w["kind"] = "img2img" if capable else "t2i"
            elif base_id:
                # 绑定了但查询失败（基础工作流被删/store 未就绪）：按可用兜底，不误伤
                capable = True
                w["kind"] = "img2img"
            else:
                # 旧版未绑定：手填 image_node → 现解析内联 JSON → 名字含「图生图」
                legacy_roles = _legacy_wf_roles(plugin, entry, w["name"])
                if str(entry.get("image_node") or "").strip():
                    capable = True
                elif legacy_roles is not None:
                    capable = bool(str(legacy_roles.get("image_node") or "").strip())
                else:
                    capable = "图生图" in w["name"]
                w["kind"] = "img2img" if capable else "t2i"

            if rec is not None:
                w["base_name"] = str(rec.get("name") or "")
                w["multi_image"] = bool(roles.get("multi_image"))
                sd = roles.get("sampler_defaults") or {}
                # v7.9.5：注记缺采样默认值时，用基础工作流原始 JSON 现场提取一次
                # （workflow_builder.get_sampler_defaults 读的是节点当前值，最真实）
                if not (sd.get("steps") or sd.get("cfg") or sd.get("denoise")):
                    try:
                        try:
                            from . import workflow_builder as _wb
                        except ImportError:
                            import workflow_builder as _wb
                        _prompt = json.loads(rec.get("wf_json") or "{}")
                        _sd2 = _wb.get_sampler_defaults(_prompt) if _prompt else {}
                        if _sd2:
                            sd = {**{k: v for k, v in _sd2.items()}, **{k: v for k, v in sd.items() if v is not None}}
                    except Exception as _sde:
                        logger.debug(f"【生图工作台】 工作流「{w['name']}」采样默认值现场提取失败: {_sde}")
                w["sampler_defaults"] = {
                    "steps": sd.get("steps"),
                    "cfg": sd.get("cfg"),
                    "denoise": sd.get("denoise"),
                    "sampler_name": str(sd.get("sampler_name") or ""),
                    "scheduler": str(sd.get("scheduler") or ""),
                }
                bm = None
                bm_store = getattr(plugin, "basemodels", None)
                bm_id = rec.get("basemodel_id")
                if bm_store is not None and bm_id:
                    try:
                        bm = bm_store.get(int(bm_id))
                    except Exception:
                        bm = None
                if bm:
                    w["basemodel_name"] = str(bm.get("name") or "")
                    w["prompt_style"] = str(bm.get("prompt_style") or "")
                    w["is_anima"] = bool(
                        bm.get("prompt_style") == "danbooru" or bm.get("danbooru_ready")
                    )
            if not w["basemodel_name"]:
                # 旧版条目回退 base_model 字段（_wf_base_model 同口径）
                w["basemodel_name"] = str(entry.get("base_model") or "").strip()

            # v7.9.9：老版本工作流的采样参数——与老版编辑弹窗「读取文件中的采样器参数」
            # 同逻辑（内联 workflow_json 或 workflow_dir/<workflow_name>.json →
            # get_sampler_defaults 读节点当前值）。新版走上方解析注记分支，此处只补旧版。
            if rec is None:
                _lprompt = _load_legacy_prompt(plugin, entry, w["name"])
                if _lprompt:
                    try:
                        try:
                            from . import workflow_builder as _wb
                        except ImportError:
                            import workflow_builder as _wb
                        _lsd = _wb.get_sampler_defaults(_lprompt)
                        if _lsd and (_lsd.get("steps") or _lsd.get("cfg") or _lsd.get("denoise")
                                     or _lsd.get("sampler_name") or _lsd.get("scheduler")):
                            w["sampler_defaults"] = {
                                "steps": _lsd.get("steps"),
                                "cfg": _lsd.get("cfg"),
                                "denoise": _lsd.get("denoise"),
                                "sampler_name": str(_lsd.get("sampler_name") or ""),
                                "scheduler": str(_lsd.get("scheduler") or ""),
                            }
                    except Exception as _lse:
                        logger.debug(f"【生图工作台】 旧版工作流「{w['name']}」采样参数读取失败: {_lse}")

            # v7.9.9：工作流自带的 LoRA（loras_text，出图时本就自动生效）——
            # 前端自动勾选并展示「自带」角标
            try:
                w["bound_loras"] = [
                    {"name": str(l.get("name") or "").strip(), "weight": l.get("weight")}
                    for l in plugin._loras_of(entry)
                    if isinstance(l, dict) and str(l.get("name") or "").strip()
                ]
            except Exception as e:
                logger.warning(f"【生图工作台】 工作流「{w['name']}」自带 LoRA 解析失败: {e}")

            # 可用 LoRA 名单：与聊天侧 _lora_matches_wf 同规则（底模族兼容）。
            # v7.9.5：**不再按 enabled 过滤**——聊天侧 _lora_lookup 按名字匹配时
            # 从不看 enabled，库条目没开这个开关（默认 False）≠ 不可用，
            # 此前多加这一刀把整库滤空（用户实测：切几种底模工作流都是空）。
            try:
                w["lora_names"] = [
                    str(l.get("name") or "").strip()
                    for l in plugin._lora_library()
                    if isinstance(l, dict)
                    and str(l.get("name") or "").strip()
                    and plugin._lora_matches_wf(l, entry)
                ]
            except Exception as e:
                logger.warning(f"【生图工作台】 工作流「{w['name']}」LoRA 兼容名单计算失败: {e}")
            workflows.append(w)

        loras: list[dict] = []
        try:
            for item in plugin._lora_library():
                if not isinstance(item, dict):
                    continue
                name = str(item.get("name") or "").strip()
                if not name:
                    continue
                presets = [
                    {"name": str(p.get("name") or ""), "always_on": bool(p.get("always_on"))}
                    for p in (item.get("presets") or [])
                    if isinstance(p, dict) and str(p.get("name") or "").strip()
                ]
                loras.append({
                    "name": name,
                    "enabled": bool(item.get("enabled", True)),
                    "category": str(item.get("category") or ""),
                    "base_model": str(item.get("base_model") or ""),
                    "trigger_words": str(item.get("trigger_words") or ""),
                    "description": str(item.get("description") or "")[:300],
                    "weight": item.get("weight"),
                    "model_only": bool(item.get("model_only", True)),
                    "presets": presets,
                    "image": str(item.get("image") or ""),
                    "starred": bool(item.get("starred")),
                })
        except Exception as e:
            logger.warning(f"【生图工作台】 LoRA 库读取失败: {e}")

        size_tiers: list = []
        try:
            try:
                from .main import _size_tier_table
            except ImportError:
                from main import _size_tier_table
            size_tiers = _size_tier_table(plugin._cfg("draw_ratio", []) or [])
        except Exception as e:
            logger.warning(f"【生图工作台】 尺寸档位表读取失败: {e}")

        servers = [
            {
                "name": str(s.get("name") or ""),
                "url": str(s.get("url") or ""),
                "device": str(s.get("device") or ""),
            }
            for s in (plugin._servers() or [])
            if isinstance(s, dict)
        ]

        llm_ready = False
        try:
            llm_ready = bool(plugin._resolve_translate_provider_id())
        except Exception:
            llm_ready = False

        templates: list[dict] = []
        if self.store is not None:
            try:
                templates = self.store.list_templates()
            except Exception as e:
                logger.warning(f"【生图工作台】 模板列表读取失败: {e}")

        return {
            "workflows": workflows,
            "loras": loras,
            "size_tiers": size_tiers,
            "servers": servers,
            "llm_ready": llm_ready,
            "max_refs": plugin._img2img_max_refs(None),
            "templates": templates,
            "limits": {
                "max_active": StudioJobManager.MAX_ACTIVE,
                "history_limit": JOB_HISTORY_LIMIT,
            },
        }

    # ---- LLM 提示词生成 ------------------------------------------- #
    async def llm_prompt(self, mode: str, text: str, lora_ref: str = "") -> str:
        """AI 生成/润色提示词。全部复用聊天侧同款方法（token 照常入账）。"""
        mode = str(mode or "").strip()
        text = str(text or "").strip()
        if not text:
            raise ValueError("请先填写一段提示词或想法")
        if mode not in _LLM_PROMPT_MODES:
            raise ValueError(f"不支持的 AI 模式: {mode}")
        if mode == "expand":
            out = await self.plugin._enhance_prompt_llm(text, lora_ref=lora_ref)
        elif mode == "translate":
            out = await self.plugin._translate_llm(text, lora_ref=lora_ref)
        elif mode == "anima":
            out = await self.plugin._rewrite_to_anima_llm(text, lora_ref=lora_ref)
        elif mode == "natural":
            out = await self.plugin._rewrite_to_natural_llm(text, lora_ref=lora_ref)
        else:  # qwen_edit
            out = await self.plugin._rewrite_to_qwen_llm(text, edit_mode=True)
        out = str(out or "").strip()
        if not out:
            raise RuntimeError(
                "LLM 没有返回内容（请检查是否配置了「翻译/改写模型」，或稍后重试）"
            )
        return out

    async def ai_imagine(self, hint: str, workflow_name: str, detail: bool = False) -> dict:
        """🎲 AI 想画面：按所选工作流底模的提示词格式**自由创作**一幅画面的提示词。

        - 格式跟随底模 prompt_style：danbooru→英文标签；natural→自然语言
          （语言按 priority_lang）；qwen→中文描述式。
        - detail=True 时要求生成数百至上千字的丰富提示词（多维度层层展开），
          默认关闭保持简洁；详细模式自动放宽 LLM 超时。
        - hint 非空 = 围绕用户的想法创作；为空 = AI 自由发挥
          （每次随机抽一个题材建议，重复点击画面不重样）。
        - 返回 {"text": 提示词, "translation": 中文译文或 ""}：英文提示词时在同一次
          LLM 输出里用 ===ZH=== 分隔顺带产出译文（省一次翻译调用）；中文提示词不含译文。
        - LLM 调用与 token 入账与聊天侧同款（_record_llm_token）。
        """
        detail = bool(detail)
        plugin = self.plugin
        wf_entry = None
        try:
            target = str(workflow_name or "").strip()
            for e in plugin._workflows():
                if isinstance(e, dict) and str(e.get("name") or "").strip() == target:
                    wf_entry = e
                    break
        except Exception:
            wf_entry = None

        style = "danbooru"
        bm_name = ""
        priority_lang = ""
        if wf_entry is not None:
            try:
                bm = plugin._basemodel_of_workflow(wf_entry) or {}
                style = str(bm.get("prompt_style") or "danbooru")
                bm_name = str(bm.get("name") or "")
                priority_lang = str(bm.get("priority_lang") or "").strip()
            except Exception:
                pass

        if style == "danbooru":
            if detail:
                fmt = (
                    "输出格式：英文 Danbooru 标签——用英文逗号分隔 80~160 个具体小写标签，"
                    "总长 400~1200 字符。按以下维度层层展开，每个维度都要有具体标签、"
                    "不要空泛重复：\n"
                    "①主体（人数/性别/年龄感/身份/种族）②外貌（发型/发色/发质/眼睛/"
                    "肤色/脸型/表情/体态特征）③服装（上下装/款式/颜色/材质/褶皱）"
                    "④配饰（发饰/首饰/鞋袜/包/手持道具）⑤姿态动作（站坐卧跑/手势/"
                    "视线方向/与他人互动）⑥场景环境（地点/时间/天气/建筑或自然细节/"
                    "远近景层次）⑦光影色调（光源方向/色温/阴影/明暗对比）"
                    "⑧构图镜头（视角/景别/景深/镜头语言）⑨画风质感（艺术风格/笔触/材质）。\n"
                    "⛔不要写任何画质词（masterpiece / best quality / score_9 / very aesthetic 等，"
                    "插件会自动补）；不要编号、不要解释、不要引号。"
                )
            else:
                fmt = (
                    "输出格式：英文 Danbooru 标签——用英文逗号分隔 10~24 个小写标签，"
                    "涵盖主体、动作/表情、服装、场景、构图与光影。"
                    "⛔不要写任何画质词（masterpiece / best quality / score_9 / very aesthetic 等，"
                    "插件会自动补）；不要编号、不要解释、不要引号。"
                )
        elif style == "qwen":
            if detail:
                fmt = (
                    "输出格式：中文描述式提示词（Qwen 图像模型口径）——6~12 句、"
                    "300~800 字的完整画面描述。把主体身份、外貌特征、服装配饰、动作表情、"
                    "场景环境、光影色调、构图镜头、氛围情绪逐层写清楚，细节具体可感，"
                    "不要空泛重复。不要标签化罗列、不要画质词堆砌、不要编号与解释。"
                )
            else:
                fmt = (
                    "输出格式：中文描述式提示词——1~3 句完整的画面描述（Qwen 图像模型口径），"
                    "主体、动作、场景、细节都要具体。不要标签化罗列、不要画质词堆砌、"
                    "不要编号与解释。"
                )
        else:  # natural
            lang = "中文" if (not priority_lang or "中" in priority_lang) else "英文"
            if detail:
                fmt = (
                    f"输出格式：{lang}自然语言——8~16 句、共 400~1000 字的连贯画面描写。"
                    "按主体外貌、服装配饰、姿态表情、场景环境、光影色调、构图镜头、"
                    "氛围情绪逐层展开，每部分都要有具体细节，避免空泛重复。"
                    "不要标签化罗列、不要画质词堆砌、不要编号与解释。"
                )
            else:
                fmt = (
                    f"输出格式：{lang}自然语言描述——2~4 句流畅的画面描写，"
                    "主体、动作、场景、氛围都要具体。不要标签化罗列、不要画质词堆砌、"
                    "不要编号与解释。"
                )

        hint = str(hint or "").strip()
        if hint:
            subject = f"用户给了一些想法，请围绕它们创作一幅画面（可适度补充细节，不要偏离）：{hint}"
        else:
            theme = random.choice(_IMAGINE_THEMES)
            subject = (
                "用户没有给任何想法——请你自由发挥，想象一幅有意思、有记忆点的画面。"
                f"本次题材建议：{theme}（可自由变化，不必拘泥）；"
                "可以是纯风景、一个女孩、多个角色、动漫/游戏角色、动物、都市、"
                "和风、科幻、奇幻等等，尽量避免最老套的构图。"
            )

        bm_line = f"当前底模：{bm_name}。" if bm_name else ""
        zh_rule = (
            "紧接着用中文把上面的画面细节完整译成通顺描述（200~600 字，覆盖主体、外貌、"
            "服装、场景、光影、构图等主要信息）；"
            if detail else
            "紧接着写 1~2 句通顺中文说明画面（只描述画面，不逐条复述标签名）；"
        )
        prompt = (
            "你是顶级的 AI 绘画提示词创作师。请为用户创作一幅画面的提示词。\n"
            f"{bm_line}{fmt}\n"
            f"{subject}\n"
            "【输出格式·必须严格遵守】只输出以下两段，不要任何其它说明：\n"
            "第 1 段：提示词正文。\n"
            "第 2 段：若第 1 段是英文——在正文之后另起一行原样写上分隔符 ===ZH===，"
            f"{zh_rule}"
            "若第 1 段是中文——不要写分隔符、不要写译文。\n"
        )
        provider_id = plugin._resolve_translate_provider_id()
        if not provider_id:
            raise RuntimeError("未配置「翻译/改写模型」，无法 AI 生成提示词")
        timeout = max(1, int(plugin._cfg("llm_rewrite_timeout", 60) or 60))
        if detail:
            # 上千字输出明显慢于简洁模式，放宽到至少 150s（前端 detail 请求也同步放宽）
            timeout = max(timeout, 150)
        try:
            llm_resp = await asyncio.wait_for(
                plugin.context.llm_generate(chat_provider_id=provider_id, prompt=prompt),
                timeout=timeout,
            )
        except asyncio.TimeoutError:
            raise RuntimeError(f"AI 想画面超时（>{timeout}s）") from None
        except Exception as e:
            raise RuntimeError(f"AI 想画面失败: {e}") from e
        try:
            plugin._record_llm_token("studio_imagine", provider_id, llm_resp)
        except Exception:
            pass
        out = str(getattr(llm_resp, "completion_text", "") or "").strip()
        out = out.strip("`").strip()
        # v7.10.3：同一次 LLM 调用顺带产出中文译文（仅英文提示词有），省一次翻译额度。
        # LLM 按 ===ZH=== 分隔；不遵守格式时退回「只有提示词」，绝不丢主产物。
        translation = ""
        if "===ZH===" in out:
            out, zh = out.split("===ZH===", 1)
            out = out.strip().strip("`").strip()
            translation = zh.strip().strip("`").strip()
            translation = re.sub(r"^\s*(中文译文|译文)\s*[:：]\s*", "", translation)
            translation = " ".join(translation.split())
        out = re.sub(r"^\s*(提示词|prompt)\s*[:：]\s*", "", out, flags=re.I)
        if not out:
            raise RuntimeError("LLM 没有返回内容（请稍后重试）")
        # 判断提示词语言用「汉字占比」而非「含任一汉字」：英文标签里偶带中文角色名
        # （如 hatsune miku 写成「初音未来」）不该让译文被误清空。
        cn = len(re.findall(r"[\u4e00-\u9fff]", out))
        is_cn = cn >= 2 and cn / max(len(out), 1) > 0.30
        if is_cn:
            translation = ""
        elif not translation and hasattr(plugin, "_translate_to_chinese_llm"):
            # v7.10.4 兜底：LLM 没按格式给译文时补调一次翻译，保证英文提示词一定有译文可看。
            # 正常遵守格式时不走这里，不额外耗额度。
            try:
                translation = await plugin._translate_to_chinese_llm(out)
            except Exception:
                translation = ""
        return {"text": out, "translation": translation}


# ────────────────────────────────────────────────────────────────── #
# 旧版工作流的现解析（带缓存）
# ────────────────────────────────────────────────────────────────── #

_LEGACY_PROMPT_CACHE: dict[str, dict | None] = {}
_LEGACY_ROLES_CACHE: dict[str, dict | None] = {}


def _load_legacy_prompt(plugin, entry: dict, name: str) -> dict | None:
    """旧版工作流的有效 JSON（有缓存）。

    来源两选一：内联 `workflow_json` 优先；否则读
    `workflow_dir/<workflow_name>.json` 文件——与老版编辑弹窗「读取文件中的
    采样器参数」（webui_api._read_workflow_sampler_file）同一文件口径。
    都拿不到返回 None。"""
    key = str(entry.get("workflow_id") or name)
    if key in _LEGACY_PROMPT_CACHE:
        return _LEGACY_PROMPT_CACHE[key]
    prompt: dict | None = None
    try:
        raw = entry.get("workflow_json")
        if isinstance(raw, dict):
            prompt = raw
        elif isinstance(raw, str) and raw.strip():
            prompt = json.loads(raw)
        if not isinstance(prompt, dict) or not prompt:
            wname = str(entry.get("workflow_name") or "").strip()
            if wname:
                wdir = getattr(plugin, "workflow_dir", None)
                if wdir is not None:
                    p = Path(wdir) / wname
                    if not p.suffix:
                        p = p.with_suffix(".json")
                    if p.is_file():
                        prompt = json.loads(p.read_text(encoding="utf-8"))
    except Exception as e:
        logger.debug(f"【生图工作台】 旧版工作流「{name}」JSON 加载失败: {e}")
        prompt = None
    if not isinstance(prompt, dict) or not prompt:
        prompt = None
    _LEGACY_PROMPT_CACHE[key] = prompt
    return prompt


def _legacy_wf_roles(plugin, entry: dict, name: str) -> dict | None:
    """旧版工作流的解析注记（有缓存）。

    解析失败 / 拿不到 JSON 返回 None（调用方退回手填 image_node / 名字兜底口径）。"""
    key = str(entry.get("workflow_id") or name)
    if key in _LEGACY_ROLES_CACHE:
        return _LEGACY_ROLES_CACHE[key]
    roles: dict | None = None
    prompt = _load_legacy_prompt(plugin, entry, name)
    if prompt:
        try:
            try:
                from . import workflow_parser
            except ImportError:
                import workflow_parser
            parsed, errors = workflow_parser.parse_workflow(prompt)
            if parsed and not errors:
                roles = parsed
        except Exception as e:
            logger.debug(f"【生图工作台】 旧版工作流「{name}」解析失败: {e}")
    _LEGACY_ROLES_CACHE[key] = roles
    return roles

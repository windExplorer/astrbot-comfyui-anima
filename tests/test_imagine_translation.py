"""AI 想画面：一次 LLM 同时产出提示词+中文译文的解析契约（v7.10.3）。

用假 plugin 调真 StudioManager.ai_imagine（__new__ 绕过初始化建库），验证
===ZH=== 分隔解析、中文提示词不给译文、LLM 不守格式时不丢主产物。

    uv run --no-project --python 3.12 python tests/test_imagine_translation.py
"""
import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import studio_api

_ok = 0
_fail = 0


def check(title, cond, detail=""):
    global _ok, _fail
    if cond:
        _ok += 1
        print(f"  [OK] {title}")
    else:
        _fail += 1
        print(f"  [FAIL] {title}  {detail}")


class _Resp:
    def __init__(self, text):
        self.completion_text = text
        self.usage = None


def _fake_manager(output, style="danbooru", fallback=False, size=None):
    class _Ctx:
        last_prompt = ""

        async def llm_generate(self, chat_provider_id, prompt):
            _Ctx.last_prompt = prompt
            return _Resp(output)

    class _Plugin:
        context = _Ctx()

        def _workflows(self):
            return [{
                "name": "wf1", "workflow_id": "x",
                "default_width": size[0] if size else None,
                "default_height": size[1] if size else None,
            }]

        def _basemodel_of_workflow(self, entry):
            return {"name": "测试底模", "prompt_style": style, "priority_lang": ""}

        def _resolve_translate_provider_id(self):
            return "fake-provider"

        def _cfg(self, key, default=None):
            return 60 if key == "llm_rewrite_timeout" else default

        def _record_llm_token(self, *a, **kw):
            pass

        if fallback:
            async def _translate_to_chinese_llm(self, text):
                return "兜底译文"

    mgr = studio_api.StudioManager.__new__(studio_api.StudioManager)
    mgr.plugin = _Plugin()
    return mgr, _Ctx


async def main():
    # 1. 英文提示词 + 同一次调用产出的译文
    mgr, ctx = _fake_manager("1girl, long hair, blue eyes\n===ZH===\n一个长发蓝眼睛的女孩")
    r = await mgr.ai_imagine("一个女孩", "wf1")
    check("英文提示词正文", r["text"] == "1girl, long hair, blue eyes", repr(r))
    check("译文正确提取", r["translation"] == "一个长发蓝眼睛的女孩", repr(r))
    check("prompt 要求输出分隔符", "===ZH===" in ctx.last_prompt)

    # 2. 中文提示词（qwen 口径）不给译文
    mgr, _ = _fake_manager("一个女孩站在樱花树下，微风吹过长发", style="qwen")
    r = await mgr.ai_imagine("", "wf1")
    check("中文提示词保留", "樱花" in r["text"], repr(r))
    check("中文不生成译文", r["translation"] == "", repr(r))

    # 3. LLM 不守格式（只给正文）时不能丢主产物
    mgr, _ = _fake_manager("1boy, solo, white shirt")
    r = await mgr.ai_imagine("男孩", "wf1")
    check("无分隔符时正文不丢", r["text"] == "1boy, solo, white shirt", repr(r))
    check("无分隔符时译文为空", r["translation"] == "", repr(r))

    # 4. 中文提示词却写了分隔符 → 译文必须清空（避免前端误展示）
    mgr, _ = _fake_manager("一个女孩\n===ZH===\n不该出现", style="qwen")
    r = await mgr.ai_imagine("", "wf1")
    check("中文+分隔符仍不给译文", r["translation"] == "", repr(r))

    # 5. 译文带「译文：」前缀与多余空白时归一
    mgr, _ = _fake_manager("1girl\n===ZH===\n  中文译文：  一个女孩  ")
    r = await mgr.ai_imagine("", "wf1")
    check("译文前缀清理", r["translation"] == "一个女孩", repr(r))

    # 6. 英文标签里夹少量中文角色名，不该把译文误清空（v7.10.4 汉字占比判定）
    mgr, _ = _fake_manager("1girl, 初音未来, long hair\n===ZH===\n一个长发女孩")
    r = await mgr.ai_imagine("", "wf1")
    check("夹中文角色名仍保留译文", r["translation"] == "一个长发女孩", repr(r))

    # 7. LLM 没写分隔符但有兜底翻译方法 → 补调一次，保证英文提示词一定有译文
    mgr, _ = _fake_manager("1boy, solo, white shirt", fallback=True)
    r = await mgr.ai_imagine("", "wf1")
    check("无分隔符时兜底出译文", r["translation"] == "兜底译文", repr(r))

    # 8. 中文提示词即使带兜底方法也不翻译
    mgr, _ = _fake_manager("一个女孩站在樱花树下，微风吹过长发", style="qwen", fallback=True)
    r = await mgr.ai_imagine("", "wf1")
    check("中文提示词不触发兜底", r["translation"] == "", repr(r))

    # 9. 详细模式：prompt 要求数百至上千字的标签 + 展开译文（v7.10.5）
    mgr, ctx = _fake_manager("1girl, long hair")
    await mgr.ai_imagine("", "wf1", detail=True)
    check("详细模式要求长标签串", "400~1200" in ctx.last_prompt, ctx.last_prompt[:200])
    check("详细模式要求展开译文", "200~600 字" in ctx.last_prompt)

    # 10. 默认模式仍保持简洁（不因新增开关改变既有行为）
    mgr, ctx = _fake_manager("1girl, long hair")
    await mgr.ai_imagine("", "wf1")
    check("默认模式仍简洁", "10~24 个" in ctx.last_prompt, ctx.last_prompt[:200])

    # 11. 把当前工作流尺寸告知 LLM，并禁止它写会触发尺寸改写的比例/档位词（v7.10.8）
    mgr, ctx = _fake_manager("1girl, long hair", size=(832, 1216))
    await mgr.ai_imagine("", "wf1")
    check("prompt 带当前画布尺寸", "832×1216" in ctx.last_prompt, ctx.last_prompt[:300])
    check("prompt 禁止比例词", "portrait / landscape" in ctx.last_prompt)

    print(f"\n结果：{_ok} 通过 / {_fail} 失败")
    return 1 if _fail else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))

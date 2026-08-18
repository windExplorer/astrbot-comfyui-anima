"""NSFW 检测模块：封装 opennsfw-onnx 做图片色情内容分类。

open_nsfw（Yahoo ResNet-50）输出 P(nsfw)，模型权重已内置在 ``opennsfw-onnx`` 包内
（约 22.5MB），无需单独下载。本模块懒加载、容错降级：模型/依赖缺失时返回
``(False, 0.0, available=False)``，不阻塞图库归档等主流程。
"""

import logging
import os

logger = logging.getLogger("astrbot_plugin_comfyui_anima.nsfw")

# 可选依赖（装不上则检测功能不可用，但插件不崩）
try:
    import onnxruntime  # noqa: F401
    _HAS_ONNX = True
except Exception:  # pragma: no cover
    _HAS_ONNX = False

try:
    from opennsfw_onnx import NSFWClassifier
    _HAS_OPENNSFW = True
except Exception:  # pragma: no cover
    _HAS_OPENNSFW = False


class NSFWDetector:
    """NSFW 检测器（单例/懒加载）。线程不安全但足够（AstrBot 单事件循环）。"""

    def __init__(self, threshold: float = 0.5):
        self._clf = None
        self._init_failed = False
        try:
            self.threshold = max(0.0, min(1.0, float(threshold or 0.5)))
        except (TypeError, ValueError):
            self.threshold = 0.5

    def available(self) -> bool:
        """检测能力是否可用（依赖 + 模型均已就绪）。"""
        return self._ensure() is not None

    def _ensure(self):
        """懒加载分类器。失败返回 None 并记一次日志。"""
        if self._clf is not None:
            return self._clf
        if self._init_failed:
            return None
        if not _HAS_ONNX or not _HAS_OPENNSFW:
            logger.warning(
                "[NSFW] 依赖缺失（onnxruntime / opennsfw-onnx），NSFW 检测不可用。"
                "请执行：pip install onnxruntime opennsfw-onnx"
            )
            self._init_failed = True
            return None
        try:
            self._clf = NSFWClassifier()
            logger.info("[NSFW] opennsfw-onnx 分类器已就绪")
            return self._clf
        except Exception as e:  # pragma: no cover
            logger.warning(f"[NSFW] 初始化分类器失败（NSFW 检测不可用）: {e}")
            self._init_failed = True
            return None

    def detect(self, image_path: str):
        """检测一张图片，返回 ``(is_nsfw, score, available)``。

        - ``is_nsfw``：bool，P(nsfw) >= threshold 判定为 NSFW
        - ``score``：float，P(nsfw) 置信度（0~1）；检测不可用/失败时为 0.0
        - ``available``：bool，本次检测是否真正执行（False=模型不可用/失败）
        """
        if not image_path or not os.path.exists(image_path):
            return False, 0.0, False
        clf = self._ensure()
        if clf is None:
            return False, 0.0, False
        try:
            pred = clf.classify(image_path)
            score = float(getattr(pred, "nsfw", 0.0) or 0.0)
            # opennsfw-onnx 默认阈值 0.5；这里按配置阈值判定
            is_nsfw = bool(score >= self.threshold)
            return is_nsfw, score, True
        except Exception as e:  # pragma: no cover
            logger.warning(f"[NSFW] 检测图片失败 {os.path.basename(str(image_path))}: {e}")
            return False, 0.0, False


# 全局单例（进程内复用，避免重复加载模型）
_DETECTOR: "NSFWDetector | None" = None


def get_detector(threshold: float = 0.5) -> "NSFWDetector | None":
    """返回全局单例检测器；配置阈值变化时重建。"""
    global _DETECTOR
    if _DETECTOR is None:
        _DETECTOR = NSFWDetector(threshold)
    else:
        try:
            _DETECTOR.threshold = max(0.0, min(1.0, float(threshold or 0.5)))
        except (TypeError, ValueError):
            pass
    return _DETECTOR

# 临时脚本：Chrome headless 截图 gallery_help.html -> 裁剪底部空白 -> 输出 gallery_help.png
import shutil
import subprocess
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parent
CHROME = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
SHOT_H = 1400
W = 720
SHOT_H = 1500

# 复制底图（用户素材）到 _new_help/base.webp，HTML 引用 base.webp 避免空格
src = ROOT.parent / "UI参考图" / "28C52ED58C18A75DF3E348AB743DC103 (2).webp"
shutil.copy2(src, ROOT / "base.webp")
# 复制萌字体（霞鹜文楷，来自参考项目 templates，OFL 开源协议）
shutil.copy2(
    ROOT.parent / "_References" / "astrbot_plugin_get_px" / "templates" / "checkin_themes" / "default" / "fonts" / "LXGWWenKaiLite-GB2312.woff2",
    ROOT / "LXGWWenKaiLite-GB2312.woff2",
)
# 复制项目 logo
shutil.copy2(ROOT.parent / "logo.png", ROOT / "logo.png")

name = "gallery_help"
html = ROOT / f"{name}.html"
raw = ROOT / f"{name}_raw.png"
out = ROOT / f"{name}.png"
cmd = [
    CHROME, "--headless", "--disable-gpu", "--hide-scrollbars",
    f"--window-size={W},{SHOT_H}",
    f"--screenshot={raw}", html.resolve().as_uri(),
]
r = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
print(name, "rc=", r.returncode, r.stderr[:200] if r.returncode else "ok")

im = Image.open(raw).convert("RGB")
w, h = im.size
px = im.load()

def has_content(y):
    for x in range(0, w - 6, 8):
        r1, g1, b1 = px[x, y]
        r2, g2, b2 = px[x + 6, y]
        if abs(r1 - r2) + abs(g1 - g2) + abs(b1 - b2) > 24:
            return True
    return False

crop_h = h
for y in range(h - 1, 0, -1):
    if has_content(y):
        crop_h = min(h, y + 46)
        break
if crop_h < h:
    im = im.crop((0, 0, w, crop_h))
im.save(out)
print("  saved", out, im.size)

import { useMessage } from "naive-ui";
import { apiPost } from "@/api/bridge";

function readAsDataURL(file: Blob): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onerror = () => reject(new Error("读取图片失败"));
    reader.onload = () => resolve(reader.result as string);
    reader.readAsDataURL(file);
  });
}

// 封面只需缩略图：压缩到最长边 maxDim、转 JPEG 质量 quality，规避反代 413（Request Entity Too Large）。
// 压缩失败（如非位图 / 动图）则回退原图直传，不阻断上传。
function compressImage(file: File, maxDim: number, quality: number): Promise<{ blob: Blob; name: string }> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onerror = () => reject(new Error("读取图片失败"));
    reader.onload = () => {
      const img = new Image();
      img.onerror = () => reject(new Error("图片解析失败"));
      img.onload = () => {
        const ow = img.width || 1;
        const oh = img.height || 1;
        const scale = Math.min(1, maxDim / Math.max(ow, oh));
        const w = Math.max(1, Math.round(ow * scale));
        const h = Math.max(1, Math.round(oh * scale));
        const canvas = document.createElement("canvas");
        canvas.width = w;
        canvas.height = h;
        const ctx = canvas.getContext("2d");
        if (!ctx) {
          reject(new Error("无法创建画布"));
          return;
        }
        // 先铺白底，避免透明 PNG 压成 JPEG 后变黑
        ctx.fillStyle = "#fff";
        ctx.fillRect(0, 0, w, h);
        ctx.drawImage(img, 0, 0, w, h);
        canvas.toBlob(
          (blob) => {
            if (!blob) {
              reject(new Error("图片压缩失败"));
              return;
            }
            const base = (file.name || "cover").replace(/\.[^.]+$/, "");
            resolve({ blob, name: base + ".jpg" });
          },
          "image/jpeg",
          quality
        );
      };
      img.src = reader.result as string;
    };
    reader.readAsDataURL(file);
  });
}

export function useCover() {
  const message = useMessage();

  // 本地图片文件 → base64 上传，返回 lora_assets 文件名；失败已弹提示并返回 null
  async function uploadFile(file: File): Promise<string | null> {
    const mh = message.loading("上传中…", { duration: 0 });
    try {
      // 压缩后再上传，规避反代请求体大小限制（413 Request Entity Too Large）
      let payload: Blob = file;
      let payloadName = file.name || "cover.jpg";
      try {
        const c = await compressImage(file, 512, 0.85);
        payload = c.blob;
        payloadName = c.name;
      } catch {
        // 压缩失败（如非位图 / GIF 动图）：退回原图直传
      }
      const b64 = await readAsDataURL(payload);
      const d: any = await apiPost("lora/upload_image", { filename: payloadName, data: b64 });
      return d.name;
    } catch (e: any) {
      message.error(e?.message || "上传失败");
      return null;
    } finally {
      mh.destroy();
    }
  }

  // 图片直链 → 下载到 lora_assets，返回文件名
  async function fetchUrl(url: string): Promise<string | null> {
    const mh = message.loading("下载中…", { duration: 0 });
    try {
      const d: any = await apiPost("lora/fetch", { url, direct_image: true }, { timeout: 60000 });
      return d.name;
    } catch (e: any) {
      message.error(e?.message || "下载失败");
      return null;
    } finally {
      mh.destroy();
    }
  }

  return { uploadFile, fetchUrl };
}

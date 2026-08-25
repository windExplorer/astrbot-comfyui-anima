import { useMessage } from "naive-ui";
import { apiPost } from "@/api/bridge";

function readAsDataURL(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onerror = () => reject(new Error("文件读取失败"));
    reader.onload = () => {
      const b64 = String(reader.result || "").split(",")[1] || "";
      resolve(b64);
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
      const b64 = await readAsDataURL(file);
      const d: any = await apiPost("lora/upload_image", { filename: file.name, data: b64 });
      return d.name;
    } catch (e: any) {
      message.error(e?.message || "上传失败");
      return null;
    } finally {
      mh.destroy();
    }
  }

  // 任意图片直链 → 后端下载，返回 lora_assets 文件名；失败已弹提示并返回 null
  async function fetchUrl(url: string): Promise<string | null> {
    if (!/^https?:\/\//i.test(url)) {
      message.warning("请输入 http(s) 图片直链");
      return null;
    }
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

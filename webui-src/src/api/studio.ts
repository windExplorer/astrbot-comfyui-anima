/**
 * 在线生图工作台（studio）API 封装。
 * 端点一律走 apiGet/apiPost（不带前导斜杠、不带插件名），双通道（内嵌页/独立服务）
 * 由 bridge 自动适配。图片展示复用 fetchThumb / standaloneImgUrl 的双模式逻辑。
 */
import { apiGet, apiPost, fetchThumb, isStandaloneMode, standaloneImgUrl } from "@/api/bridge";

export interface StudioTemplate {
  id: number;
  name: string;
  category: string;
  content: string;
  is_negative: boolean;
  enabled: boolean;
  sort_order: number;
}

export interface StudioSamplerDefaults {
  steps?: number | null;
  cfg?: number | null;
  denoise?: number | null;
  sampler_name?: string;
  scheduler?: string;
}

export interface StudioWorkflow {
  name: string;
  enabled: boolean;
  /** 基础工作流解析出的类型（waifu 同款三层口径：注记→fail-open→名字兜底） */
  kind: string | null;
  base_name: string;
  basemodel_name: string;
  prompt_style: string;
  is_anima: boolean;
  /** 工作流带 TextEncodeQwenImage21 类多参考节点 */
  multi_image: boolean;
  /** 绑定服务器的展示名（名字而非 key/ID；解析不到为空） */
  server_name: string;
  default_width: number | null;
  default_height: number | null;
  default_positive: string;
  default_negative: string;
  loras_text: string;
  size_switch: boolean | null;
  max_refs: number;
  aliases: string;
  image: string;
  description: string;
  /** 基础工作流解析出的采样默认值（steps/cfg/denoise…），null=未解析到 */
  sampler_defaults: StudioSamplerDefaults | null;
  /** 按底模兼容性筛好的可用 LoRA 名单（与聊天侧 _lora_matches_wf 同规则） */
  lora_names: string[];
}

export interface StudioLoraPreset {
  name: string;
  always_on: boolean;
}

export interface StudioLora {
  name: string;
  enabled: boolean;
  category: string;
  base_model: string;
  trigger_words: string;
  description: string;
  weight: number | null;
  model_only: boolean;
  presets: StudioLoraPreset[];
  image: string;
  starred: boolean;
}

export interface StudioSizeTier {
  key: string;
  label: string;
  factor: number;
  budget_mp: number;
}

export interface StudioSizeRatio {
  name: string;
  keywords: string;
  enabled: boolean;
  base: string;
  sizes: Record<string, string>;
}

export interface StudioRefItem {
  ref_id: string;
  /** 本地预览 data URL（上传前由 FileReader 生成，双通道一致） */
  preview: string;
  name: string;
}

export interface StudioLoraSel {
  name: string;
  /** null = 用 LoRA 库默认权重 */
  weight: number | null;
  preset: string;
}

export interface StudioGenerateParams {
  mode: "t2i" | "img2img" | "multi_ref";
  workflow: string;
  positive: string;
  negative: string;
  width: number | null;
  height: number | null;
  seed: number | null;
  steps: number | null;
  cfg: number | null;
  denoise: number | null;
  sampler: string;
  noise_schedule: string;
  custom_size: boolean | null;
  llm_refine: boolean;
  loras: StudioLoraSel[];
  ref_ids: string[];
}

export type StudioJobState = "queued" | "running" | "done" | "error" | "discarded";

export interface StudioJob {
  id: string;
  state: StudioJobState;
  elapsed: number | null;
  error: string;
  images: Array<{ sha: string }>;
  nsfw_blocked: number;
  params: Partial<StudioGenerateParams>;
  created_at: number;
}

export interface StudioOverview {
  workflows: StudioWorkflow[];
  loras: StudioLora[];
  size_tiers: { tiers: StudioSizeTier[]; ratios: StudioSizeRatio[]; default_max_tier: string };
  servers: Array<{ name: string; url: string; device: string }>;
  llm_ready: boolean;
  max_refs: number;
  templates: StudioTemplate[];
  limits: { max_active: number; history_limit: number };
}

export interface StudioQueueServer {
  name: string;
  running: number | null;
  pending: number | null;
  ok: boolean;
}

export interface StudioGalleryItem {
  sha?: string;
  sha256?: string;
  prompt?: string;
  workflow?: string;
  user_name?: string;
  user_id?: string;
  created_at?: number;
  w?: number;
  h?: number;
  [k: string]: any;
}

export const studioApi = {
  overview: (): Promise<StudioOverview> => apiGet("studio/overview", undefined, { timeout: 30000 }),
  uploadRef: (dataUrl: string): Promise<{ ref_id: string }> =>
    apiPost("studio/upload_ref", { data_url: dataUrl }, { timeout: 60000 }),
  uploadRefFromGallery: (sha: string): Promise<{ ref_id: string }> =>
    apiPost("studio/upload_ref_gallery", { sha }),
  generate: (params: StudioGenerateParams): Promise<{ job_id: string }> =>
    apiPost("studio/generate", params as any, { timeout: 30000 }),
  jobs: (limit = 30): Promise<{ jobs: StudioJob[] }> => apiGet("studio/jobs", { limit }),
  job: (id: string): Promise<StudioJob> => apiGet("studio/job", { id }),
  discard: (id: string): Promise<any> => apiPost("studio/job/discard", { id }),
  /** ComfyUI 服务器队列概览（右栏「服务器状态」块，约 5s 轮询一次） */
  queue: (): Promise<{ servers: StudioQueueServer[] }> => apiGet("studio/queue", undefined, { timeout: 12000 }),
  /** 采样器/调度器枚举（后端从 /object_info 的 KSampler 节点提取并缓存） */
  samplers: (): Promise<{ samplers: string[]; schedulers: string[]; source: string }> =>
    apiGet("studio/samplers", undefined, { timeout: 45000 }),
  /** 最近成品（图库直连，type=gen 只取生图成品） */
  recentGens: (size = 9): Promise<{ images: StudioGalleryItem[]; total: number }> =>
    apiGet("gallery/search", { page: 1, size, type: "gen" }, { timeout: 20000 }),
  /** 全局中断 ComfyUI 当前任务 —— 调用前必须二次确认 */
  interrupt: (): Promise<{ results: Record<string, boolean> }> =>
    apiPost("studio/interrupt", {}, { timeout: 30000 }),
  /** AI 生成/润色提示词。mode: expand / translate / anima / natural / qwen_edit */
  llmPrompt: (mode: string, text: string, loraRef = ""): Promise<{ text: string }> =>
    apiPost("studio/llm_prompt", { mode, text, lora_ref: loraRef }, { timeout: 120000 }),
  templates: (): Promise<{ items: StudioTemplate[] }> => apiGet("studio/templates"),
  saveTemplate: (t: Partial<StudioTemplate>): Promise<{ id: number }> =>
    apiPost("studio/templates/save", t as any),
  deleteTemplate: (id: number): Promise<any> => apiPost("studio/templates/delete", { id }),
  reseedTemplates: (): Promise<{ added: number }> => apiPost("studio/templates/reseed", {}),
};

/** 任务结果图展示：独立模式走 /img/{sha} 直链；内嵌模式走 gallery/thumb base64。 */
export async function studioImg(sha: string, size?: number): Promise<string> {
  if (!sha) return "";
  return fetchThumb(sha, size ?? 1024);
}

/** 下载结果图。独立模式直接 blob 下载原图；内嵌模式走 base64（>8MB 会失败）。 */
export async function downloadStudioImage(sha: string, filename = ""): Promise<void> {
  let dataUrl = "";
  if (isStandaloneMode()) {
    const resp = await fetch(standaloneImgUrl(sha));
    if (!resp.ok) throw new Error("下载失败（HTTP " + resp.status + "）");
    const blob = await resp.blob();
    dataUrl = await new Promise((resolve, reject) => {
      const fr = new FileReader();
      fr.onload = () => resolve(String(fr.result));
      fr.onerror = () => reject(new Error("读取图片失败"));
      fr.readAsDataURL(blob);
    });
  } else {
    const d = await apiGet("gallery/image", { sha, raw: 1 }, { timeout: 60000 });
    dataUrl = (d && (d.data_url || d.url)) || "";
    if (!dataUrl) throw new Error("下载失败：图片过大或不可用");
  }
  const a = document.createElement("a");
  a.href = dataUrl;
  a.download = filename || `anima_${sha.slice(0, 12)}.png`;
  document.body.appendChild(a);
  a.click();
  a.remove();
}

/** 文件 → data URL（参考图上传前的本地预览与上传体）。 */
export function fileToDataUrl(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const fr = new FileReader();
    fr.onload = () => resolve(String(fr.result));
    fr.onerror = () => reject(new Error("读取文件失败"));
    fr.readAsDataURL(file);
  });
}

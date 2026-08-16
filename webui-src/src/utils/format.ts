/** 通用格式化工具 */

export function fmtBytes(bytes: number | null | undefined): string {
  if (bytes == null || isNaN(bytes) || bytes < 0) return "-";
  if (bytes < 1024) return bytes + " B";
  const kb = bytes / 1024;
  if (kb < 1024) return kb.toFixed(1) + " KB";
  const mb = kb / 1024;
  if (mb < 1024) return mb.toFixed(2) + " MB";
  return (mb / 1024).toFixed(2) + " GB";
}

export function fmtDuration(sec: number | null | undefined): string {
  if (sec == null || isNaN(sec) || sec < 0) return "-";
  if (sec < 1) return (sec * 1000).toFixed(0) + "ms";
  if (sec < 60) return sec.toFixed(1) + "s";
  const m = Math.floor(sec / 60);
  const s = Math.round(sec % 60);
  return `${m}m${s}s`;
}

export function fmtTime(ts: number | string | null | undefined): string {
  if (ts == null) return "-";
  const t = typeof ts === "number" ? new Date(ts * 1000) : new Date(ts);
  if (isNaN(t.getTime())) return String(ts);
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${t.getFullYear()}-${pad(t.getMonth() + 1)}-${pad(t.getDate())} ${pad(t.getHours())}:${pad(t.getMinutes())}`;
}

export function fmtDateTime(ts: number | string | null | undefined): string {
  if (ts == null) return "-";
  const t = typeof ts === "number" ? new Date(ts * 1000) : new Date(ts);
  if (isNaN(t.getTime())) return String(ts);
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${t.getFullYear()}-${pad(t.getMonth() + 1)}-${pad(t.getDate())} ${pad(t.getHours())}:${pad(t.getMinutes())}:${pad(t.getSeconds())}`;
}

export function truncate(text: string | null | undefined, max: number): string {
  if (!text) return "";
  return text.length > max ? text.slice(0, max) + "…" : text;
}

export function parseAliases(raw: string | null | undefined): string[] {
  if (!raw) return [];
  return raw.split(/[,，\n\r]+/).map((s) => s.trim()).filter(Boolean);
}

/**
 * sanitizeHtml：轻量 HTML 白名单净化（无外部依赖）。
 *
 * 用于在 WebUI 中安全渲染用户/C 站提供的富文本（如 LoRA 描述中的 <strong>、<p>、
 * <br> 等），同时彻底剥离脚本、事件属性与危险协议，避免 XSS。
 *
 * 仅保留白名单内的标签与属性；不在白名单的标签会被「降级为纯文本」（保留其子内容）；
 * 所有 on* 事件属性被移除；href/src 仅放行 http(s)/mailto。
 */

const ALLOWED_TAGS = new Set([
  "b", "strong", "i", "em", "u", "s", "p", "br", "ul", "ol", "li",
  "blockquote", "code", "pre", "h1", "h2", "h3", "h4", "h5", "h6",
  "span", "a", "img", "table", "thead", "tbody", "tr", "td", "th",
  "div", "hr", "dl", "dt", "dd",
]);

const ALLOWED_ATTRS = new Set([
  "href", "title", "alt", "src", "width", "height", "target", "rel", "class",
]);

/** 危险协议前缀（href/src 一律只放行这些） */
const SAFE_PROTO = ["http:", "https:", "mailto:", "data:image/"];

function safeUrl(raw: string, attr: string): string {
  const v = (raw || "").trim();
  const low = v.toLowerCase();
  if (SAFE_PROTO.some((p) => low.startsWith(p))) return v;
  // data: 仅放行图片（用于 img src），其余协议一律移除
  if (attr === "src" && low.startsWith("data:image/")) return v;
  return "";
}

function cleanNode(el: Element): void {
  // 1) 逐属性清洗
  const attrs = Array.from(el.attributes);
  for (const attr of attrs) {
    const name = attr.name.toLowerCase();
    if (name.startsWith("on")) {
      el.removeAttribute(attr.name);
      continue;
    }
    if (!ALLOWED_ATTRS.has(name)) {
      el.removeAttribute(attr.name);
      continue;
    }
    if (name === "href" || name === "src") {
      const cleaned = safeUrl(attr.value, name);
      if (cleaned) {
        attr.value = cleaned;
      } else {
        el.removeAttribute(attr.name);
      }
    }
  }
  // 2) 递归清洗子节点
  Array.from(el.children).forEach((child) => cleanNode(child));
}

export function sanitizeHtml(input: string): string {
  if (!input) return "";
  if (typeof DOMParser === "undefined") {
    // 非浏览器环境（如 SSR/测试）退化：只转义，不渲染 HTML
    return input.replace(/[&<>"']/g, (c) => ({
      "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
    })[c] as string);
  }
  const doc = new DOMParser().parseFromString(input, "text/html");
  const body = doc.body;

  // 移除非白名单标签，保留其文本/子内容（降级为纯文本）
  const walker = doc.createTreeWalker(body, NodeFilter.SHOW_ELEMENT);
  const toUnwrap: Element[] = [];
  let node = walker.nextNode();
  while (node) {
    const el = node as Element;
    if (!ALLOWED_TAGS.has(el.tagName.toLowerCase())) {
      toUnwrap.push(el);
    }
    node = walker.nextNode();
  }
  for (const el of toUnwrap) {
    el.replaceWith(...Array.from(el.childNodes));
  }

  // 清洗白名单内标签的属性
  Array.from(body.children).forEach((child) => cleanNode(child));

  return body.innerHTML;
}

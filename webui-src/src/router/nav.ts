/**
 * PC 侧边栏与移动端抽屉共用的导航条目（单一数据源）。
 * 保证移动端菜单顺序与 PC 端完全一致；新增页面时两处同步生效。
 */
export interface NavItem {
  path: string;
  name: string;
  label: string;
  icon: string;
  key: string;
}

export const NAV_ITEMS: NavItem[] = [
  { path: "/config", name: "config", label: "配置", icon: "⚙️", key: "config" },
  { path: "/logs", name: "logs", label: "日志", icon: "📋", key: "logs" },
  { path: "/stats", name: "stats", label: "统计", icon: "📊", key: "stats" },
  { path: "/workflows", name: "workflows", label: "工作流", icon: "🗂️", key: "workflows" },
  { path: "/loras", name: "loras", label: "LoRA", icon: "🎨", key: "loras" },
  { path: "/gallery", name: "gallery", label: "图库", icon: "🖼️", key: "gallery" },
  { path: "/quota", name: "quota", label: "限额", icon: "🚦", key: "quota" },
  { path: "/token", name: "token", label: "Token", icon: "🔑", key: "token" },
  { path: "/share-manage", name: "share-manage", label: "分享管理", icon: "🔗", key: "share-manage" },
];
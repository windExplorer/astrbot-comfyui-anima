/**
 * 移动端抽屉（MobileShell.vue）使用的导航条目。
 * ⚠️ PC 侧边栏并不读这里，而是 App.vue 里独立的 menuOptions 硬编码列表
 * （两者内容/顺序已允许不一致：PC 含旧版工作流、配置项、基础工作流等额外项）。
 * 新增页面时必须同步改 App.vue 的 menuOptions，否则移动端有入口、PC 侧边栏缺失
 * （v8.13.0「图片复核」只加在这里，PC 侧漏了 → v8.13.2 修复的教训）。
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
  { path: "/studio", name: "studio", label: "在线生图", icon: "🖌️", key: "studio" },
  { path: "/role-cards", name: "role-cards", label: "角色卡", icon: "🎭", key: "role-cards" },
  { path: "/features", name: "features", label: "更多功能", icon: "🧩", key: "features" },
  { path: "/logs", name: "logs", label: "日志", icon: "📋", key: "logs" },
  { path: "/stats", name: "stats", label: "统计", icon: "📊", key: "stats" },
  { path: "/workflows", name: "workflows", label: "工作流", icon: "🗂️", key: "workflows" },
  { path: "/loras", name: "loras", label: "LoRA", icon: "🎨", key: "loras" },
  { path: "/platforms", name: "platforms", label: "生图平台", icon: "🌐", key: "platforms" },
  { path: "/gallery", name: "gallery", label: "图库", icon: "🖼️", key: "gallery" },
  { path: "/review", name: "review", label: "图片复核", icon: "🛡️", key: "review" },
  { path: "/quota", name: "quota", label: "限额", icon: "🚦", key: "quota" },
  { path: "/sessions", name: "sessions", label: "会话管理", icon: "💬", key: "sessions" },
  { path: "/token", name: "token", label: "Token", icon: "🔑", key: "token" },
  { path: "/share-manage", name: "share-manage", label: "分享管理", icon: "🔗", key: "share-manage" },
];
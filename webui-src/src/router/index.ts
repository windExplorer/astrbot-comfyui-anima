import { createRouter, createWebHashHistory } from "vue-router";
import ConfigView from "@/views/ConfigView.vue";
import LogsView from "@/views/LogsView.vue";
import StatsView from "@/views/StatsView.vue";
import WorkflowsView from "@/views/WorkflowsView.vue";
import FeaturesView from "@/views/FeaturesView.vue";
import LorasView from "@/views/LorasView.vue";
import GalleryView from "@/views/GalleryView.vue";
import QuotaView from "@/views/QuotaView.vue";
import TokenView from "@/views/TokenView.vue";
import ShareView from "@/views/ShareView.vue";
import ShareManageView from "@/views/ShareManageView.vue";
import StoryView from "@/views/StoryView.vue";
import LoginView from "@/views/LoginView.vue";
import { authState, checkStandaloneAuth } from "@/composables/auth";
import { isStandaloneMode } from "@/api/bridge";

// AstrBot 插件页面以静态文件提供，必须用 hash 路由（history 路由刷新会 404）。
// 全部使用静态 import（而非懒加载 import()）：确保构建产物为单文件、无跨 chunk 动态
// import，避免 AstrBot 旧版本无法重写动态 chunk 导致资源 401、页面空白。
const router = createRouter({
  history: createWebHashHistory(),
  routes: [
    { path: "/", redirect: "/config" },
    { path: "/login", name: "login", component: LoginView, meta: { title: "访问口令", public: true } },
    { path: "/config", name: "config", component: ConfigView, meta: { title: "配置" } },
    { path: "/logs", name: "logs", component: LogsView, meta: { title: "日志" } },
    { path: "/stats", name: "stats", component: StatsView, meta: { title: "统计" } },
    { path: "/workflows", name: "workflows", component: WorkflowsView, meta: { title: "工作流" } },
    { path: "/features", name: "features", component: FeaturesView, meta: { title: "功能配置" } },
    { path: "/loras", name: "loras", component: LorasView, meta: { title: "LoRA" } },
    { path: "/gallery", name: "gallery", component: GalleryView, meta: { title: "图库" } },
    { path: "/quota", name: "quota", component: QuotaView, meta: { title: "限额" } },
    { path: "/token", name: "token", component: TokenView, meta: { title: "Token" } },
    { path: "/share-manage", name: "share-manage", component: ShareManageView, meta: { title: "分享管理" } },
    { path: "/share", name: "share", component: ShareView, meta: { title: "萌绘分享", public: true } },
    { path: "/story", name: "story", component: StoryView, meta: { title: "剧情档案" } },
    { path: "/:pathMatch(.*)*", redirect: "/config" },
  ],
});

// 独立服务认证守卫：登录页放行；独立模式下未认证的路由强制跳转 /login。
// 内嵌页（AstrBot）无 token 概念，直接放行。
router.beforeEach(async (to) => {
  // 分享站为公开路由（令牌鉴权），不要求独立服务管理员登录
  if (to.meta && to.meta.public) return true;
  if (!isStandaloneMode()) {
    // 内嵌页：不做独立口令校验
    return true;
  }
  // /login 永远放行
  if (to.name === "login") {
    return true;
  }
  // 若尚未探测（初始 authed 占位），先探测一次
  if (authState.value !== "authed") {
    await checkStandaloneAuth();
  }
  if (authState.value === "unauthed") {
    // 未认证 → 强制到登录页
    return { name: "login", query: { redirect: to.fullPath } };
  }
  return true;
});

export default router;

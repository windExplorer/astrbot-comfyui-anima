import { createRouter, createWebHashHistory } from "vue-router";
import ConfigView from "@/views/ConfigView.vue";
import LogsView from "@/views/LogsView.vue";
import StatsView from "@/views/StatsView.vue";
import WorkflowsView from "@/views/WorkflowsView.vue";
import LorasView from "@/views/LorasView.vue";
import GalleryView from "@/views/GalleryView.vue";
import QuotaView from "@/views/QuotaView.vue";
import TokenView from "@/views/TokenView.vue";

// AstrBot 插件页面以静态文件提供，必须用 hash 路由（history 路由刷新会 404）。
// 全部使用静态 import（而非懒加载 import()）：确保构建产物为单文件、无跨 chunk 动态
// import，避免 AstrBot 旧版本无法重写动态 chunk 导致资源 401、页面空白。
const router = createRouter({
  history: createWebHashHistory(),
  routes: [
    { path: "/", redirect: "/config" },
    { path: "/config", name: "config", component: ConfigView, meta: { title: "配置" } },
    { path: "/logs", name: "logs", component: LogsView, meta: { title: "日志" } },
    { path: "/stats", name: "stats", component: StatsView, meta: { title: "统计" } },
    { path: "/workflows", name: "workflows", component: WorkflowsView, meta: { title: "工作流" } },
    { path: "/loras", name: "loras", component: LorasView, meta: { title: "LoRA" } },
    { path: "/gallery", name: "gallery", component: GalleryView, meta: { title: "图库" } },
    { path: "/quota", name: "quota", component: QuotaView, meta: { title: "限额" } },
    { path: "/token", name: "token", component: TokenView, meta: { title: "Token" } },
    { path: "/:pathMatch(.*)*", redirect: "/config" },
  ],
});

export default router;

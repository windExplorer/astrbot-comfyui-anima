import { createRouter, createWebHashHistory } from "vue-router";

// AstrBot 插件页面以静态文件提供，必须用 hash 路由（history 路由刷新会 404）。
const router = createRouter({
  history: createWebHashHistory(),
  routes: [
    { path: "/", redirect: "/config" },
    { path: "/config", name: "config", component: () => import("@/views/ConfigView.vue"), meta: { title: "配置" } },
    { path: "/logs", name: "logs", component: () => import("@/views/LogsView.vue"), meta: { title: "日志" } },
    { path: "/stats", name: "stats", component: () => import("@/views/StatsView.vue"), meta: { title: "统计" } },
    { path: "/workflows", name: "workflows", component: () => import("@/views/WorkflowsView.vue"), meta: { title: "工作流" } },
    { path: "/loras", name: "loras", component: () => import("@/views/LorasView.vue"), meta: { title: "LoRA" } },
    { path: "/gallery", name: "gallery", component: () => import("@/views/GalleryView.vue"), meta: { title: "图库" } },
    { path: "/quota", name: "quota", component: () => import("@/views/QuotaView.vue"), meta: { title: "限额" } },
    { path: "/token", name: "token", component: () => import("@/views/TokenView.vue"), meta: { title: "Token" } },
    { path: "/:pathMatch(.*)*", redirect: "/config" },
  ],
});

export default router;

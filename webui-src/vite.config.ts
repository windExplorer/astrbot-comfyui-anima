import { defineConfig } from "vite";
import vue from "@vitejs/plugin-vue";
import { fileURLToPath, URL } from "node:url";

// AstrBot 插件页面以静态资源方式从 pages/anima-console-vue/ 提供。
// 必须用相对 base（./），且构建产物输出到 ../pages/anima-console-vue。
// 插件页面用 hash 路由（createWebHashHistory），故无需 SPA fallback。
export default defineConfig({
  plugins: [vue()],
  base: "./",
  resolve: {
    alias: {
      "@": fileURLToPath(new URL("./src", import.meta.url)),
    },
  },
  build: {
    outDir: "../pages/anima-console-vue",
    emptyOutDir: true,
    chunkSizeWarningLimit: 1200,
    rollupOptions: {
      output: {
        // 分包策略：把体积大的依赖拆出来，便于 AstrBot 资源重写时按需缓存
        manualChunks: {
          "vue-vendor": ["vue", "vue-router"],
          "naive": ["naive-ui"],
          "vchart": ["@visactor/vchart"],
        },
      },
    },
  },
  server: {
    port: 5173,
    // 开发时用本地 mock 或反向代理到 AstrBot？开发环境直接 fallback 到 index，生产产物不受影响。
    proxy: {},
  },
});

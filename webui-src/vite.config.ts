import { defineConfig } from "vite";
import vue from "@vitejs/plugin-vue";
import cssInjectedByJs from "vite-plugin-css-injected-by-js";
import { fileURLToPath, URL } from "node:url";

// AstrBot 插件页面以静态资源方式从 pages/anima-console-vue/ 提供。
//
// ⚠️ 兼容性关键（务必保持）：
//  AstrBot 的 plugin_page_service 只会对「入口 index.html 直接引用的资源」重写为带
//  asset_token 的绝对路径；而对「入口 JS 内部动态 import 出来的 chunk」能否正确重写，
//  取决于运行中的 AstrBot 版本（旧版本的正则不支持 Vite 的跨 chunk import，会导致这些
//  chunk 以无 token 的相对路径请求 → 401 → 页面空白）。
//
//  为确保在任何 AstrBot 版本下都能打开，这里强制构建为【单文件】：
//    - inlineDynamicImports: true → 路由懒加载被合并进单个 JS，不再产生跨 chunk import
//    - 不拆 manualChunks → vue/naive 全部打进同一个 JS（图表为手写 SVG，无重量级库）
//  产物只有 index.html + 单个 assets/index-*.js + 单个 assets/index-*.css，
//  入口 index.html 直接用 <script>/<link> 引用它们，AstrBot 重写一次即全部可加载。
//
// 必须用相对 base（./），且构建产物输出到 ../pages/anima-console-vue。
// 插件页面用 hash 路由（createWebHashHistory），故无需 SPA fallback。
export default defineConfig({
  plugins: [vue(), cssInjectedByJs()],
  base: "./",
  resolve: {
    alias: {
      "@": fileURLToPath(new URL("./src", import.meta.url)),
    },
  },
  build: {
    outDir: "../pages/anima-console-vue",
    emptyOutDir: true,
    chunkSizeWarningLimit: 4000,
    cssCodeSplit: false,
    rollupOptions: {
      output: {
        inlineDynamicImports: true,
      },
    },
  },
  server: {
    port: 5173,
    proxy: {},
  },
});

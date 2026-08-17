import { createApp } from "vue";
import App from "./App.vue";
import router from "./router";

// Naive UI 全量引入（控制台规模，tree-shaking 收益有限，全量最省事）
import naive from "naive-ui";
// 封面图懒加载指令（受限并发 + 视口懒加载，避免一次性打爆 postMessage 桥接）
import { vCoverLazy } from "./directives/coverLazy";

const app = createApp(App);
app.use(router);
app.use(naive);
app.directive("cover-lazy", vCoverLazy);
app.mount("#app");

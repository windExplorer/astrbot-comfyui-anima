import { createApp } from "vue";
import App from "./App.vue";
import router from "./router";

// Naive UI 全量引入（控制台规模，tree-shaking 收益有限，全量最省事）
import naive from "naive-ui";

const app = createApp(App);
app.use(router);
app.use(naive);
app.mount("#app");

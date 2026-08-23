<template>
  <div class="auth-login-page">
    <div class="auth-login-card">
      <div class="auth-login-logo-wrap">
        <img :src="LOGO_DATA_URL" alt="logo" class="auth-login-logo" />
      </div>
      <div class="auth-login-title">萌绘控制台</div>
      <div class="auth-login-sub">该控制台已设置访问口令，请输入口令进入</div>
      <n-input
        v-model:value="authInput"
        type="password"
        size="large"
        placeholder="请输入访问口令"
        show-password-on="click"
        @keyup.enter="onSubmit"
      />
      <n-button type="primary" size="large" block :loading="authLoading" @click="onSubmit">确认进入</n-button>
      <div v-if="authError" class="auth-login-error">{{ authError }}</div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref } from "vue";
import { useRouter, useRoute } from "vue-router";
import { NInput, NButton } from "naive-ui";
import { submitToken, authState } from "@/composables/auth";
import { LOGO_DATA_URL } from "@/assets/logo";

const router = useRouter();
const route = useRoute();
const authInput = ref("");
const authLoading = ref(false);
const authError = ref("");

async function onSubmit() {
  const token = (authInput.value || "").trim();
  if (!token) return;
  authLoading.value = true;
  authError.value = "";
  try {
    const res = await submitToken(token);
    if (res.ok) {
      // 标记已认证（令牌已存 localStorage，后续请求自动携带）
      authState.value = "authed";
      // 跳转到来源页或控制台首页，而非停留在登录页
      const redirect = typeof route.query.redirect === "string" ? route.query.redirect : "";
      const target = redirect && redirect.startsWith("/") ? redirect : "/config";
      router.replace(target);
    } else {
      authError.value = res.error || "验证失败";
      authInput.value = "";
    }
  } finally {
    authLoading.value = false;
  }
}
</script>

<style scoped>
.auth-login-page {
  min-height: 100vh;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 24px;
  background: linear-gradient(135deg, #fff0f6 0%, #ffe9f2 50%, #ffe0eb 100%);
}
.auth-login-card {
  width: 380px;
  max-width: 92vw;
  background: #fff;
  border-radius: 16px;
  padding: 40px 32px 32px;
  box-shadow: 0 8px 30px rgba(255, 143, 179, 0.18);
  display: flex;
  flex-direction: column;
  gap: 14px;
  text-align: center;
}
.auth-login-logo-wrap {
  width: 72px;
  height: 72px;
  margin: 0 auto;
  border-radius: 18px;
  background: linear-gradient(135deg, #ff8fb3, #ff6b9d);
  box-shadow: 0 6px 16px rgba(255, 107, 157, 0.35);
  display: flex;
  align-items: center;
  justify-content: center;
  overflow: hidden;
}
.auth-login-logo {
  width: 100%;
  height: 100%;
  object-fit: cover;
  display: block;
}
.auth-login-title {
  font-size: 20px;
  font-weight: 700;
  color: #3a2a33;
}
.auth-login-sub {
  font-size: 13px;
  color: #9a7a88;
  line-height: 1.5;
  margin-bottom: 6px;
}
.auth-login-error {
  font-size: 13px;
  color: #e74c3c;
  margin-top: 4px;
}
@media (max-width: 480px) {
  .auth-login-card { padding: 32px 20px 24px; }
}
</style>

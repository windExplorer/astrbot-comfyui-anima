// 独立服务认证状态（共享，供路由守卫与 LoginView 使用）
import { ref } from "vue";
import { apiGet, isStandaloneMode, setStandaloneToken } from "@/api/bridge";

export type AuthState = "checking" | "unauthed" | "authed";

// 全局认证状态：初始为 checking（待探测），避免初始 authed 绕过守卫
export const authState = ref<AuthState>("checking");

/** 探测认证状态：ping 成功 → authed；401 → unauthed；其他错误 → 保持当前。 */
export async function checkStandaloneAuth(): Promise<void> {
  if (!isStandaloneMode()) {
    authState.value = "authed";
    return;
  }
  authState.value = "checking";
  try {
    await apiGet("ping", {}, { timeout: 8000 });
    authState.value = "authed";
  } catch (e: any) {
    if (e && e.authRequired) {
      authState.value = "unauthed";
    } else {
      // 非鉴权错误：保守视为未认证，引导到登录页
      authState.value = "unauthed";
    }
  }
}

/** 校验口令并进入。返回是否成功。 */
export async function submitToken(token: string): Promise<{ ok: boolean; error?: string }> {
  setStandaloneToken(token);
  try {
    await apiGet("ping", {}, { timeout: 8000 });
    return { ok: true };
  } catch (e: any) {
    if (e && e.authRequired) {
      return { ok: false, error: "口令不正确，请重新输入" };
    }
    return { ok: false, error: (e && e.message) || "验证失败" };
  }
}

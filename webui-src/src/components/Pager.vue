<template>
  <div class="pager" :class="{ 'pager-mobile': isMobile }">
    <span v-if="!isMobile" class="pager-total">共 {{ total }} 条</span>
    <n-pagination
      v-if="!isMobile"
      :page="page"
      :page-size="pageSize"
      :item-count="total"
      :page-sizes="[10, 20, 24, 40, 60, 100]"
      show-size-picker
      @update:page="onPage"
      @update:page-size="onSize"
    />
    <n-pagination
      v-else
      simple
      :page="page"
      :item-count="total"
      @update:page="onPage"
    />
    <div v-if="!isMobile" class="pager-jump">
      <n-input-number
        :value="jumpVal"
        :min="1"
        :max="maxPage"
        size="small"
        style="width:70px"
        placeholder="页"
        @update:value="jumpVal = $event ?? 1"
      />
      <n-button size="small" @click="jump">跳页</n-button>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, ref, watch } from "vue";
import { NPagination, NInputNumber, NButton } from "naive-ui";
import { useDevice } from "@/composables/useDevice";

const props = defineProps<{
  page: number;
  pageSize: number;
  total: number;
}>();

const emit = defineEmits<{
  (e: "update:page", p: number): void;
  (e: "update:page-size", s: number): void;
}>();

const { isMobile } = useDevice();
const jumpVal = ref(props.page);

// 移动端 simple 模式只显示「x / y」+ 上一页/下一页，不触发 page-size 变更，
// 也不需要同步 jumpVal（无跳页输入框），但仍兜底同步以保证一致性。
watch(() => props.page, (v) => { jumpVal.value = v; });

const maxPage = computed(() => Math.max(1, Math.ceil(props.total / props.pageSize)));

function onPage(p: number) {
  jumpVal.value = p;
  emit("update:page", p);
}
function onSize(s: number) {
  emit("update:page-size", s);
}
function jump() {
  const p = Math.min(Math.max(1, jumpVal.value || 1), maxPage.value);
  onPage(p);
}
</script>

<style scoped>
.pager {
  display: flex;
  align-items: center;
  gap: 16px;
  justify-content: flex-end;
  flex: 0 0 auto;
  padding-top: 12px;
  flex-wrap: wrap;
}
.pager-total {
  color: var(--text-sub);
  font-size: 13px;
}
.pager-jump {
  display: flex;
  align-items: center;
  gap: 6px;
}
/* 移动端：simple 模式，单行紧凑居中，不换行不溢出 */
.pager-mobile {
  justify-content: center;
  gap: 0;
  width: 100%;
  padding-top: 10px;
}
.pager-mobile :deep(.n-pagination) {
  font-size: 13px;
}
</style>
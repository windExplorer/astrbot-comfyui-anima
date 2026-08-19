<template>
  <div class="pager">
    <span class="pager-total">共 {{ total }} 条</span>
    <n-pagination
      :page="page"
      :page-size="pageSize"
      :item-count="total"
      :page-sizes="[10, 20, 24, 40, 60, 100]"
      show-size-picker
      @update:page="onPage"
      @update:page-size="onSize"
    />
    <div class="pager-jump">
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

const props = defineProps<{
  page: number;
  pageSize: number;
  total: number;
}>();

const emit = defineEmits<{
  (e: "update:page", p: number): void;
  (e: "update:page-size", s: number): void;
}>();

const jumpVal = ref(props.page);

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
/* 移动端：分页器精简，避免一行放不下。隐藏每页数量选择器与跳页，
   仅保留页码（n-pagination 自身会折叠页码显示省略号）；整体居中并允许横向滚动兜底 */
@media (max-width: 768px) {
  .pager {
    justify-content: center;
    gap: 10px;
    width: 100%;
    overflow-x: auto;
    -webkit-overflow-scrolling: touch;
  }
  .pager :deep(.n-pagination-sizer) { display: none !important; }
  .pager-jump { display: none !important; }
  .pager :deep(.n-pagination) { overflow-x: auto; }
}
</style>

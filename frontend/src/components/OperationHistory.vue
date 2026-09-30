<script setup lang="ts">
import { onMounted, ref } from "vue";
import { NButton, NAlert, NEmpty, useMessage } from "naive-ui";
import { api, dateLabel, errorText, mutate, query } from "../api";
import type { Operation } from "../types";
const props = defineProps<{ kind: "person" | "place" }>();
const emit = defineEmits<{ changed: [] }>();
const rows = ref<Operation[]>([]),
  busy = ref(false),
  error = ref(""),
  cursor = ref<string | null>(null),
  more = ref(false);
const message = useMessage();
const names: Record<string, string> = {
  RENAME: "人物改名",
  RENAME_OR_MERGE: "人物命名",
  MERGE: "合并人物",
  SPLIT: "拆分人物",
  ASSIGN: "指定归类",
  UNASSIGN: "取消归类",
  RESTORE_AUTO: "恢复自动建议",
  BATCH: "批量整理",
  BATCH_ASSIGN: "批量指定地点",
  BATCH_UNASSIGN: "批量取消地点",
  BATCH_RESTORE_AUTO: "批量恢复地点建议",
  UNDO: "撤销整理",
  FACE_CORRECTION: "人脸纠错",
  FACE_CORRECTION_BATCH: "批量人脸纠错",
};
async function load(next = false) {
  busy.value = true;
  error.value = "";
  try {
    const r = await api<any>(
      `/${props.kind}-operations` +
        query({ limit: 20, cursor: next ? cursor.value : null }),
    );
    rows.value = next ? [...rows.value, ...r.operations] : r.operations;
    cursor.value = r.next_cursor;
    more.value = r.has_more;
  } catch (e) {
    error.value = errorText(e);
  } finally {
    busy.value = false;
  }
}
async function undo(row: Operation) {
  busy.value = true;
  error.value = "";
  try {
    await mutate(
      `/${props.kind}-operations/${encodeURIComponent(row.operation_id)}/undo`,
      { expected_operation_revision: row.operation_revision },
    );
    message.success("已撤销这次整理");
    emit("changed");
    await load();
  } catch (e) {
    error.value = errorText(e);
  } finally {
    busy.value = false;
  }
}
onMounted(() => load());
</script>
<template>
  <NAlert v-if="error" type="error"
    >{{ error }}
    <div class="alert-action">
      <NButton @click="load()">刷新记录</NButton>
    </div></NAlert
  >
  <div v-for="row in rows" :key="row.operation_id" class="history-row">
    <div>
      {{ names[row.operation_type] || "照片整理"
      }}<small
        >{{ dateLabel(row.created_at) }} ·
        {{
          ["UNDONE", "REVERSED"].includes(row.status) ? "已撤销" : "已保存"
        }}</small
      >
    </div>
    <NButton
      size="small"
      :disabled="row.status !== 'COMMITTED' || !!row.undo_of_operation_id"
      :loading="busy"
      @click="undo(row)"
      >撤销</NButton
    >
  </div>
  <NEmpty v-if="!rows.length && !busy && !error" description="还没有整理记录" />
  <NButton v-if="more" :loading="busy" @click="load(true)">更多记录</NButton>
</template>

<script setup lang="ts">
import { onMounted, ref } from "vue";
import { NButton, NAlert, NCheckbox, NEmpty, useMessage } from "naive-ui";
import { api, errorText, mutate, query } from "../api";
import type { Mapping } from "../types";
const emit = defineEmits<{ changed: [] }>();
const rows = ref<Mapping[]>([]),
  error = ref(""),
  busy = ref(false),
  cursor = ref<string | null>(null),
  more = ref(false);
const chosen = ref<Record<string, string[]>>({}),
  inherited = ref<Record<string, string[]>>({});
const message = useMessage();
async function load(next = false) {
  busy.value = true;
  error.value = "";
  try {
    const r = await api<any>(
      "/face-mappings" +
        query({ state: "open", limit: 20, cursor: next ? cursor.value : null }),
    );
    rows.value = next ? [...rows.value, ...r.groups] : r.groups;
    cursor.value = r.next_cursor;
    more.value = r.has_more;
  } catch (e) {
    error.value = errorText(e);
  } finally {
    busy.value = false;
  }
}
async function expand(row: Mapping) {
  try {
    Object.assign(
      row,
      await api<Mapping>(
        "/face-mappings/" + encodeURIComponent(row.old_face_id),
      ),
    );
  } catch (e) {
    error.value = errorText(e);
  }
}
function toggle(
  store: Record<string, string[]>,
  key: string,
  value: string,
  enabled: boolean,
) {
  store[key] = enabled
    ? [...(store[key] || []), value]
    : (store[key] || []).filter((v) => v !== value);
}
async function decide(row: Mapping, reject = false) {
  busy.value = true;
  error.value = "";
  try {
    await mutate("/face-mappings/decisions", {
      contract_version: "p3-face-redetection-contracts.v1",
      asset_id: row.asset_id,
      decisions: [
        {
          old_face_id: row.old_face_id,
          expected_review_revision: row.revision,
          decision: reject ? "reject" : "confirm",
          targets: reject
            ? []
            : (chosen.value[row.old_face_id] || []).map((id) => ({
                new_face_id: id,
                inherits_person: (
                  inherited.value[row.old_face_id] || []
                ).includes(id),
              })),
        },
      ],
    });
    message.success("已保存确认结果");
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
  <p class="muted">
    重新识别人脸后，请确认新旧人脸是否对应。确认前会保留已有的人工整理。
  </p>
  <NAlert v-if="error" type="error"
    >{{ error }}
    <div class="alert-action">
      <NButton @click="load()">刷新</NButton>
    </div></NAlert
  >
  <div v-for="row in rows" :key="row.old_face_id" class="surface mapping-card">
    <div class="row-actions">
      <img class="avatar" :src="row.thumbnail_url" alt="原来的人脸" /><span
        >原来的人脸</span
      ><NButton :loading="busy" @click="expand(row)">查看新的候选</NButton>
    </div>
    <div v-if="row.candidates" class="face-grid">
      <div
        v-for="candidate in row.candidates"
        :key="candidate.new_face_id"
        class="face-card"
      >
        <img :src="candidate.thumbnail_url" alt="新的候选人脸" /><NCheckbox
          :checked="chosen[row.old_face_id]?.includes(candidate.new_face_id)"
          @update:checked="
            (v) => toggle(chosen, row.old_face_id, candidate.new_face_id, v)
          "
          >是同一张脸</NCheckbox
        ><NCheckbox
          v-if="row.control_mode === 'MANUAL_ASSIGNED'"
          :disabled="!chosen[row.old_face_id]?.includes(candidate.new_face_id)"
          :checked="inherited[row.old_face_id]?.includes(candidate.new_face_id)"
          @update:checked="
            (v) => toggle(inherited, row.old_face_id, candidate.new_face_id, v)
          "
          >沿用原人物归属</NCheckbox
        >
      </div>
    </div>
    <div class="row-actions">
      <NButton
        type="primary"
        :disabled="!chosen[row.old_face_id]?.length"
        :loading="busy"
        @click="decide(row)"
        >确认所选</NButton
      ><NButton :loading="busy" @click="decide(row, true)">都不对应</NButton>
    </div>
  </div>
  <NEmpty
    v-if="!rows.length && !busy && !error"
    description="没有需要确认的新旧人脸"
  /><NButton v-if="more" @click="load(true)">加载更多</NButton>
</template>

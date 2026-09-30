<script setup lang="ts">
import { computed, onMounted, ref } from "vue";
import {
  NButton,
  NInput,
  NSelect,
  NCheckbox,
  NModal,
  NAlert,
  NEmpty,
  NTabs,
  NTab,
  useMessage,
} from "naive-ui";
import { ArrowLeft, UsersRound, History } from "@lucide/vue";
import { api, errorText, mutate, personLabel, query } from "../api";
import type { Asset, Face, Person } from "../types";
import EntitySelect from "../components/EntitySelect.vue";
import PhotoDetail from "../components/PhotoDetail.vue";
import OperationHistory from "../components/OperationHistory.vue";
import FaceMappings from "../components/FaceMappings.vue";
const people = ref<Person[]>([]),
  person = ref<Person | null>(null),
  faces = ref<Face[]>([]),
  kind = ref("all");
const selectedPeople = ref<number[]>([]),
  selectedFaces = ref<string[]>([]),
  target = ref<number | null>(null);
const busy = ref(false),
  saving = ref(false),
  error = ref(""),
  cursor = ref<string | null>(null),
  more = ref(false),
  history = ref(false),
  nameOpen = ref(false),
  name = ref(""),
  mergeOpen = ref(false),
  photo = ref<Asset | null>(null);
const message = useMessage();
let epoch = 0;
const faceMode = computed(() => !!person.value || kind.value === "manual");
const selected = computed(() =>
  faces.value.filter((f) => selectedFaces.value.includes(f.face_id)),
);
const sameName = computed(() =>
  people.value.find(
    (p) =>
      p.person_id !== person.value?.person_id &&
      p.name?.normalize("NFC").trim().replace(/\s+/g, " ") ===
        name.value.normalize("NFC").trim().replace(/\s+/g, " "),
  ),
);
function toggle<T>(list: T[], value: T, checked: boolean) {
  return checked ? [...list, value] : list.filter((v) => v !== value);
}
async function load(next = false) {
  const run = ++epoch;
  busy.value = true;
  error.value = "";
  if (!next) {
    cursor.value = null;
    selectedFaces.value = [];
    selectedPeople.value = [];
  }
  try {
    if (faceMode.value) {
      const r = await api<any>(
        "/faces" +
          query({
            person_id: person.value?.person_id,
            assignment_state:
              kind.value === "manual" ? "manual_unassigned" : null,
            limit: 60,
            cursor: next ? cursor.value : null,
          }),
      );
      if (run !== epoch) return;
      faces.value = next ? [...faces.value, ...r.faces] : r.faces;
      if (person.value && r.person_revision)
        person.value.revision = r.person_revision;
      cursor.value = r.next_cursor;
      more.value = r.has_more;
    } else {
      const r = await api<any>(
        "/persons" +
          query({
            kind: kind.value,
            limit: 60,
            cursor: next ? cursor.value : null,
          }),
      );
      if (run !== epoch) return;
      people.value = next ? [...people.value, ...r.persons] : r.persons;
      cursor.value = r.next_cursor;
      more.value = r.has_more;
    }
  } catch (e) {
    if (run === epoch) error.value = errorText(e);
  } finally {
    if (run === epoch) busy.value = false;
  }
}
function changeTab(value: string) {
  kind.value = value;
  person.value = null;
  cursor.value = null;
  if (value !== "mappings") load();
}
function openPerson(p: Person) {
  person.value = { ...p };
  selectedFaces.value = [];
  load();
}
async function write(action: () => Promise<unknown>, text: string) {
  saving.value = true;
  error.value = "";
  try {
    await action();
    message.success(text);
    nameOpen.value = false;
    mergeOpen.value = false;
    await load();
  } catch (e) {
    error.value = errorText(e);
  } finally {
    saving.value = false;
  }
}
async function rename() {
  if (!person.value) return;
  const p = person.value;
  await write(
    () =>
      mutate(
        `/persons/${p.person_id}/name`,
        { name: name.value, expected_revision: p.revision },
        "PUT",
      ),
    "名字已保存，可在最近整理中撤销",
  );
  if (!error.value) {
    person.value = null;
    await load();
  }
}
async function merge() {
  if (!target.value || saving.value) return;
  saving.value = true;
  // A fresh target is fetched for the explicitly selected destination; sources retain the revisions the user saw.
  try {
    const targetPerson = await api<Person>(`/persons/${target.value}`);
    await write(
      () =>
        mutate("/persons/merge", {
          target_person_id: targetPerson.person_id,
          target_expected_revision: targetPerson.revision,
          sources: people.value
            .filter(
              (p) =>
                selectedPeople.value.includes(p.person_id) &&
                p.person_id !== targetPerson.person_id,
            )
            .map((p) => ({
              person_id: p.person_id,
              expected_revision: p.revision,
            })),
        }),
      "人物已合并，可在最近整理中撤销",
    );
  } catch (e) {
    error.value = errorText(e);
  } finally {
    saving.value = false;
  }
}
function correct(action: string) {
  const items = selected.value.map((f) => ({
    face_id: f.face_id,
    action,
    expected_revision: f.control_revision,
    ...(action === "assign" ? { target_person_id: target.value } : {}),
  }));
  write(
    () =>
      mutate("/face-assignments/batch", {
        contract_version: "p3-face-correction-contracts.v1",
        items,
      }),
    "已更新所选人脸，其他人物不受影响",
  );
}
function split() {
  if (!person.value) return;
  const p = person.value;
  write(
    () =>
      mutate(`/persons/${p.person_id}/split`, {
        contract_version: "p3-person-split-contracts.v1",
        source_expected_revision: p.revision,
        faces: selected.value.map((f) => ({
          face_id: f.face_id,
          expected_control_revision: f.control_revision,
          expected_assignment_revision: f.assignment_revision,
        })),
      }),
    "所选人脸已拆成一个新人物",
  );
}
onMounted(() => load());
</script>
<template>
  <header class="page-header">
    <div>
      <p class="eyebrow">FAMILIAR FACES</p>
      <h1>{{ person ? personLabel(person) : "人物" }}</h1>
      <p class="subtitle">给熟悉的面孔一个名字。</p>
    </div>
    <NButton @click="history = true"
      ><template #icon><History :size="17" /></template>最近整理</NButton
    >
  </header>
  <NAlert v-if="error" type="error" class="page-notice"
    >{{ error }}
    <div class="alert-action">
      <NButton @click="load()">刷新列表</NButton>
    </div></NAlert
  >
  <template v-if="person"
    ><div class="row-actions">
      <NButton
        quaternary
        @click="
          person = null;
          load();
        "
        ><template #icon><ArrowLeft :size="18" /></template>全部人物</NButton
      ><NButton
        @click="
          name = person.name || '';
          nameOpen = true;
        "
        >修改名字</NButton
      ><RouterLink :to="{ path: '/', query: { person: person.person_id } }"
        ><NButton type="primary">查看这个人的照片</NButton></RouterLink
      >
    </div></template
  >
  <NTabs v-else :value="kind" type="line" @update:value="changeTab"
    ><NTab name="all">全部人物</NTab><NTab name="named">已命名</NTab
    ><NTab name="unnamed">未命名</NTab><NTab name="manual">待手动归类</NTab
    ><NTab name="mappings">重新识别待确认</NTab></NTabs
  >
  <FaceMappings v-if="kind === 'mappings'" />
  <template v-else-if="!faceMode">
    <div v-if="selectedPeople.length" class="selection-bar">
      <span>已选 {{ selectedPeople.length }} 个人物</span
      ><NButton @click="mergeOpen = true">合并到同一个人</NButton
      ><NButton quaternary @click="selectedPeople = []">取消选择</NButton>
    </div>
    <div class="people-grid">
      <article v-for="p in people" :key="p.person_id" class="person-card">
        <NCheckbox
          class="person-check"
          :checked="selectedPeople.includes(p.person_id)"
          :aria-label="`选择 ${personLabel(p)}`"
          @update:checked="
            (v) => (selectedPeople = toggle(selectedPeople, p.person_id, v))
          "
        /><button @click="openPerson(p)">
          <img
            v-if="p.thumbnail_url"
            class="person-avatar"
            :src="p.thumbnail_url"
            :alt="personLabel(p)"
          /><span v-else class="person-avatar avatar-placeholder"
            ><UsersRound :size="38" /></span
          ><strong>{{ personLabel(p) }}</strong
          ><small>{{ p.asset_count }} 张照片</small>
        </button>
      </article>
    </div>
    <NEmpty
      v-if="!people.length && !busy && !error"
      class="empty-state"
      description="还没有人物分组。照片完成人脸分析后会出现在这里。"
    />
  </template>
  <template v-else>
    <p class="muted">
      勾选要整理的人脸。每次操作只影响所选人脸，不会改动合照里的其他人。
    </p>
    <div v-if="selectedFaces.length" class="selection-bar">
      <span>已选 {{ selectedFaces.length }} 张人脸</span
      ><EntitySelect v-model="target" kind="persons" /><NButton
        type="primary"
        :disabled="!target || selectedFaces.length > 500"
        :loading="saving"
        @click="correct('assign')"
        >指定人物</NButton
      ><NButton
        :disabled="selectedFaces.length > 500"
        :loading="saving"
        @click="correct('unassign')"
        >取消归类</NButton
      ><NButton
        :disabled="selectedFaces.length > 500"
        :loading="saving"
        @click="correct('restore_auto')"
        >恢复自动</NButton
      ><NButton
        v-if="person"
        :disabled="selectedFaces.length > 500"
        :loading="saving"
        @click="split"
        >拆成新人物</NButton
      ><NButton quaternary @click="selectedFaces = []">取消选择</NButton>
    </div>
    <div class="face-grid">
      <article v-for="face in faces" :key="face.face_id" class="face-card">
        <button @click="photo = { asset_id: face.asset_id }">
          <img
            :src="face.thumbnail_url"
            alt="点击查看这张人脸所在的照片"
          /></button
        ><NCheckbox
          :checked="selectedFaces.includes(face.face_id)"
          @update:checked="
            (v) => (selectedFaces = toggle(selectedFaces, face.face_id, v))
          "
          >选择这张脸</NCheckbox
        ><small>{{
          face.mode === "MANUAL_ASSIGNED"
            ? "你已确认"
            : face.mode === "MANUAL_UNASSIGNED"
              ? "待手动归类"
              : "自动归类"
        }}</small>
      </article>
    </div>
    <NEmpty
      v-if="!faces.length && !busy && !error"
      class="empty-state"
      description="这里还没有人脸"
    />
  </template>
  <div class="load-more">
    <NButton
      v-if="more && kind !== 'mappings'"
      :loading="busy"
      @click="load(true)"
      >加载更多</NButton
    ><span v-else-if="busy">正在载入…</span>
  </div>
  <NModal
    v-model:show="nameOpen"
    preset="card"
    title="给人物命名"
    class="small-modal"
    ><NInput
      v-model:value="name"
      placeholder="例如：妈妈"
      :maxlength="200"
      @keyup.enter="rename"
    /><NAlert class="page-notice" type="info">{{
      sameName
        ? `保存后将合并到已有的「${sameName.name}」。`
        : "如果已有同名人物，保存后会合并。不同的人请使用不同名字。"
    }}</NAlert
    ><NAlert v-if="error" type="error">{{ error }}</NAlert
    ><template #footer
      ><NButton
        type="primary"
        :disabled="!name.trim()"
        :loading="saving"
        @click="rename"
        >保存名字</NButton
      ></template
    ></NModal
  >
  <NModal
    v-model:show="mergeOpen"
    preset="card"
    title="合并人物"
    class="small-modal"
    ><p>将所选分组归到同一个人物，已有照片和人工整理会保留。</p>
    <EntitySelect v-model="target" kind="persons" /><NAlert
      v-if="error"
      type="error"
      >{{ error }}</NAlert
    ><template #footer
      ><NButton
        type="primary"
        :disabled="!target"
        :loading="saving"
        @click="merge"
        >合并到所选人物</NButton
      ></template
    ></NModal
  >
  <NModal
    v-model:show="history"
    preset="card"
    title="最近的人物整理"
    class="small-modal"
    ><OperationHistory v-if="history" kind="person" @changed="load()"
  /></NModal>
  <PhotoDetail :photo="photo" @close="photo = null" />
</template>

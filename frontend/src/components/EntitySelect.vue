<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from "vue";
import { NSelect, NButton } from "naive-ui";
import { api, errorText, personLabel, placePaths, query } from "../api";
import type { Person, Place } from "../types";
const props = defineProps<{
  kind: "persons" | "places";
  multiple?: boolean;
  modelValue: number | number[] | null;
  placeholder?: string;
  disabled?: boolean;
  allowedPlaceKinds?: string[];
}>();
const emit = defineEmits<{
  "update:modelValue": [value: any];
  labels: [value: { value: number; label: string }[]];
}>();
const rows = ref<(Person | Place)[]>([]),
  cursor = ref<string | null>(null),
  more = ref(false),
  loading = ref(false),
  error = ref("");
const placeNames = ref<Record<number, string>>({});
const keyword = ref("");
let queuedReset = false;
let disposed = false;
let timer: ReturnType<typeof setTimeout> | undefined;
const selectedIds = computed(() =>
  Array.isArray(props.modelValue)
    ? props.modelValue
    : props.modelValue
      ? [props.modelValue]
      : [],
);
const options = computed(() =>
  rows.value.map((row) =>
    "person_id" in row
      ? { label: personLabel(row), value: row.person_id }
      : {
          label: `${placeNames.value[row.place_id] || row.canonical_name} · #${row.place_id}`,
          disabled: props.allowedPlaceKinds
            ? !props.allowedPlaceKinds.includes(row.kind)
            : false,
          value: row.place_id,
        },
  ),
);
watch(
  [options, selectedIds],
  () =>
    emit(
      "labels",
      options.value.filter((o) => selectedIds.value.includes(o.value)),
    ),
  { immediate: true },
);
async function load(reset = false) {
  if (disposed) return;
  if (loading.value) {
    queuedReset ||= reset;
    return;
  }
  loading.value = true;
  error.value = "";
  try {
    const response = await api<any>(
      "/" +
        props.kind +
        query({
          limit: 50,
          cursor: reset ? null : cursor.value,
          lifecycle: props.kind === "places" ? "ACTIVE" : null,
          name: props.kind === "places" ? keyword.value : null,
        }),
    );
    const items = response[props.kind] as (Person | Place)[];
    const kept = reset
      ? rows.value.filter((r) =>
          selectedIds.value.includes(
            "person_id" in r ? r.person_id : r.place_id,
          ),
        )
      : rows.value;
    rows.value = [
      ...new Map(
        [...kept, ...items].map((r) => [
          "person_id" in r ? r.person_id : r.place_id,
          r,
        ]),
      ).values(),
    ];
    cursor.value = response.next_cursor;
    more.value = response.has_more;
    // Resolve only the ancestors of the bounded visible page; never download the whole directory.
    if (props.kind === "places") {
      Object.assign(placeNames.value, await placePaths(items as Place[]));
    }
  } catch (e) {
    error.value = errorText(e);
  } finally {
    loading.value = false;
    if (queuedReset && !disposed) {
      queuedReset = false;
      void load(true);
    }
  }
}
onMounted(() => load(true));
function search(value: string) {
  if (props.kind !== "places") return;
  keyword.value = value;
  clearTimeout(timer);
  timer = setTimeout(() => load(true), 250);
}
onUnmounted(() => {
  disposed = true;
  clearTimeout(timer);
});
watch(
  () => props.modelValue,
  async (value) => {
    for (const id of Array.isArray(value) ? value : value ? [value] : []) {
      if (
        rows.value.some(
          (r) => ("person_id" in r ? r.person_id : r.place_id) === id,
        )
      )
        continue;
      try {
        const row = await api<Person | Place>(`/${props.kind}/${id}`);
        if (disposed) return;
        rows.value.push(row);
        if ("place_id" in row)
          Object.assign(placeNames.value, await placePaths([row]));
      } catch (e) {
        error.value = errorText(e);
      }
    }
  },
  { immediate: true, deep: true },
);
</script>
<template>
  <div class="entity-select">
    <NSelect
      :value="modelValue"
      :multiple="multiple"
      :options="options"
      :loading="loading"
      :disabled="disabled"
      :remote="kind === 'places'"
      filterable
      clearable
      :placeholder="
        placeholder || (kind === 'persons' ? '选择人物' : '选择地点')
      "
      @search="search"
      @update:value="(v) => emit('update:modelValue', v)"
    />
    <NButton v-if="more" text size="tiny" :loading="loading" @click="load()"
      >加载更多{{ kind === "persons" ? "人物" : "地点" }}</NButton
    >
    <span v-if="error" class="inline-error"
      >{{ error }}
      <NButton text size="tiny" @click="load(true)">重试</NButton></span
    >
  </div>
</template>

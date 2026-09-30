<script setup lang="ts">
import { computed, onMounted, onUnmounted, reactive, ref, watch } from "vue";
import { useRoute } from "vue-router";
import {
  NButton,
  NCheckbox,
  NInput,
  NSelect,
  NDatePicker,
  NTag,
  NAlert,
  NEmpty,
  NCollapse,
  NCollapseItem,
} from "naive-ui";
import { Search, SlidersHorizontal, ImagePlus, X } from "@lucide/vue";
import { api, dateBounds, day, errorText, query } from "../api";
import type { Asset, Filters, SearchResult, Source } from "../types";
import PhotoGrid from "../components/PhotoGrid.vue";
import PhotoDetail from "../components/PhotoDetail.vue";
import EntitySelect from "../components/EntitySelect.vue";
const route = useRoute();
const expandedFilters = ref<string[]>([]);
const text = ref(""),
  photos = ref<Asset[]>([]),
  sources = ref<Source[]>([]),
  busy = ref(false),
  error = ref(""),
  note = ref("");
const filters = reactive<Filters>({
  source_id: null,
  person_ids: [],
  person_match: "any",
  place_ids: [],
  include_descendants: true,
  place_state: "any",
  availability: "any",
  dates: null,
});
const file = ref<File | null>(null),
  imageUrl = ref(""),
  picker = ref<HTMLInputElement | null>(null),
  selected = ref<Asset | null>(null);
const cursor = ref<string | null>(null),
  session = ref<string | null>(null),
  more = ref(false),
  total = ref<number | null>(null),
  applied = ref("");
const personLabels = ref<{ value: number; label: string }[]>([]),
  placeLabels = ref<{ value: number; label: string }[]>([]);
let epoch = 0;
let request: Record<string, unknown> = {};
const sourceOptions = computed(() =>
  sources.value.map((s) => ({ label: s.display_name, value: s.source_id })),
);
const signature = computed(() =>
  JSON.stringify([
    text.value,
    filters,
    file.value?.name,
    file.value?.lastModified,
  ]),
);
const dirty = computed(() => applied.value !== signature.value);
const filterCount = computed(
  () =>
    filters.person_ids.length +
    filters.place_ids.length +
    Number(!!filters.dates) +
    Number(!!filters.source_id) +
    Number(filters.availability !== "any") +
    Number(filters.place_state !== "any"),
);
const imageBlocked = computed(
  () => !!file.value && (filters.person_ids.length > 0 || !!filters.dates),
);
const useSearch = () =>
  !!text.value.trim() || !!file.value || filters.person_ids.length > 0;
function notices(r: SearchResult) {
  const notes: string[] = [];
  if (r.state !== "OK") notes.push("部分搜索结果尚未就绪，可以稍后重新搜索。");
  if ((r.pending_semantic?.count ?? 0) > 0)
    notes.push("还有照片正在建立搜索索引，当前结果可能不完整。");
  if (r.search_projection?.state === "LAGGING")
    notes.push("图片文字正在更新。");
  if (r.taken_time && r.taken_time.state !== "READY")
    notes.push(
      "部分照片缺少拍摄时间或尚未完成提取，日期筛选不会包含这些照片。",
    );
  if (r.candidates_truncated)
    notes.push("已显示最相关的一部分照片，可增加筛选条件缩小范围。");
  note.value = notes.join(" ");
}
async function search(next = false) {
  if (imageBlocked.value) return;
  const run = ++epoch;
  busy.value = true;
  error.value = "";
  note.value = "";
  if (!next) {
    photos.value = [];
    cursor.value = null;
    session.value = null;
    more.value = false;
    total.value = null;
    applied.value = signature.value;
    request = {
      source_id: filters.source_id,
      person_ids: [...filters.person_ids],
      person_match: filters.person_match,
      place_ids: [...filters.place_ids],
      place_state: filters.place_ids.length ? "any" : filters.place_state,
      availability: filters.availability,
      include_descendants: filters.include_descendants,
      ...dateBounds(filters.dates),
      text: text.value.trim() || null,
      top_k: 5000,
      page_size: 48,
    };
  }
  const snapshot = { ...request };
  try {
    let items: Asset[], result: any;
    if (useSearch()) {
      const payload = {
        ...snapshot,
        request_id: crypto.randomUUID(),
        ...(next
          ? { cursor: cursor.value, search_session_id: session.value }
          : {}),
      };
      if (file.value) {
        const form = new FormData();
        form.append("image", file.value);
        for (const [k, v] of Object.entries(payload)) {
          if (
            [
              "person_ids",
              "person_match",
              "capture_from",
              "capture_to_exclusive",
              "text",
            ].includes(k) ||
            v === null ||
            v === undefined
          )
            continue;
          form.append(k, Array.isArray(v) ? v.join(",") : String(v));
        }
        result = await api<SearchResult>("/search/image", form);
      } else result = await api<SearchResult>("/search", payload);
      items = result.hits;
    } else {
      const { person_ids, person_match, top_k, page_size, ...rest } = snapshot;
      result = await api<any>(
        "/assets" +
          query({
            ...rest,
            order: "time_desc",
            limit: 48,
            cursor: next ? cursor.value : null,
          }),
      );
      items = result.assets;
    }
    if (run !== epoch) return;
    photos.value = next ? [...photos.value, ...items] : items;
    cursor.value = result.next_cursor;
    more.value = result.has_more;
    session.value = result.search_session_id || null;
    total.value = result.total_candidates ?? null;
    if (result.hits) notices(result);
    else if (result.taken_time && result.taken_time.state !== "READY")
      note.value = "部分照片没有拍摄时间或尚未提取，日期筛选结果可能不完整。";
  } catch (e) {
    if (run === epoch) {
      error.value = errorText(e);
      more.value = false;
    }
  } finally {
    if (run === epoch) busy.value = false;
  }
}
function upload(event: Event) {
  const next = (event.target as HTMLInputElement).files?.[0];
  if (!next) return;
  if (imageUrl.value) URL.revokeObjectURL(imageUrl.value);
  file.value = next;
  imageUrl.value = URL.createObjectURL(next);
  text.value = "";
  search();
}
function clearImage() {
  file.value = null;
  URL.revokeObjectURL(imageUrl.value);
  imageUrl.value = "";
  if (picker.value) picker.value.value = "";
  search();
}
function clearFilters() {
  Object.assign(filters, {
    source_id: null,
    person_ids: [],
    person_match: "any",
    place_ids: [],
    include_descendants: true,
    place_state: "any",
    availability: "any",
    dates: null,
  });
  search();
}
function routeFilters() {
  const person = Number(route.query.person),
    place = Number(route.query.place);
  filters.person_ids =
    Number.isSafeInteger(person) && person > 0 ? [person] : [];
  filters.place_ids = Number.isSafeInteger(place) && place > 0 ? [place] : [];
  filters.place_state =
    route.query.place_state === "suggested" ? "suggested" : "any";
  if (filters.person_ids.length || filters.place_ids.length)
    expandedFilters.value = ["filters"];
  search();
}
watch(() => route.query, routeFilters);
onMounted(() => {
  api<{ sources: Source[] }>("/sources")
    .then((r) => {
      sources.value = r.sources;
    })
    .catch((e) => {
      error.value = errorText(e);
    });
  routeFilters();
});
onUnmounted(() => {
  epoch++;
  if (imageUrl.value) URL.revokeObjectURL(imageUrl.value);
});
</script>
<template>
  <header class="page-header">
    <div>
      <p class="eyebrow">YOUR PHOTO LIBRARY</p>
      <h1>照片</h1>
      <p class="subtitle">记得画面，就能开始寻找。</p>
    </div>
    <NButton @click="picker?.click()"
      ><template #icon><ImagePlus :size="18" /></template>以图搜图</NButton
    >
  </header>
  <form class="search-bar" @submit.prevent="search()">
    <NInput
      v-model:value="text"
      size="large"
      clearable
      :disabled="!!file"
      placeholder="试试「海边的日落」或「一只趴着的猫」"
      :maxlength="256"
      aria-label="搜索照片"
      ><template #prefix><Search :size="20" /></template></NInput
    ><NButton
      type="primary"
      size="large"
      attr-type="submit"
      :loading="busy"
      :disabled="imageBlocked"
      >搜索</NButton
    >
  </form>
  <input
    ref="picker"
    type="file"
    accept="image/jpeg,image/png,image/webp,image/bmp,image/gif,image/heic,image/heif,.heic,.heif"
    hidden
    @change="upload"
  />
  <div v-if="file" class="query-image">
    <img :src="imageUrl" alt="用于搜索的图片" /><span
      >{{ file.name }}<small>按画面相似度查找</small></span
    ><NButton quaternary circle aria-label="取消以图搜图" @click="clearImage"
      ><X :size="18"
    /></NButton>
  </div>
  <NCollapse v-model:expanded-names="expandedFilters" class="filter-panel"
    ><NCollapseItem name="filters" display-directive="show"
      ><template #header
        ><span class="icon-label"
          ><SlidersHorizontal :size="16" />筛选照片
          <NTag v-if="filterCount" size="small" round>{{
            filterCount
          }}</NTag></span
        ></template
      >
      <div class="filter-grid">
        <label
          >人物<EntitySelect
            v-model="filters.person_ids"
            kind="persons"
            multiple
            @labels="personLabels = $event"
        /></label>
        <label
          >合照条件<NSelect
            v-model:value="filters.person_match"
            :options="[
              { label: '包含任意一人', value: 'any' },
              { label: '包含所有人', value: 'all' },
            ]"
        /></label>
        <label
          >地点<EntitySelect
            v-model="filters.place_ids"
            kind="places"
            multiple
            @labels="placeLabels = $event"
        /></label>
        <label>
          地点范围<NCheckbox
            v-model:checked="filters.include_descendants"
            :disabled="!filters.place_ids.length"
            >包含下级地点</NCheckbox
          >
          <small class="muted">例如选择杭州时，也包含归到西湖的照片。</small>
        </label>
        <label
          >拍摄日期<NDatePicker
            v-model:value="filters.dates"
            type="daterange"
            clearable
        /></label>
        <label
          >照片来源<NSelect
            v-model:value="filters.source_id"
            :options="sourceOptions"
            clearable
            placeholder="所有来源"
        /></label>
        <label
          >原图状态<NSelect
            v-model:value="filters.availability"
            :options="[
              { label: '全部照片', value: 'any' },
              { label: '原图可用', value: 'present' },
              { label: '原图离线', value: 'missing' },
            ]"
        /></label>
        <label
          >地点整理状态<NSelect
            v-model:value="filters.place_state"
            :disabled="!!filters.place_ids.length"
            :options="[
              { label: '不限', value: 'any' },
              { label: '已有地点', value: 'assigned' },
              { label: '未归类', value: 'unassigned' },
              { label: '待确认建议', value: 'suggested' },
              { label: '已取消归类', value: 'manual_unassigned' },
            ]"
        /></label>
      </div>
      <div class="row-actions">
        <NButton
          type="primary"
          :disabled="imageBlocked"
          :loading="busy"
          @click="search()"
          >应用筛选</NButton
        ><NButton quaternary @click="clearFilters">清空筛选</NButton
        ><span class="muted"
          >多个地点匹配任一；人物、日期与地点条件需同时满足。</span
        >
      </div>
    </NCollapseItem></NCollapse
  >
  <div v-if="filterCount" class="filter-chips">
    <NTag
      v-for="person in personLabels"
      :key="`p${person.value}`"
      closable
      round
      @close="
        filters.person_ids = filters.person_ids.filter(
          (id) => id !== person.value,
        );
        search();
      "
      >{{ person.label }}</NTag
    ><NTag
      v-for="place in placeLabels"
      :key="`l${place.value}`"
      closable
      round
      @close="
        filters.place_ids = filters.place_ids.filter(
          (id) => id !== place.value,
        );
        search();
      "
      >{{ place.label }} ·
      {{ filters.include_descendants ? "含下级" : "仅当前" }}</NTag
    ><NTag
      v-if="filters.dates"
      closable
      round
      @close="
        filters.dates = null;
        search();
      "
      >{{ day(filters.dates[0]) }} 至 {{ day(filters.dates[1]) }}</NTag
    ><NTag
      v-if="filters.source_id"
      closable
      round
      @close="
        filters.source_id = null;
        search();
      "
      >{{
        sources.find((s) => s.source_id === filters.source_id)?.display_name ||
        "已选来源"
      }}</NTag
    ><NButton text size="small" @click="clearFilters">清空条件</NButton>
  </div>
  <NAlert v-if="imageBlocked" type="warning" class="page-notice"
    >以图搜图暂不支持人物和拍摄日期条件。请清除这两项，或取消上传图片。</NAlert
  >
  <div class="result-heading">
    <span
      >{{ text || file ? "搜索结果" : "全部照片 · 按加入时间"
      }}<span v-if="total !== null" class="muted">
        · {{ total.toLocaleString() }} 张候选</span
      ></span
    ><NButton v-if="dirty && !busy" text type="primary" @click="search()"
      >条件已改变，点击更新结果</NButton
    >
  </div>
  <NAlert v-if="error" type="error" class="page-notice"
    >{{ error }}
    <div class="alert-action">
      <NButton size="small" @click="search()">重新加载</NButton>
    </div></NAlert
  >
  <NAlert v-if="note" type="info" class="page-notice">{{ note }}</NAlert>
  <PhotoGrid :photos="photos" :loading="busy" @open="selected = $event" />
  <NEmpty
    v-if="!busy && !error && !photos.length"
    class="empty-state"
    :description="
      text || file || filterCount ? '没有找到符合条件的照片' : '相册还是空的'
    "
  >
    <template #extra
      ><NButton v-if="text || file || filterCount" @click="clearFilters"
        >放宽筛选</NButton
      ><RouterLink v-else to="/library"
        ><NButton type="primary">添加照片文件夹</NButton></RouterLink
      ></template
    >
  </NEmpty>
  <div class="load-more">
    <NButton v-if="more" :loading="busy" :disabled="dirty" @click="search(true)"
      >加载更多照片</NButton
    >
  </div>
  <PhotoDetail :photo="selected" @close="selected = null" @changed="search()" />
</template>

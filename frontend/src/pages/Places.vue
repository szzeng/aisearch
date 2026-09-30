<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from "vue";
import {
  NButton,
  NCheckbox,
  NInput,
  NSelect,
  NModal,
  NAlert,
  NEmpty,
  NTabs,
  NTab,
  useMessage,
} from "naive-ui";
import { MapPin, Plus, History, ArrowLeft } from "@lucide/vue";
import { api, errorText, mutate, query } from "../api";
import type { Asset, Place, PlaceDetail } from "../types";
import EntitySelect from "../components/EntitySelect.vue";
import PhotoGrid from "../components/PhotoGrid.vue";
import PhotoDetail from "../components/PhotoDetail.vue";
import OperationHistory from "../components/OperationHistory.vue";
const places = ref<Place[]>([]),
  current = ref<Place | null>(null),
  tab = ref("directory"),
  photos = ref<Asset[]>([]),
  selected = ref<number[]>([]),
  revisions = ref<Record<number, number>>({});
const busy = ref(false),
  saving = ref(false),
  error = ref(""),
  cursor = ref<string | null>(null),
  more = ref(false),
  nameFilter = ref(""),
  photo = ref<Asset | null>(null),
  target = ref<number | null>(null);
const editor = ref(false),
  editing = ref<Place | null>(null),
  name = ref(""),
  aliases = ref(""),
  kind = ref("custom"),
  parent = ref<number | null>(null),
  history = ref(false);
const includeDescendants = ref(true);
const showEmpty = ref(false), scenic = ref(false);
const childrenCursor = ref<string | null>(null), childrenMore = ref(false);
let disposed = false;
const children = ref<Place[]>([]), areaLinks = ref<Place[]>([]);
const breadcrumbs = ref<{place_id: number; canonical_name: string}[]>([]);
const boundaryInfo = ref<{available: boolean; packages: {layer: string; namespace: string; version: string; coverage: string; boundary_year: string; license: string; attribution?: string; source: string; pending: number}[]} | null>(null);
const packageInput = ref<HTMLInputElement | null>(null);
const showData = ref(false);
let refreshTimer: ReturnType<typeof setTimeout> | undefined;
async function loadBoundaryInfo() {
  const wasPending = boundaryInfo.value?.packages.some(p => p.pending > 0);
  boundaryInfo.value = await api("/places/boundaries");
  if (disposed) return;
  if (wasPending && !boundaryInfo.value?.packages.some(p => p.pending > 0)) await load();
  if (boundaryInfo.value?.packages.some(p => p.pending > 0))
    refreshTimer = setTimeout(() => { loadBoundaryInfo().catch(() => {}); }, 2500);
}
async function moreChildren() {
  if (!current.value || !childrenCursor.value) return;
  const run = epoch;
  try {
    const r = await api<any>("/places/browse" + query({parent: current.value.place_id, show_empty: showEmpty.value, limit: 48, cursor: childrenCursor.value}));
    if (run !== epoch) return;
    children.value.push(...r.places); childrenCursor.value = r.next_cursor; childrenMore.value = r.has_more;
  } catch (e) { error.value = errorText(e); }
}
async function installPackage(event: Event) {
  const input = event.target as HTMLInputElement;
  const file = input.files?.[0];
  if (!file) return;
  saving.value = true;
  try {
    if (file.size > 64 * 1024 * 1024) throw new Error("边界包不能超过 64 MB");
    const body = JSON.parse(await file.text());
    boundaryInfo.value = await api("/places/boundaries", body, "POST");
    message.success("边界包已启用，正在整理已有 GPS 照片");
    await load();
    await loadBoundaryInfo();
  } catch (e) { error.value = errorText(e); }
  finally { saving.value = false; input.value = ""; }
}
async function openPlace(place: Place | null) {
  current.value = place;
  nameFilter.value = "";
  await load();
}
async function openAncestor(id: number) { await openPlace(await api<Place>(`/places/${id}`)); }

const datasetState = ref<{ resolver_available: boolean; state: string } | null>(
  null,
);
const allowedParents = computed(() =>
  kind.value === "city"
    ? ["country", "province"]
    : kind.value === "attraction"
      ? ["country", "province", "city", "district"]
      : kind.value === "province" ? ["country"] : kind.value === "district" ? ["country", "province", "city"] : ["country", "province", "city", "district", "attraction", "custom"],
);
watch(
  kind,
  () => {
    if (!editing.value) parent.value = null;
  },
  { flush: "sync" },
);
const message = useMessage();
const paths = ref<Record<number, string>>({});
let epoch = 0;
const kinds = [
  { label: "自定义地点", value: "custom" },
  { label: "省 / 自治区", value: "province" },
  { label: "城市", value: "city" },
  { label: "区 / 县", value: "district" },
  { label: "景点", value: "attraction" },
];
const kindLabel = (value: string) =>
  ({
    country: "国家 / 地区",
    province: "省 / 自治区",
    city: "城市",
    district: "区 / 县",
    attraction: "景点",
    custom: "自定义",
  })[value] || "地点";
async function load(next = false) {
  const run = ++epoch;
  busy.value = true;
  error.value = "";
  if (!next) {
    selected.value = [];
    revisions.value = {};
    cursor.value = null;
  }
  try {
    if (tab.value === "directory" && (!next || !current.value)) {
      const r = await api<any>(
        "/places/browse" +
          query({
            limit: 48,
            name: nameFilter.value.trim(),
            parent: current.value?.place_id,
            show_empty: showEmpty.value,
            scenic: scenic.value && !current.value,
            cursor: next ? cursor.value : null,
          }),
      );
      if (run !== epoch) return;
      const names = Object.fromEntries(r.places.map((p: Place & {ancestors?: Place[]}) => [
        p.place_id, [...(p.ancestors || []).map(a => a.canonical_name), p.canonical_name].join(" / ")
      ]));
      if (run !== epoch) return;
      Object.assign(paths.value, names);
      if (current.value) { children.value = r.places; childrenCursor.value = r.next_cursor; childrenMore.value = r.has_more; }
      else places.value = next ? [...places.value, ...r.places] : r.places;
      breadcrumbs.value = r.ancestors || [];
      areaLinks.value = r.areas || [];
      more.value = r.has_more;
      cursor.value = r.next_cursor;
    }
    if (current.value || tab.value !== "directory") {
      const r = await api<any>(
        "/assets" +
          query({
            limit: 48,
            place_ids: current.value ? [current.value.place_id] : [],
            place_state: current.value ? "any" : tab.value,
            include_descendants: includeDescendants.value,
            order: "time_desc",
            cursor: next ? cursor.value : null,
          }),
      );
      if (run !== epoch) return;
      photos.value = next ? [...photos.value, ...r.assets] : r.assets;
      if (tab.value === "suggested") {
        const status = await api<{
          resolver_available: boolean;
          state: string;
        }>("/places/datasets");
        if (run !== epoch) return;
        datasetState.value = status;
      }
      more.value = r.has_more;
      cursor.value = r.next_cursor;
    }
  } catch (e) {
    if (run === epoch) error.value = errorText(e);
  } finally {
    if (run === epoch) busy.value = false;
  }
}
function changeTab(value: string) {
  tab.value = value;
  current.value = null;
  load();
}
function edit(place: Place | null) {
  editing.value = place;
  name.value = place?.canonical_name || "";
  aliases.value = place?.aliases.map((a) => a.text).join("，") || "";
  kind.value = place?.kind || "custom";
  parent.value = place
    ? place.parent_place_id
    : (current.value?.place_id ?? null);
  editor.value = true;
}
async function savePlace() {
  saving.value = true;
  error.value = "";
  try {
    const body = {
      canonical_name: name.value.trim(),
      aliases: aliases.value
        .split(/[,，\n]/)
        .map((v) => v.trim())
        .filter(Boolean)
        .map((text) => ({ text })),
    };
    const p = editing.value;
    const result = await api<Place>(
      p ? `/places/${p.place_id}` : "/places",
      p
        ? { ...body, expected_revision: p.revision }
        : { ...body, kind: kind.value, parent_place_id: parent.value },
      p ? "PATCH" : "POST",
    );
    if (current.value?.place_id === result.place_id) current.value = result;
    editor.value = false;
    message.success("地点已保存");
    await load();
  } catch (e) {
    error.value = errorText(e);
  } finally {
    saving.value = false;
  }
}
async function select(asset: Asset, checked: boolean) {
  const run = epoch;
  if (!checked) {
    selected.value = selected.value.filter((id) => id !== asset.asset_id);
    delete revisions.value[asset.asset_id];
    return;
  }
  selected.value.push(asset.asset_id);
  try {
    const r = await api<PlaceDetail>(`/assets/${asset.asset_id}/place`);
    if (run === epoch && selected.value.includes(asset.asset_id))
      revisions.value[asset.asset_id] = r.assignment?.revision ?? 0;
  } catch (e) {
    if (run === epoch) {
      error.value = errorText(e);
      selected.value = selected.value.filter((id) => id !== asset.asset_id);
    }
  }
}
async function assign(action: string) {
  saving.value = true;
  error.value = "";
  try {
    await mutate("/place-assignments/batch", {
      items: selected.value.map((id) => ({
        asset_id: id,
        expected_revision: revisions.value[id],
        action,
        ...(action === "assign" ? { target_place_id: target.value } : {}),
      })),
    });
    message.success("所选照片的地点已更新，可在最近整理中撤销");
    await load();
  } catch (e) {
    error.value = errorText(e);
  } finally {
    saving.value = false;
  }
}
onMounted(() => { load(); loadBoundaryInfo().catch(() => {}); });
onUnmounted(() => { disposed = true; if (refreshTimer) clearTimeout(refreshTimer); });
</script>
<template>
  <header class="page-header">
    <div>
      <p class="eyebrow">PLACES TO REMEMBER</p>
      <h1>{{ current?.canonical_name || "地点" }}</h1>
      <p v-if="current" class="muted">{{ paths[current.place_id] }}</p>
      <p class="subtitle">按拍摄地点逐级查看；一个地区包含它下级地点的照片。</p>
    </div>
    <div class="row-actions">
      <NButton @click="showData = !showData">离线地点数据</NButton>
      <NButton @click="history = true"
        ><template #icon><History :size="17" /></template>最近整理</NButton
      ><NButton type="primary" @click="edit(null)"
        ><template #icon><Plus :size="18" /></template
        >{{ current ? "新建下级地点" : "新建地点" }}</NButton
      >
    </div>
  </header>
  <p v-if="boundaryInfo?.packages.some(p => p.layer === 'administrative' && /未标明|历史/.test(p.boundary_year))" class="muted">
    当前行政边界来自历史公开数据，部分区县名称可能已调整；可在“离线地点数据”中查看来源或更新。
  </p>
  <NAlert v-if="error" type="error" class="page-notice"
    >{{ error }}
    <div class="alert-action">
      <NButton @click="load()">刷新列表</NButton>
    </div></NAlert
  >
  <div v-if="showData" class="place-data-panel">
    <p>GPS 坐标在本机按边界归类。行政地区与景区范围分别保存；人工指定优先。</p>
    <p v-if="!boundaryInfo?.available" class="muted">尚未安装行政边界包，当前可手动整理地点。</p>
    <p v-for="pack in boundaryInfo?.packages" :key="pack.namespace" class="muted">
      {{ pack.coverage }} · 区划年份 {{ pack.boundary_year }} · {{ pack.version }}<br />
      {{ pack.license }} · {{ pack.attribution }} · <a :href="pack.source" target="_blank" rel="noopener noreferrer">数据来源</a>
      <span v-if="pack.pending"> · {{ pack.pending }} 张待整理</span>
    </p>
    <input ref="packageInput" type="file" accept=".json,.geojson" hidden @change="installPackage" />
    <NButton :loading="saving" @click="packageInput?.click()">导入边界包</NButton>
  </div>
  <nav v-if="current" class="place-breadcrumbs" aria-label="地点层级">
    <button @click="openPlace(null)">全部地点</button>
    <template v-for="crumb in breadcrumbs" :key="crumb.place_id">
      <span>›</span><button @click="openAncestor(crumb.place_id)">{{ crumb.canonical_name }}</button>
    </template><span>›</span><strong>{{ current.canonical_name }}</strong>
  </nav>
  <div v-if="current" class="row-actions">
    <NButton
      quaternary
      @click="
        openPlace(null);
      "
      ><template #icon><ArrowLeft :size="17" /></template>返回地点</NButton
    ><NButton @click="edit(current)">编辑地点</NButton
    ><span class="muted">{{
      includeDescendants ? "包含下级地点的照片" : "仅直接归到当前地点的照片"
    }}</span>
  </div>
  <NTabs v-else :value="tab" type="line" @update:value="changeTab"
    ><NTab name="directory">全部地点</NTab
    ><NTab name="unassigned">未归类照片</NTab
    ><NTab name="suggested">待确认建议</NTab
    ><NTab name="manual_unassigned">已取消归类</NTab></NTabs
  >
  <template v-if="tab === 'directory'">
    <div v-if="!current" class="row-actions place-view-options">
      <NButton :type="!scenic ? 'primary' : 'default'" @click="scenic = false; load()">按地区</NButton>
      <NButton :type="scenic ? 'primary' : 'default'" @click="scenic = true; load()">景区与地点</NButton>
      <NCheckbox v-model:checked="showEmpty" @update:checked="load()">显示暂无照片的地点</NCheckbox>
    </div>
    <form v-if="!current" class="directory-search" @submit.prevent="load()">
      <NInput
        v-model:value="nameFilter"
        placeholder="搜索地点名称或别名"
        clearable
        aria-label="搜索地点"
      /><NButton attr-type="submit" :loading="busy">查找</NButton>
    </form>
    <h3 v-if="current && children.length">下级地点</h3>
    <div class="places-grid">
      <article v-for="place in (current ? children : places)" :key="place.place_id" class="place-card">
        <button
          @click="openPlace(place)"
        >
          <img v-if="place.cover_asset_id" class="place-cover" :src="`/api/v1/assets/${place.cover_asset_id}/thumbnail`" alt="" loading="lazy" />
          <span v-else class="place-icon"><MapPin :size="24" /></span>
          <strong>{{ nameFilter ? paths[place.place_id] : place.canonical_name }}</strong>
          <small>{{ kindLabel(place.kind) }} · {{ place.asset_count ?? 0 }} 张照片</small
          ><small v-if="place.aliases.length">{{
            place.aliases.map((a) => a.text).join(" / ")
          }}</small></button
        ><NButton quaternary size="small" @click="edit(place)">编辑</NButton>
      </article>
    </div>
    <NButton v-if="current && childrenMore" @click="moreChildren()">更多下级地点</NButton>
    <NEmpty
      v-if="!current && !places.length && !busy && !error"
      class="empty-state"
      description="还没有这个范围的照片，可显示全部地点或手动创建"
      ><template #extra
        ><NButton @click="edit(null)">创建一个地点</NButton></template
      ></NEmpty
    >
  </template>
  <template v-if="current || tab !== 'directory'">
    <div v-if="areaLinks.length" class="row-actions place-area-links">
      <span class="muted">这里的景区</span><NButton v-for="area in areaLinks" :key="area.place_id" size="small" @click="openPlace(area)">{{ area.canonical_name }}</NButton>
    </div>
    <h3 v-if="current">{{ includeDescendants ? '这个地区的全部照片' : '直接归到这里的照片' }}</h3>
    <NCheckbox
      v-if="current"
      v-model:checked="includeDescendants"
      @update:checked="load()"
      >包含下级地点</NCheckbox
    >
    <NAlert
      v-if="
        tab === 'suggested' && datasetState && !datasetState.resolver_available
      "
      type="info"
      class="page-notice"
    >
      {{
        datasetState.state === "unavailable"
          ? "尚未安装离线地名数据包"
          : "离线地名数据包暂不可用"
      }}，无法生成附近城市建议。仍可创建地点并手动整理照片。
    </NAlert>
    <p class="muted">
      {{
        tab === "suggested"
          ? "GPS 建议按城市点距离生成，不能证明行政区或景点归属。打开照片确认后才会归类。"
          : "选择照片后可以批量设置地点。取消归类不会删除照片。"
      }}
    </p>
    <div v-if="selected.length" class="selection-bar">
      <span>已选 {{ selected.length }} 张</span
      ><EntitySelect v-model="target" kind="places" /><NButton
        type="primary"
        :disabled="
          !target ||
          selected.some((id) => revisions[id] === undefined) ||
          selected.length > 500
        "
        :loading="saving"
        @click="assign('assign')"
        >指定地点</NButton
      ><NButton
        :disabled="
          selected.length > 500 ||
          selected.some((id) => revisions[id] === undefined)
        "
        :loading="saving"
        @click="assign('unassign')"
        >取消归类</NButton
      ><NButton
        :disabled="
          selected.length > 500 ||
          selected.some((id) => revisions[id] === undefined)
        "
        :loading="saving"
        @click="assign('restore_auto')"
        >恢复自动归类</NButton
      ><NButton
        quaternary
        @click="
          selected = [];
          revisions = {};
        "
        >取消选择</NButton
      >
    </div>
    <PhotoGrid
      :photos="photos"
      :loading="busy"
      selectable
      :selected="selected"
      @select="select"
      @open="photo = $event"
    /><NEmpty
      v-if="!photos.length && !busy && !error"
      class="empty-state"
      description="这里还没有照片"
    />
  </template>
  <div class="load-more">
    <NButton v-if="more" :loading="busy" @click="load(true)">加载更多</NButton>
  </div>
  <NModal
    v-model:show="editor"
    preset="card"
    :title="editing ? '编辑地点' : '新建地点'"
    class="small-modal"
    ><div class="form-stack">
      <label
        >地点名称<NInput
          v-model:value="name"
          placeholder="例如：家、杭州、西湖"
          :maxlength="200" /></label
      ><label v-if="!editing"
        >类型<NSelect v-model:value="kind" :options="kinds" /></label
      ><label v-if="!editing"
        >上级地点（可选）<EntitySelect
          v-model="parent"
          kind="places"
          :allowed-place-kinds="allowedParents"
        />
        <small class="muted">{{
          kind === "city"
            ? "城市可属于省或国家，也可暂时留空。"
            : kind === "attraction"
              ? "单一区域地点可选择父级。跨区域景区应导入独立的景区边界范围。"
              : "可选择已有地点作为上级，之后按上级筛选也能找到这里的照片。"
        }}</small></label
      ><label
        >别名（可选）<NInput
          v-model:value="aliases"
          placeholder="多个别名用逗号分隔" /></label
      ><NAlert v-if="error" type="error">{{ error }}</NAlert>
    </div>
    <template #footer
      ><NButton
        type="primary"
        :loading="saving"
        :disabled="!name.trim()"
        @click="savePlace"
        >保存地点</NButton
      ></template
    ></NModal
  >
  <NModal
    v-model:show="history"
    preset="card"
    title="最近的地点整理"
    class="small-modal"
    ><OperationHistory v-if="history" kind="place" @changed="load()"
  /></NModal>
  <PhotoDetail :photo="photo" @close="photo = null" @changed="load()" />
</template>

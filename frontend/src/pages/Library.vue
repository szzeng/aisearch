<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from "vue";
import {
  NButton,
  NInput,
  NModal,
  NAlert,
  NEmpty,
  NProgress,
  NTag,
  NCollapse,
  NCollapseItem,
  useDialog,
  useMessage,
} from "naive-ui";
import {
  FolderPlus,
  RefreshCw,
  FolderOpen,
  CircleCheck,
  HardDrive,
  Pause,
  Play,
} from "@lucide/vue";
import { api, dateLabel, errorText } from "../api";
import type { Capabilities, Refresh, Source, Stage } from "../types";
const sources = ref<Source[]>([]),
  capabilities = ref<Capabilities | null>(null),
  refresh = ref<Refresh | null>(null),
  stats = ref<any>(null),
  jobs = ref<any[]>([]);
const busy = ref(false),
  saving = ref(false),
  error = ref(""),
  addOpen = ref(false),
  name = ref(""),
  path = ref(""),
  jobCursor = ref<string | null>(null),
  jobsMore = ref(false);
const dialog = useDialog(),
  message = useMessage();
let timer: ReturnType<typeof setTimeout> | undefined;
let disposed = false;
const stageLabels: Record<string, string> = {
  semantic: "画面搜索",
  thumbnail: "缩略图",
  thumbnails: "缩略图",
  taken_time: "拍摄时间",
  gps: "GPS 信息",
  geo: "GPS 信息",
  place: "地点建议",
  places: "地点建议",
  face: "人物识别",
  faces: "人物识别",
  ocr: "图片文字",
  scan: "扫描文件",
};
const stateLabels: Record<string, string> = {
  UNBUILT: "尚未建库",
  BUILDING: "正在建库",
  IDLE: "尚未开始",
  COMPLETE: "已完成",
  SEARCH_READY: "已可搜索",
  SEARCHABLE: "已可搜索",
  NOT_STARTED: "尚未开始",
  PENDING: "等待处理",
  QUEUED: "等待处理",
  RUNNING: "处理中",
  IN_PROGRESS: "处理中",
  SCANNING: "扫描中",
  ENRICHING: "补充人物和文字",
  FAILED: "处理失败",
  BLOCKED: "等待恢复",
  UNAVAILABLE: "暂不可用",
  NO_IMAGES: "没有图片",
  PARTIAL: "部分完成",
  ONLINE: "在线",
  OFFLINE: "离线",
  AVAILABLE: "可用",
  UNINDEXABLE: "部分图片无法建立索引",
  BACKLOG: "仍有照片待处理",
  NEVER_SCANNED: "尚未扫描",
};
const stateText = (value?: string) => stateLabels[value || ""] || "等待处理";
const paused = computed(
  () => capabilities.value?.scheduler?.state === "paused",
);
const healthLabels: Record<string, string> = {
  healthy: "正常",
  degraded: "部分能力不可用",
  unhealthy: "不可用",
  unknown: "待检测",
};
const readySources = computed(
  () => sources.value.filter((s) => s.progress?.search_ready).length,
);
function progress(stage: Stage) {
  const total = Number(stage.total ?? 0);
  return total
    ? Math.min(
        100,
        Math.round(
          (Number(stage.confirmed ?? stage.handled ?? 0) / total) * 100,
        ),
      )
    : 0;
}
async function load(silent = false) {
  if (busy.value || disposed) return;
  busy.value = true;
  if (!silent) error.value = "";
  const results = await Promise.allSettled([
    api<{ sources: Source[] }>("/sources"),
    api<Capabilities>("/capabilities"),
    api<Refresh>("/library/refresh"),
    api<any>("/stats"),
  ]);
  if (disposed) return;
  const [s, c, r, st] = results;
  if (s.status === "fulfilled") sources.value = s.value.sources;
  if (c.status === "fulfilled") capabilities.value = c.value;
  if (r.status === "fulfilled") refresh.value = r.value;
  if (st.status === "fulfilled") stats.value = st.value;
  const failed = results.find((v) => v.status === "rejected");
  if (failed?.status === "rejected") error.value = errorText(failed.reason);
  busy.value = false;
}
async function poll() {
  await load(true);
  if (!disposed)
    timer = setTimeout(poll, refresh.value?.running ? 3000 : 10000);
}
async function createSource() {
  saving.value = true;
  error.value = "";
  try {
    await api("/sources", {
      display_name: name.value.trim(),
      root_path: path.value.trim(),
    });
    addOpen.value = false;
    path.value = "";
    name.value = "";
    message.success("文件夹已添加，点击“开始建库”后才会扫描");
    await load();
  } catch (e) {
    error.value = errorText(e);
  } finally {
    saving.value = false;
  }
}
async function build(source: Source, mode: string) {
  saving.value = true;
  error.value = "";
  try {
    refresh.value = await api<Refresh>("/library/refresh", {
      mode,
      source_ids: [source.source_id],
    });
    message.info(
      refresh.value.accepted === false
        ? "已有处理任务正在运行"
        : "已开始处理，可以继续浏览照片",
    );
    await load();
  } catch (e) {
    error.value = errorText(e);
  } finally {
    saving.value = false;
  }
}
async function remove(source: Source) {
  dialog.warning({
    title: `移除「${source.display_name}」？`,
    content: "将停止管理这个文件夹，不会删除磁盘里的照片。已有人工整理会保留。",
    positiveText: "移除来源",
    negativeText: "取消",
    onPositiveClick: async () => {
      try {
        await api(`/sources/${source.source_id}`, undefined, "DELETE");
        await load();
        message.success("来源已移除，原文件未改动");
      } catch (e) {
        error.value = errorText(e);
      }
    },
  });
}
async function scan(source: Source) {
  saving.value = true;
  try {
    await api(`/sources/${source.source_id}/scan`, {});
    message.success("已开始完整校验扫描");
    await load();
  } catch (e) {
    error.value = errorText(e);
  } finally {
    saving.value = false;
  }
}
async function pause() {
  saving.value = true;
  try {
    await api(`/scheduler/${paused.value ? "resume" : "pause"}`, {});
    await load();
  } catch (e) {
    error.value = errorText(e);
  } finally {
    saving.value = false;
  }
}
async function loadJobs(next = false) {
  try {
    const r = await api<any>(
      "/jobs?state=FAILED&limit=20" +
        (next && jobCursor.value
          ? "&cursor=" + encodeURIComponent(jobCursor.value)
          : ""),
    );
    jobs.value = next ? [...jobs.value, ...r.jobs] : r.jobs;
    jobCursor.value = r.next_cursor;
    jobsMore.value = r.has_more;
  } catch (e) {
    error.value = errorText(e);
  }
}
onMounted(() => {
  poll();
  loadJobs();
});
onUnmounted(() => {
  disposed = true;
  clearTimeout(timer);
});
</script>
<template>
  <header class="page-header">
    <div>
      <p class="eyebrow">MADE FOR YOUR LIBRARY</p>
      <h1>图库管理</h1>
      <p class="subtitle">添加照片所在的文件夹，其余交给后台处理。</p>
    </div>
    <NButton type="primary" @click="addOpen = true"
      ><template #icon><FolderPlus :size="18" /></template>添加文件夹</NButton
    >
  </header>
  <NAlert v-if="error" type="error" class="page-notice"
    >{{ error }}
    <div class="alert-action">
      <NButton @click="load()">重新连接</NButton>
    </div></NAlert
  >
  <div class="stats-grid">
    <div class="stat-card">
      <HardDrive :size="21" /><strong>{{
        stats?.assets_total?.toLocaleString() ?? "—"
      }}</strong
      ><span>已登记照片</span>
    </div>
    <div class="stat-card">
      <FolderOpen :size="21" /><strong>{{ sources.length }}</strong
      ><span>照片文件夹</span>
    </div>
    <div class="stat-card">
      <CircleCheck :size="21" /><strong>{{ readySources }}</strong
      ><span>已可搜索的文件夹</span>
    </div>
  </div>
  <NAlert v-if="refresh?.running || paused" type="info" class="page-notice"
    ><span>{{
      paused ? "后台任务已暂停。" : "正在处理照片，你可以继续浏览已有结果。"
    }}</span
    >当前阶段：{{
      stateText(refresh?.state)
    }}。搜索索引完成后，人物和图片文字会继续补齐。
    <div class="alert-action">
      <NButton :loading="saving" @click="pause"
        ><template #icon
          ><Play v-if="paused" :size="16" /><Pause
            v-else
            :size="16" /></template
        >{{ paused ? "继续处理" : "暂停后台任务" }}</NButton
      >
    </div></NAlert
  >
  <NAlert
    v-for="(problem, index) in refresh?.errors"
    :key="index"
    type="warning"
    class="page-notice"
    >{{ stageLabels[problem.stage || ""] || "部分照片" }}处理未完成（{{
      problem.code || "处理失败"
    }}）。已有结果仍可使用。</NAlert
  >
  <div class="section-heading">
    <h2>照片文件夹</h2>
    <NButton quaternary :loading="busy" @click="load()"
      ><template #icon><RefreshCw :size="16" /></template>更新状态</NButton
    >
  </div>
  <article
    v-for="source in sources"
    :key="source.source_id"
    class="source-card surface"
  >
    <div class="source-heading">
      <div class="icon-label">
        <FolderOpen :size="24" />
        <div>
          <h3>{{ source.display_name }}</h3>
          <small class="muted"
            >上次扫描：{{ dateLabel(source.last_successful_scan) }}</small
          >
        </div>
      </div>
      <NTag :type="source.status === 'ONLINE' ? 'success' : 'default'" round>{{
        stateText(source.status)
      }}</NTag>
    </div>
    <div v-if="source.progress" class="stage-grid">
      <div v-for="(stage, key) in source.progress.stages" :key="key">
        <div class="stage-label">
          <span>{{ stageLabels[key] || "照片处理" }}</span
          ><small>{{ stateText(stage.state) }}</small>
        </div>
        <NProgress
          type="line"
          :percentage="progress(stage)"
          :show-indicator="false"
          :height="5"
        /><small class="muted">{{
          stage.state === "UNAVAILABLE"
            ? "所需模型或处理能力尚未就绪"
            : `${Number(stage.confirmed ?? stage.handled ?? 0).toLocaleString()} / ${Number(stage.total ?? 0).toLocaleString()} 张`
        }}</small>
        <small v-if="Number(stage.failed) > 0" class="inline-error"
          >{{ stage.failed }} 张未能处理，可在下方查看失败任务。</small
        >
      </div>
    </div>
    <div class="row-actions">
      <NButton
        type="primary"
        :loading="saving"
        :disabled="refresh?.running || !capabilities?.writable"
        @click="build(source, 'build')"
        >{{ source.progress?.built ? "继续建库" : "开始建库" }}</NButton
      ><NButton
        :loading="saving"
        :disabled="refresh?.running || !capabilities?.writable"
        @click="build(source, 'refresh')"
        >查找新增照片</NButton
      ><NButton quaternary :disabled="refresh?.running" @click="remove(source)"
        >移除来源</NButton
      >
    </div>
    <NCollapse
      ><NCollapseItem title="更多操作" name="source-tools"
        ><p class="muted">文件被替换但日期和大小未变时，可重新校验文件内容。</p>
        <NButton
          size="small"
          :loading="saving"
          :disabled="refresh?.running || !capabilities?.writable"
          @click="scan(source)"
          >完整校验扫描</NButton
        ></NCollapseItem
      ></NCollapse
    >
  </article>
  <NEmpty
    v-if="!sources.length && !busy && !error"
    class="empty-state"
    description="添加第一个照片文件夹，开始整理你的相册"
    ><template #extra
      ><NButton type="primary" @click="addOpen = true"
        >添加文件夹</NButton
      ></template
    ></NEmpty
  >
  <NCollapse class="diagnostics"
    ><NCollapseItem title="处理问题与运行信息" name="details"
      ><p>本地运行 · 图文模型通过 GPU 搜索 · 人物与图片文字分别处理</p>
      <p class="muted">
        这些处理覆盖率表示已完成的照片数量，不代表识别准确率。
      </p>
      <div class="row-actions">
        <NButton size="small" @click="loadJobs()">刷新失败任务</NButton
        ><NButton
          v-if="capabilities?.scheduler"
          size="small"
          :loading="saving"
          @click="pause"
          >{{ paused ? "继续后台任务" : "暂停后台任务" }}</NButton
        >
      </div>
      <p v-if="!jobs.length" class="muted">当前未读到失败任务。</p>
      <div v-for="job in jobs" :key="job.job_id" class="history-row">
        <span>{{
          stageLabels[job.task_type?.toLowerCase()] || "照片处理"
        }}</span
        ><span>{{ job.last_error_code || "未完成" }}</span>
      </div>
      <NButton v-if="jobsMore" @click="loadJobs(true)"
        >更多失败任务</NButton
      ></NCollapseItem
    ></NCollapse
  >
  <NModal
    v-model:show="addOpen"
    preset="card"
    title="添加照片文件夹"
    class="small-modal"
    ><p class="muted">
      填写本机目录的完整路径。添加后点击“开始建库”进行扫描，程序只读取原图。
    </p>
    <div class="form-stack">
      <label
        >显示名称<NInput
          v-model:value="name"
          placeholder="例如：旅行照片"
          :maxlength="200" /></label
      ><label
        >文件夹路径<NInput
          v-model:value="path"
          placeholder="粘贴照片文件夹的完整路径" /></label
      ><NAlert v-if="error" type="error">{{ error }}</NAlert>
    </div>
    <template #footer
      ><NButton
        type="primary"
        :disabled="!name.trim() || !path.trim()"
        :loading="saving"
        @click="createSource"
        >添加文件夹</NButton
      ></template
    ></NModal
  >
</template>

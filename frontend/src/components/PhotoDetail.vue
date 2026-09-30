<script setup lang="ts">
import { ref, watch } from "vue";
import {
  NModal,
  NButton,
  NAlert,
  NSpin,
  NDescriptions,
  NDescriptionsItem,
  useMessage,
} from "naive-ui";
import { api, errorText, fileLabel, mutate } from "../api";
import type { Asset, PlaceDetail } from "../types";
import EntitySelect from "./EntitySelect.vue";
const props = defineProps<{ photo: Asset | null }>();
const emit = defineEmits<{ close: []; changed: [] }>();
const detail = ref<Asset | null>(null),
  place = ref<PlaceDetail | null>(null),
  target = ref<number | null>(null),
  error = ref(""),
  busy = ref(false),
  saving = ref(false),
  broken = ref(false);
const message = useMessage();
let epoch = 0;
async function load() {
  const id = props.photo?.asset_id;
  if (!id) return;
  const run = ++epoch;
  busy.value = true;
  error.value = "";
  detail.value = null;
  place.value = null;
  broken.value = false;
  const results = await Promise.allSettled([
    api<Asset>(`/assets/${id}`),
    api<PlaceDetail>(`/assets/${id}/place`),
  ]);
  if (run !== epoch) return;
  if (results[0].status === "fulfilled") detail.value = results[0].value;
  else error.value = errorText(results[0].reason);
  if (results[1].status === "fulfilled") {
    place.value = results[1].value;
    target.value = place.value.place?.place_id ?? null;
  } else error.value = errorText(results[1].reason);
  busy.value = false;
}
watch(
  () => props.photo?.asset_id,
  (id) => {
    if (id) load();
    else epoch++;
  },
  { immediate: true },
);
async function assign(action: string, suggestion?: number) {
  if (!props.photo || !place.value) return;
  saving.value = true;
  try {
    await mutate(
      `/assets/${props.photo.asset_id}/place-assignment`,
      {
        action,
        expected_revision: place.value.assignment?.revision ?? 0,
        ...(action === "assign"
          ? { target_place_id: suggestion ?? target.value }
          : {}),
        ...(suggestion ? { origin: "CONFIRMED_SUGGESTION" } : {}),
      },
      "PUT",
    );
    message.success("地点已更新");
    await load();
    emit("changed");
  } catch (e) {
    error.value = errorText(e);
  } finally {
    saving.value = false;
  }
}
</script>
<template>
  <NModal
    :show="!!photo"
    preset="card"
    class="detail-modal"
    :title="photo ? fileLabel(detail || photo) : ''"
    :bordered="false"
    @update:show="(v) => !v && emit('close')"
  >
    <NSpin :show="busy"
      ><div v-if="photo" class="detail-layout">
        <div class="detail-image">
          <img
            v-if="!broken"
            :src="`/api/v1/assets/${photo.asset_id}/preview`"
            alt="照片大图"
            @error="broken = true"
          />
          <p v-else>大图暂不可用，可重新连接照片所在硬盘后重试。</p>
        </div>
        <div class="detail-info">
          <NAlert v-if="error" type="error"
            >{{ error }}
            <div class="alert-action">
              <NButton size="small" @click="load">刷新</NButton>
            </div></NAlert
          >
          <NAlert v-if="detail?.availability === 'missing'" type="warning"
            >原图离线，仍可查看已有预览和整理信息。</NAlert
          >
          <h3>照片信息</h3>
          <NDescriptions :column="1" label-placement="left">
            <NDescriptionsItem
              :label="
                detail?.taken_time?.label === 'file_time'
                  ? '文件时间'
                  : '拍摄时间'
              "
              >{{
                detail?.taken_time?.taken_at?.replace("T", " ") || "未记录"
              }}</NDescriptionsItem
            >
            <NDescriptionsItem label="尺寸">{{
              detail?.width && detail?.height
                ? `${detail.width} × ${detail.height}`
                : "未知"
            }}</NDescriptionsItem>
            <NDescriptionsItem label="格式">{{
              detail?.format || "未知"
            }}</NDescriptionsItem>
            <NDescriptionsItem v-if="detail?.taken_time?.camera" label="相机">{{
              detail.taken_time.camera
            }}</NDescriptionsItem>
          </NDescriptions>
          <h3>文件位置</h3>
          <p
            v-for="(location, i) in detail?.locations"
            :key="i"
            class="file-location"
          >
            {{ location.source_display_name
            }}<small>{{ location.relative_path }}</small>
          </p>
          <a
            v-if="detail?.availability === 'present'"
            :href="`/api/v1/assets/${photo.asset_id}/original`"
            target="_blank"
            rel="noopener"
            ><NButton block>打开原图</NButton></a
          >
          <h3>拍摄地点</h3>
          <p v-if="place?.assignment?.mode === 'MANUAL_ASSIGNED'" class="muted">
            {{
              place.assignment.origin === "CONFIRMED_SUGGESTION"
                ? "你已确认的地点建议"
                : "你已指定的地点"
            }}
          </p>
          <p v-else-if="place?.effective_origin === 'GPS_BOUNDARY'" class="muted">
            由照片 GPS 与离线边界自动归类
            <span v-if="place.automatic?.some(p => p.datum_status === 'ASSUMED_WGS84')">（照片未声明坐标基准，按 WGS84 解释）</span>。
            <span v-if="place.automatic?.some(p => p.status === 'AMBIGUOUS')">位于重叠边界，仅归到共同的上级地区。</span>
          </p>
          <p v-else-if="place?.gps_state === 'ABSENT'" class="muted">
            照片没有 GPS 信息，可以手动指定地点。
          </p>
          <p v-else-if="place?.gps_state === 'NOT_EXTRACTED'" class="muted">
            尚未提取 GPS 信息，可以先手动整理。
          </p>
          <p
            v-else-if="
              ['INVALID', 'UNSUPPORTED_CRS'].includes(place?.gps_state || '')
            "
            class="muted"
          >
            GPS 信息无效或坐标基准不受支持，不会据此确定地点。
          </p>
          <p class="muted">
            {{
              place?.place
                ? [
                    ...place.place.ancestors.map((p) => p.canonical_name),
                    place.place.canonical_name,
                  ].join(" / ")
                : "尚未指定地点"
            }}
          </p>
          <p v-if="place?.areas?.length" class="muted">景区：{{ place.areas.map(p => p.canonical_name).join('、') }}</p>
          <div
            v-for="candidate in place?.suggested.candidates"
            :key="candidate.candidate_id"
            class="place-suggestion"
          >
            <small
              >附近城市建议 ·
              {{
                [...candidate.ancestor_names, candidate.display_name].join(
                  " / ",
                )
              }}
              · 距城市参考点约
              {{ (candidate.distance_m / 1000).toFixed(1) }}
              公里，需你确认。</small
            ><NButton
              v-if="candidate.local_place_id"
              size="small"
              :loading="saving"
              @click="assign('assign', candidate.local_place_id)"
              >确认这个地点</NButton
            ><small v-else>可在地点页创建对应地点后指定</small>
          </div>
          <EntitySelect
            v-model="target"
            kind="places"
            :disabled="saving || !place"
          />
          <div class="row-actions">
            <NButton
              type="primary"
              :disabled="!target || !place"
              :loading="saving"
              @click="assign('assign')"
              >保存地点</NButton
            ><NButton
              :disabled="!place"
              :loading="saving"
              @click="assign('unassign')"
              >取消归类</NButton
            ><NButton
              text
              :disabled="!place"
              :loading="saving"
              @click="assign('restore_auto')"
              >恢复自动归类</NButton
            >
          </div>
          <p
            v-if="photo.hit_reason && photo.hit_reason !== 'FILTER_ONLY'"
            class="muted"
          >
            找到这张照片的依据：{{
              photo.hit_reason.includes("SEMANTIC")
                ? "画面内容相似"
                : "图片文字、文件名或地点文字匹配"
            }}{{
              photo.matched_text?.includes("ocr_text")
                ? "，包含图片文字匹配"
                : ""
            }}
          </p>
        </div>
      </div></NSpin
    >
  </NModal>
</template>

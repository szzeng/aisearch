<script setup lang="ts">
import { ref } from "vue";
import { NCheckbox, NSkeleton } from "naive-ui";
import { ImageOff } from "@lucide/vue";
import type { Asset } from "../types";
import { fileLabel } from "../api";
defineProps<{
  photos: Asset[];
  loading?: boolean;
  selectable?: boolean;
  selected?: number[];
}>();
defineEmits<{
  open: [photo: Asset];
  select: [photo: Asset, checked: boolean];
}>();
const broken = ref(new Set<number>());
</script>
<template>
  <div class="photo-grid" aria-label="照片列表" :aria-busy="loading">
    <article
      v-for="photo in photos"
      :key="photo.asset_id"
      class="photo-tile"
      :class="{ selected: selected?.includes(photo.asset_id) }"
    >
      <button
        class="photo-open"
        :aria-label="`查看 ${fileLabel(photo)}`"
        @click="$emit('open', photo)"
      >
        <img
          v-if="!broken.has(photo.asset_id)"
          :src="`/api/v1/assets/${photo.asset_id}/thumbnail`"
          :alt="fileLabel(photo)"
          loading="lazy"
          @error="broken.add(photo.asset_id)"
        />
        <span v-else class="image-placeholder"
          ><ImageOff :size="26" />预览暂不可用</span
        >
        <span v-if="photo.availability === 'missing'" class="image-badge"
          >原图离线</span
        >
      </button>
      <NCheckbox
        v-if="selectable"
        class="tile-check"
        :checked="selected?.includes(photo.asset_id)"
        :aria-label="`选择 ${fileLabel(photo)}`"
        @update:checked="(v) => $emit('select', photo, v)"
      />
      <div class="photo-caption">
        <span>{{ fileLabel(photo) }}</span
        ><small v-if="photo.matched_text?.includes('ocr_text')"
          >图片文字匹配</small
        ><small v-else-if="photo.hit_reason?.includes('SEMANTIC')"
          >画面相似</small
        >
      </div>
    </article>
    <NSkeleton
      v-for="i in loading ? 6 : 0"
      :key="`s${i}`"
      :height="190"
      :sharp="false"
    />
  </div>
</template>

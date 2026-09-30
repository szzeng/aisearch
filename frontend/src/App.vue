<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from "vue";
import {
  NConfigProvider,
  NMessageProvider,
  NDialogProvider,
  NAlert,
  darkTheme,
  zhCN,
  dateZhCN,
} from "naive-ui";
import { Images, UsersRound, MapPin, FolderOpen, HardDrive } from "@lucide/vue";
import { sessionExpired } from "./api";
const dark = ref(false);
const media = matchMedia("(prefers-color-scheme: dark)");
const update = () => {
  dark.value = media.matches;
};
onMounted(() => {
  update();
  media.addEventListener("change", update);
});
onUnmounted(() => media.removeEventListener("change", update));
const theme = computed(() => (dark.value ? darkTheme : null));
const links = [
  { path: "/", text: "照片", icon: Images },
  { path: "/persons", text: "人物", icon: UsersRound },
  { path: "/places", text: "地点", icon: MapPin },
  { path: "/library", text: "图库管理", icon: FolderOpen },
];
</script>
<template>
  <NConfigProvider
    :theme="theme"
    :locale="zhCN"
    :date-locale="dateZhCN"
    :theme-overrides="{
      common: {
        primaryColor: '#39786d',
        primaryColorHover: '#4a9185',
        primaryColorPressed: '#286054',
        borderRadius: '10px',
        fontFamily:
          'Inter, -apple-system, BlinkMacSystemFont, Segoe UI, sans-serif',
      },
    }"
  >
    <NMessageProvider
      ><NDialogProvider>
        <div class="app-shell">
          <aside class="sidebar">
            <RouterLink to="/" class="brand"
              ><span class="brand-mark"><Images :size="22" /></span
              ><span>拾光<small>本地图片搜索</small></span></RouterLink
            >
            <nav aria-label="主要导航">
              <RouterLink
                v-for="link in links"
                :key="link.path"
                :to="link.path"
                :class="{ active: $route.path === link.path }"
                ><component :is="link.icon" :size="19" /><span>{{
                  link.text
                }}</span></RouterLink
              >
            </nav>
            <div class="local-note">
              <HardDrive :size="16" /><span>照片与搜索都留在本机</span>
            </div>
          </aside>
          <main id="main" class="main-content">
            <NAlert
              v-if="sessionExpired"
              title="请重新连接相册"
              type="warning"
              class="page-notice"
              >本地服务已重启或连接已失效。请使用启动器输出的新链接打开相册。</NAlert
            >
            <RouterView />
          </main>
        </div> </NDialogProvider
    ></NMessageProvider>
  </NConfigProvider>
</template>

import { createApp } from "vue";
import { createRouter, createWebHistory } from "vue-router";
import App from "./App.vue";
import Photos from "./pages/Photos.vue";
import "./style.css";

const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: "/", component: Photos },
    { path: "/search", redirect: "/" },
    { path: "/persons", component: () => import("./pages/People.vue") },
    { path: "/places", component: () => import("./pages/Places.vue") },
    { path: "/library", component: () => import("./pages/Library.vue") },
    { path: "/sources", redirect: "/library" },
  ],
});
createApp(App).use(router).mount("#app");

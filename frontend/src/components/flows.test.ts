// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { flushPromises, mount } from "@vue/test-utils";
import { createMemoryHistory, createRouter } from "vue-router";
import {
  NDatePicker,
  NCheckbox,
  NSelect,
  NMessageProvider,
  NDialogProvider,
} from "naive-ui";
import { defineComponent, h } from "vue";
import Photos from "../pages/Photos.vue";
import Library from "../pages/Library.vue";
import PhotoDetail from "./PhotoDetail.vue";
import EntitySelect from "./EntitySelect.vue";
import Places from "../pages/Places.vue";
import OperationHistory from "./OperationHistory.vue";
import { api, dateBounds, mutate } from "../api";

vi.mock("../api", async (importOriginal) => ({
  ...(await importOriginal<object>()),
  api: vi.fn(),
  mutate: vi.fn(),
}));
const mockApi = vi.mocked(api);
const mounted: ReturnType<typeof mount>[] = [];
beforeEach(() => {
  vi.clearAllMocks();
  vi.stubGlobal(
    "ResizeObserver",
    class {
      observe() {}
      unobserve() {}
      disconnect() {}
    },
  );
  vi.stubGlobal("matchMedia", () => ({
    matches: false,
    addEventListener() {},
    removeEventListener() {},
  }));
  mockApi.mockImplementation(async (path) => {
    if (path === "/sources") return { sources: [] };
    if (path.startsWith("/assets?"))
      return { assets: [], next_cursor: null, has_more: false };
    if (path.startsWith("/persons")) return { persons: [], has_more: false };
    if (path === "/places/boundaries") return {available: false, packages: []};
    if (path.startsWith("/places")) return { places: [], has_more: false };
    if (path === "/capabilities") return { writable: true };
    if (path === "/library/refresh")
      return { running: false, state: "IDLE", errors: [] };
    if (path === "/stats") return { assets_total: 0 };
    if (path.startsWith("/jobs")) return { jobs: [] };
    return {
      hits: [],
      state: "OK",
      search_session_id: "s1",
      has_more: false,
      total_candidates: 0,
    };
  });
});
afterEach(() => {
  mounted.splice(0).forEach((w) => w.unmount());
  vi.unstubAllGlobals();
});
async function page(component: any, props = {}) {
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: "/", component },
      { path: "/library", component },
    ],
  });
  await router.push("/");
  await router.isReady();
  const wrapper = mount(
    defineComponent({
      render: () =>
        h(NMessageProvider, null, {
          default: () =>
            h(NDialogProvider, null, { default: () => h(component, props) }),
        }),
    }),
    { global: { plugins: [router], stubs: { teleport: true } } },
  );
  mounted.push(wrapper);
  await flushPromises();
  return wrapper;
}
describe("user workflows", () => {
  it("browses without a model, then submits text with an inclusive calendar date converted to the API boundary", async () => {
    const wrapper = await page(Photos);
    expect(mockApi.mock.calls.some(([p]) => p.startsWith("/assets?"))).toBe(
      true,
    );
    expect(mockApi.mock.calls.some(([p]) => p === "/search")).toBe(false);
    const input = wrapper.get('input[placeholder*="海边"]');
    await input.setValue("海边的猫");
    await wrapper.get(".n-collapse-item__header-main").trigger("click");
    await flushPromises();
    await wrapper
      .findComponent(NDatePicker)
      .vm.$emit("update:value", [
        new Date(2025, 0, 1).getTime(),
        new Date(2025, 11, 31).getTime(),
      ]);
    await wrapper.get("form.search-bar").trigger("submit");
    await flushPromises();
    const payload = mockApi.mock.calls.findLast(
      ([p]) => p === "/search",
    )?.[1] as any;
    expect(payload.text).toBe("海边的猫");
    expect(payload.capture_from).toBe("2025-01-01");
    expect(payload.capture_to_exclusive).toBe("2026-01-01");
    expect(payload.top_k).toBe(5000);
    expect(payload.request_id).toBeTruthy();
  });
  it("carries exact-place scope through browse, text search and uploaded-image search", async () => {
    const base = mockApi.getMockImplementation()!;
    mockApi.mockImplementation(async (path, ...args) => {
      if (path.startsWith("/places"))
        return {
          places: [
            {
              place_id: 4,
              canonical_name: "杭州",
              parent_place_id: null,
              kind: "city",
              aliases: [],
            },
          ],
          has_more: false,
        };
      return base(path, ...args);
    });
    const wrapper = await page(Photos);
    await wrapper.get(".n-collapse-item__header-main").trigger("click");
    await flushPromises();
    wrapper
      .findAllComponents(EntitySelect)
      .find((c) => c.props("kind") === "places")!
      .vm.$emit("update:modelValue", [4]);
    await flushPromises();
    wrapper.findComponent(NCheckbox).vm.$emit("update:checked", false);
    await wrapper.get("form.search-bar").trigger("submit");
    await flushPromises();
    const browse = mockApi.mock.calls.findLast(([p]) =>
      p.startsWith("/assets?"),
    )![0];
    expect(
      new URLSearchParams(browse.split("?")[1]).get("include_descendants"),
    ).toBe("false");
    await wrapper.get('input[placeholder*="海边"]').setValue("日落");
    await wrapper.get("form.search-bar").trigger("submit");
    await flushPromises();
    expect(
      mockApi.mock.calls.findLast(([p]) => p === "/search")![1],
    ).toMatchObject({ place_ids: [4], include_descendants: false });
    vi.stubGlobal(
      "URL",
      class extends URL {
        static createObjectURL() {
          return "blob:fixture";
        }
        static revokeObjectURL() {}
      },
    );
    const upload = wrapper.get('input[type="file"]');
    Object.defineProperty(upload.element, "files", {
      value: [new File(["fixture"], "photo.jpg", { type: "image/jpeg" })],
    });
    await upload.trigger("change");
    await flushPromises();
    const form = mockApi.mock.calls.findLast(
      ([p]) => p === "/search/image",
    )![1] as FormData;
    expect(form.get("include_descendants")).toBe("false");
    expect(form.get("place_ids")).toBe("4");
  });
  it("distinguishes identical place names and disables invalid parent kinds", async () => {
    mockApi.mockResolvedValue({
      places: [
        {
          place_id: 1,
          canonical_name: "家",
          parent_place_id: null,
          kind: "custom",
        },
        {
          place_id: 2,
          canonical_name: "家",
          parent_place_id: null,
          kind: "custom",
        },
        {
          place_id: 3,
          canonical_name: "中国",
          parent_place_id: null,
          kind: "country",
        },
      ],
      has_more: false,
    });
    const wrapper = await page(EntitySelect, {
      kind: "places",
      modelValue: null,
      allowedPlaceKinds: ["country"],
    });
    const options = wrapper.findComponent(NSelect).props("options") as any[];
    expect(options[0].label).not.toBe(options[1].label);
    expect(options.map((o) => o.disabled)).toEqual([true, true, false]);
  });
  it("updates place-page scope and its explanation together", async () => {
    const base = mockApi.getMockImplementation()!;
    mockApi.mockImplementation(async (path, ...args) =>
      path.startsWith("/places/browse?")
        ? {
            places: [
              {
                place_id: 4,
                canonical_name: "杭州",
                parent_place_id: null,
                kind: "city",
                aliases: [],
              },
            ],
            has_more: false,
          }
        : base(path, ...args),
    );
    const wrapper = await page(Places);
    await wrapper
      .findAll("button")
      .find((b) => b.text().startsWith("杭州"))!
      .trigger("click");
    await flushPromises();
    expect(wrapper.text()).toContain("包含下级地点的照片");
    wrapper.findComponent(NCheckbox).vm.$emit("update:checked", false);
    await flushPromises();
    expect(wrapper.text()).toContain("仅直接归到当前地点的照片");
    expect(
      mockApi.mock.calls.findLast(([p]) => p.startsWith("/assets?"))![0],
    ).toContain("include_descendants=false");
  });
  it("browses parent and child collections at different levels", async () => {
    const base = mockApi.getMockImplementation()!;
    const city = {place_id: 1, canonical_name: "杭州", kind: "city", parent_place_id: null, aliases: [], asset_count: 2};
    const district = {place_id: 2, canonical_name: "西湖区", kind: "district", parent_place_id: 1, aliases: [], asset_count: 1};
    mockApi.mockImplementation(async (path, ...args) => {
      if (path.startsWith("/places/browse?")) return {places: path.includes("parent=1") ? [district] : [city], ancestors: [], has_more: false, areas: []};
      return base(path, ...args);
    });
    const wrapper = await page(Places);
    expect(wrapper.findAll(".place-card").map(c => c.text()).join()).toContain("杭州");
    expect(wrapper.findAll(".place-card").map(c => c.text()).join()).not.toContain("西湖区");
    await wrapper.get(".place-card button").trigger("click");
    await flushPromises();
    expect(wrapper.get('[aria-label="地点层级"]').text()).toContain("杭州");
    expect(wrapper.findAll(".place-card").map(c => c.text()).join()).toContain("西湖区");
    expect(wrapper.text()).toContain("这个地区的全部照片");
  });
  it("explains missing offline data separately from having no GPS suggestions", async () => {
    const base = mockApi.getMockImplementation()!;
    mockApi.mockImplementation(async (path, ...args) =>
      path === "/places/datasets"
        ? { state: "unavailable", resolver_available: false }
        : base(path, ...args),
    );
    const wrapper = await page(Places);
    const tabs = wrapper.findAll(".n-tabs-tab");
    await tabs.find((t) => t.text() === "待确认建议")!.trigger("click");
    await flushPromises();
    expect(wrapper.text()).toContain("尚未安装离线地名数据包");
    expect(wrapper.text()).toContain("不能证明行政区或景点归属");
  });
  it("retains a failed search as an error instead of reporting no matches", async () => {
    const wrapper = await page(Photos);
    mockApi.mockRejectedValueOnce(new Error("offline"));
    await wrapper.get('input[placeholder*="海边"]').setValue("狗");
    await wrapper.get("form.search-bar").trigger("submit");
    await flushPromises();
    expect(wrapper.text()).toContain("操作未完成");
    expect(wrapper.text()).not.toContain("没有找到符合条件");
  });
  it("registers a folder without starting a scan until the user requests it", async () => {
    const wrapper = await page(Library);
    await wrapper
      .findAll("button")
      .find((b) => b.text() === "添加文件夹")!
      .trigger("click");
    await flushPromises();
    await wrapper
      .get('input[placeholder="例如：旅行照片"]')
      .setValue("测试照片");
    await wrapper
      .get('input[placeholder="粘贴照片文件夹的完整路径"]')
      .setValue("/public-fixture");
    await wrapper
      .findAll("button")
      .findLast((b) => b.text() === "添加文件夹")!
      .trigger("click");
    await flushPromises();
    expect(
      mockApi.mock.calls.find(([p, body]) => p === "/sources" && !!body)?.[1],
    ).toEqual({ display_name: "测试照片", root_path: "/public-fixture" });
    expect(
      mockApi.mock.calls.some(
        ([p, body]) =>
          !!body && (p.endsWith("/scan") || p === "/library/refresh"),
      ),
    ).toBe(false);
  });
  it("resumes a paused scheduler using its persisted state", async () => {
    const base = mockApi.getMockImplementation()!;
    mockApi.mockImplementation(async (path, ...args) => {
      if (path === "/capabilities")
        return { writable: true, scheduler: { state: "paused" } };
      return base(path, ...args);
    });
    const wrapper = await page(Library);
    expect(wrapper.text()).toContain("后台任务已暂停");
    await wrapper
      .findAll("button")
      .find((b) => b.text() === "继续处理")!
      .trigger("click");
    await flushPromises();
    expect(mockApi).toHaveBeenCalledWith("/scheduler/resume", {});
    expect(mockApi.mock.calls.some(([p]) => p === "/scheduler/pause")).toBe(
      false,
    );
  });
  it("does not mistake a pending sample limit for unfinished indexing", async () => {
    const wrapper = await page(Photos);
    mockApi.mockResolvedValueOnce({
      hits: [],
      state: "OK",
      pending_semantic: { count: 0, limit: 200 },
      has_more: false,
      total_candidates: 0,
    });
    await wrapper.get('input[placeholder*="海边"]').setValue("狗");
    await wrapper.get("form.search-bar").trigger("submit");
    await flushPromises();
    expect(wrapper.text()).not.toContain("还有照片正在建立搜索索引");
  });
  it("shows a reversed place operation as undone and prevents a second undo", async () => {
    const base = mockApi.getMockImplementation()!;
    mockApi.mockImplementation(async (path, ...args) => {
      if (path.startsWith("/place-operations"))
        return {
          operations: [
            {
              operation_id: "p1",
              operation_type: "BATCH_ASSIGN",
              operation_revision: 2,
              status: "REVERSED",
            },
          ],
          has_more: false,
        };
      return base(path, ...args);
    });
    const wrapper = await page(OperationHistory, { kind: "place" });
    expect(wrapper.text()).toContain("已撤销");
    expect(wrapper.text()).toContain("批量指定地点");
    expect(wrapper.get("button").attributes("disabled")).toBeDefined();
  });
  it("updates a photo place using the revision that was displayed", async () => {
    const base = mockApi.getMockImplementation()!;
    mockApi.mockImplementation(async (path, ...args) => {
      if (path === "/assets/7")
        return { asset_id: 7, locations: [], availability: "missing" };
      if (path === "/assets/7/place")
        return {
          assignment: { revision: 9 },
          place: null,
          suggested: { candidates: [] },
        };
      return base(path, ...args);
    });
    const wrapper = await page(PhotoDetail, { photo: { asset_id: 7 } });
    await flushPromises();
    const button = wrapper
      .findAll("button")
      .find((b) => b.text() === "取消归类")!;
    await button.trigger("click");
    await flushPromises();
    expect(mutate).toHaveBeenCalledWith(
      "/assets/7/place-assignment",
      { action: "unassign", expected_revision: 9 },
      "PUT",
    );
  });
});
it("advances the inclusive end date across a daylight-saving boundary without using UTC", () => {
  expect(
    dateBounds([
      new Date(2025, 2, 8).getTime(),
      new Date(2025, 2, 9).getTime(),
    ]),
  ).toEqual({ capture_from: "2025-03-08", capture_to_exclusive: "2025-03-10" });
});

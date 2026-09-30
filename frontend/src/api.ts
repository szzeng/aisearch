import { ref } from "vue";
import type { Place } from "./types";

export const sessionExpired = ref(false);
export class ApiError extends Error {
  constructor(
    public code: string,
    public requestId = "",
  ) {
    super(code);
  }
}
const messages: Record<string, string> = {
  SESSION_REQUIRED: "连接已失效，请重新打开启动器提供的链接。",
  RUNTIME_CAPABILITY_UNAVAILABLE:
    "搜索模型暂不可用，请到图库管理查看处理状态。",
  SEARCH_PROFILE_NOT_ACTIVE: "图片索引尚未就绪，请先在图库管理中开始建库。",
  SEARCH_SESSION_EXPIRED: "这次搜索已过期，请重新搜索。",
  INVALID_CURSOR: "列表已经变化，请刷新后继续。",
  CATALOG_FILTER_REVISION_STALE: "照片整理结果已更新，请重新搜索。",
  FILTER_REVISION_CHANGED: "照片整理结果已更新，请重新搜索。",
  PLACE_SUGGESTION_REVISION_STALE: "地点建议已更新，请刷新后重试。",
  UPLOAD_TOO_LARGE: "图片文件太大，请选择较小的图片。",
  UPLOAD_TOO_MANY_PIXELS: "图片尺寸过大，请缩小后重试。",
  FORMAT_UNSUPPORTED: "暂不支持这种图片格式。",
  DECODE_FAILED: "无法读取这张图片，请换一张试试。",
  PLACE_PARENT_CYCLE:
    "上级地点类型不匹配，或地点层级过深/形成循环。请重新选择上级。",
  VALIDATION_ERROR: "填写的内容不符合要求，请检查后重试。",
  INVALID_STATE_TRANSITION: "当前有任务正在处理，请稍后重试。",
  NETWORK: "无法连接本地服务。请确认程序仍在运行，然后重试。",
};
export function errorText(error: unknown): string {
  if (!(error instanceof ApiError)) return "操作未完成，请刷新后重试。";
  if (/CONFLICT|REVISION|STALE/.test(error.code))
    return "这些内容刚刚发生了变化。请刷新后重新选择，已有整理会保留。";
  return (
    messages[error.code] ??
    `操作未完成（${error.code}），请重试或检查图库状态。`
  );
}
export async function api<T>(
  path: string,
  body?: unknown,
  method = body === undefined ? "GET" : "POST",
  signal?: AbortSignal,
): Promise<T> {
  const headers: Record<string, string> = {};
  if (method !== "GET") {
    headers["X-CSRF-Token"] = decodeURIComponent(
      document.cookie
        .split("; ")
        .find((c) => c.startsWith("ps_csrf="))
        ?.slice(8) ?? "",
    );
    if (!(body instanceof FormData))
      headers["Content-Type"] = "application/json";
  }
  let response: Response;
  try {
    response = await fetch("/api/v1" + path, {
      method,
      headers,
      credentials: "same-origin",
      cache: "no-store",
      signal,
      body:
        body === undefined
          ? undefined
          : body instanceof FormData
            ? body
            : JSON.stringify(body),
    });
  } catch (e) {
    if (e instanceof DOMException && e.name === "AbortError") throw e;
    throw new ApiError("NETWORK");
  }
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    if (response.status === 401) sessionExpired.value = true;
    // Backend diagnostics can contain paths; only stable codes reach the interface.
    throw new ApiError(
      payload.code ?? `HTTP_${response.status}`,
      payload.request_id,
    );
  }
  return payload as T;
}
const pendingWrites = new Map<string, string>();
export async function mutate<T>(
  path: string,
  body: Record<string, unknown>,
  method = "POST",
): Promise<T> {
  const key = JSON.stringify([method, path, body]);
  if (!pendingWrites.has(key)) pendingWrites.set(key, crypto.randomUUID());
  const result = await api<T>(
    path,
    { ...body, request_id: pendingWrites.get(key) },
    method,
  );
  pendingWrites.delete(key);
  return result;
}
export function query(values: Record<string, unknown>) {
  const q = new URLSearchParams();
  for (const [key, value] of Object.entries(values)) {
    if (value === null || value === undefined || value === "") continue;
    for (const item of Array.isArray(value) ? value : [value])
      q.append(key, String(item));
  }
  return "?" + q.toString();
}
export function dateLabel(value?: number | null) {
  return value
    ? new Date(value).toLocaleString("zh-CN", { hour12: false })
    : "尚未处理";
}
export function day(value: number) {
  const d = new Date(value);
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}
export function dateBounds(dates: [number, number] | null) {
  if (!dates) return { capture_from: null, capture_to_exclusive: null };
  const end = new Date(dates[1]);
  end.setDate(end.getDate() + 1);
  return {
    capture_from: day(dates[0]),
    capture_to_exclusive: day(end.getTime()),
  };
}
export const personLabel = (p: { name: string | null; person_id: number }) =>
  p.name || `未命名人物 ${p.person_id}`;
export const fileLabel = (a: {
  current_location?: { relative_path: string };
  locations?: { relative_path: string }[];
  asset_id: number;
}) =>
  (a.current_location?.relative_path || a.locations?.[0]?.relative_path)
    ?.split(/[\\/]/)
    .pop() || `照片 ${a.asset_id}`;

// Names alone are ambiguous: resolve the bounded page's ancestor paths, sharing lookups.
export async function placePaths(
  places: Place[],
): Promise<Record<number, string>> {
  const cache = new Map(places.map((p) => [p.place_id, p]));
  const labels: Record<number, string> = {};
  for (const place of places) {
    const names = [place.canonical_name];
    const seen = new Set([place.place_id]);
    let parent = place.parent_place_id;
    while (parent && seen.size <= 64 && !seen.has(parent)) {
      seen.add(parent);
      const record: Place =
        cache.get(parent) ?? (await api<Place>(`/places/${parent}`));
      cache.set(parent, record);
      names.unshift(record.canonical_name);
      parent = record.parent_place_id;
    }
    labels[place.place_id] = names.join(" / ");
  }
  return labels;
}

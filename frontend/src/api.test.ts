// @vitest-environment jsdom
import { afterEach, expect, it, vi } from "vitest";
import { api, ApiError, errorText, mutate, sessionExpired } from "./api";
afterEach(() => vi.unstubAllGlobals());
it("sends CSRF only in a same-origin request header and preserves an idempotency key after a network failure", async () => {
  document.cookie = "ps_csrf=fixture-token";
  const fetcher = vi
    .fn()
    .mockRejectedValueOnce(new TypeError("offline"))
    .mockResolvedValueOnce(new Response("{}", { status: 200 }));
  vi.stubGlobal("fetch", fetcher);
  const body = { expected_revision: 4, name: "妈妈" };
  await expect(mutate("/persons/1/name", body, "PUT")).rejects.toMatchObject({
    code: "NETWORK",
  });
  await mutate("/persons/1/name", body, "PUT");
  expect(fetcher.mock.calls[0][0]).toBe("/api/v1/persons/1/name");
  const first = fetcher.mock.calls[0][1],
    second = fetcher.mock.calls[1][1];
  expect(first.headers["X-CSRF-Token"]).toBe("fixture-token");
  expect(first.credentials).toBe("same-origin");
  expect(JSON.parse(first.body).request_id).toBe(
    JSON.parse(second.body).request_id,
  );
});
it("shows session expiry and never echoes backend paths in errors", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn().mockResolvedValue(
      new Response(
        JSON.stringify({
          code: "SESSION_REQUIRED",
          message: "/Users/private/photos",
        }),
        { status: 401 },
      ),
    ),
  );
  await expect(api("/stats")).rejects.toBeInstanceOf(ApiError);
  expect(sessionExpired.value).toBe(true);
  expect(errorText(new ApiError("SESSION_REQUIRED"))).not.toContain("/Users");
});

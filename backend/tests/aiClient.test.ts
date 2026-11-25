import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { callAi } from "../shared/aiClient";
import { HttpError } from "../shared/http";

const ok = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });

beforeEach(() => {
  process.env.AI_SERVICE_URL = "https://ai.example.com/";
  process.env.AI_SERVICE_API_KEY = "secret-key";
  delete process.env.AI_TIMEOUT_MS;
});
afterEach(() => vi.unstubAllGlobals());

async function failure(promise: Promise<unknown>): Promise<HttpError> {
  try {
    await promise;
  } catch (e) {
    return e as HttpError;
  }
  throw new Error("expected a rejection");
}

describe("callAi", () => {
  it("sends the API key, JSON body and query to the configured URL", async () => {
    const fetchMock = vi.fn().mockResolvedValue(ok({ file_id: "f", chunk_count: 2 }));
    vi.stubGlobal("fetch", fetchMock);
    const out = await callAi({ method: "POST", path: "/files/index-notes", json: { a: 1 }, query: { user_id: "u" } });
    expect(out).toEqual({ file_id: "f", chunk_count: 2 });
    const [url, init] = fetchMock.mock.calls[0]!;
    expect(String(url)).toBe("https://ai.example.com/files/index-notes?user_id=u");
    expect(init.headers["x-api-key"]).toBe("secret-key");
    expect(init.headers["content-type"]).toBe("application/json");
    expect(init.body).toBe('{"a":1}');
  });

  it("returns undefined for 204", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(null, { status: 204 })));
    expect(await callAi({ method: "DELETE", path: "/files/x" })).toBeUndefined();
  });

  it.each([403, 404, 413, 415, 422, 429, 503])("passes AI status %i and code through", async (status) => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(ok({ code: "some_code", detail: "some detail", request_id: "r" }, status))
    );
    const err = await failure(callAi({ method: "POST", path: "/plans/generate", json: {} }));
    expect(err.statusCode).toBe(status);
    expect(err.code).toBe("some_code");
    expect(err.message).toBe("some detail");
  });

  it("maps an AI 401 (our own bad key) to 502 without leaking it to the app", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(ok({ code: "unauthorized", detail: "x" }, 401)));
    const err = await failure(callAi({ method: "POST", path: "/plans/generate", json: {} }));
    expect(err.statusCode).toBe(502);
    expect(err.code).toBe("AI_AUTH");
  });

  it("maps unexpected statuses and non-JSON bodies to 502", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response("<html>boom</html>", { status: 500 })));
    expect((await failure(callAi({ method: "POST", path: "/x", json: {} }))).statusCode).toBe(502);
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response("not json", { status: 200 })));
    expect((await failure(callAi({ method: "POST", path: "/x", json: {} }))).code).toBe("AI_BAD_RESPONSE");
  });

  it("maps network failures to 503 and timeouts to 504", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("fetch failed")));
    expect((await failure(callAi({ method: "POST", path: "/x", json: {} }))).statusCode).toBe(503);
    const timeout = Object.assign(new Error("t"), { name: "TimeoutError" });
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(timeout));
    expect((await failure(callAi({ method: "POST", path: "/x", json: {} }))).statusCode).toBe(504);
  });

  it("fails clearly when AI_SERVICE_URL is missing", async () => {
    delete process.env.AI_SERVICE_URL;
    const err = await failure(callAi({ method: "POST", path: "/x", json: {} }));
    expect(err.code).toBe("AI_NOT_CONFIGURED");
  });
});

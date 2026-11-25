import { HttpError } from "./http";

/**
 * Thin client for the dawem-ai FastAPI service.
 * Configuration comes only from the environment:
 *   AI_SERVICE_URL       base URL, e.g. https://ai.example.com (no trailing slash needed)
 *   AI_SERVICE_API_KEY   sent as X-API-Key (the AI service rejects calls without it when it has a key)
 *   AI_TIMEOUT_MS        optional, default 25000 (API Gateway HTTP APIs cut off at 30 s)
 */

export interface AiCall {
  method: "POST" | "DELETE";
  path: string;
  query?: Record<string, string>;
  json?: unknown;
  /** Raw body (multipart passthrough). Mutually exclusive with `json`. */
  rawBody?: Buffer;
  contentType?: string;
}

interface AiErrorBody {
  code?: string;
  detail?: string;
}

/** AI statuses that are meaningful to the app and are passed through unchanged. */
const PASSTHROUGH = new Set([400, 403, 404, 413, 415, 422, 429, 502, 503]);

export function aiConfig(): { baseUrl: string; apiKey: string; timeoutMs: number } {
  const baseUrl = (process.env.AI_SERVICE_URL ?? "").trim().replace(/\/+$/, "");
  if (!baseUrl) throw new HttpError(503, "AI service is not configured", "AI_NOT_CONFIGURED");
  const timeoutMs = Number(process.env.AI_TIMEOUT_MS ?? "25000");
  return {
    baseUrl,
    apiKey: process.env.AI_SERVICE_API_KEY ?? "",
    timeoutMs: Number.isFinite(timeoutMs) && timeoutMs > 0 ? timeoutMs : 25000,
  };
}

/** Call the AI service. Returns the parsed JSON body, or undefined for 204. Throws HttpError on any failure. */
export async function callAi<T = unknown>(call: AiCall): Promise<T | undefined> {
  const { baseUrl, apiKey, timeoutMs } = aiConfig();
  const url = new URL(baseUrl + call.path);
  for (const [k, v] of Object.entries(call.query ?? {})) url.searchParams.set(k, v);

  const headers: Record<string, string> = { accept: "application/json" };
  if (apiKey) headers["x-api-key"] = apiKey;
  let body: Buffer | string | undefined;
  if (call.rawBody) {
    body = call.rawBody;
    if (call.contentType) headers["content-type"] = call.contentType;
  } else if (call.json !== undefined) {
    body = JSON.stringify(call.json);
    headers["content-type"] = "application/json";
  }

  let res: Response;
  try {
    res = await fetch(url, {
      method: call.method,
      headers,
      body: body as BodyInit | undefined,
      signal: AbortSignal.timeout(timeoutMs),
    });
  } catch (err) {
    const timedOut = err instanceof Error && (err.name === "TimeoutError" || err.name === "AbortError");
    console.error(JSON.stringify({ level: "ERROR", message: "AI call failed", path: call.path, timedOut }));
    throw new HttpError(
      timedOut ? 504 : 503,
      timedOut ? "AI service timed out" : "AI service unavailable",
      timedOut ? "AI_TIMEOUT" : "AI_UNAVAILABLE"
    );
  }

  if (res.status === 204) return undefined;
  const text = await res.text();

  if (!res.ok) {
    let parsed: AiErrorBody = {};
    try {
      parsed = JSON.parse(text) as AiErrorBody;
    } catch {
      /* non-JSON error body: ignore */
    }
    if (res.status === 401) {
      // Our own X-API-Key is wrong: a deployment problem, not something the end user can fix.
      console.error(JSON.stringify({ level: "ERROR", message: "AI service rejected AI_SERVICE_API_KEY" }));
      throw new HttpError(502, "AI service rejected the backend credentials", "AI_AUTH");
    }
    const status = PASSTHROUGH.has(res.status) ? res.status : 502;
    throw new HttpError(status, parsed.detail ?? "AI service error", parsed.code ?? "AI_ERROR");
  }

  try {
    return JSON.parse(text) as T;
  } catch {
    throw new HttpError(502, "AI service returned invalid JSON", "AI_BAD_RESPONSE");
  }
}

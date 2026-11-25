import type { APIGatewayProxyEventV2WithJWTAuthorizer, APIGatewayProxyResultV2 } from "aws-lambda";

export class HttpError extends Error {
  constructor(public statusCode: number, message: string, public code?: string) {
    super(message);
  }
}

export type ApiEvent = APIGatewayProxyEventV2WithJWTAuthorizer;

/** userId always comes from the verified Cognito JWT, never from the request body. */
export function getUserId(event: ApiEvent): string {
  const sub = event.requestContext.authorizer?.jwt?.claims?.sub;
  if (typeof sub !== "string" || !sub) throw new HttpError(401, "Unauthenticated");
  return sub;
}

export function json(statusCode: number, body: unknown): APIGatewayProxyResultV2 {
  return {
    statusCode,
    headers: { "content-type": "application/json" },
    body: JSON.stringify(body),
  };
}

export function parseBody<T>(event: ApiEvent, schema: { parse: (v: unknown) => T }): T {
  let raw: unknown;
  try {
    raw = event.body ? JSON.parse(event.body) : {};
  } catch {
    throw new HttpError(400, "Body must be valid JSON");
  }
  try {
    return schema.parse(raw);
  } catch (e) {
    throw new HttpError(400, "Invalid input", "VALIDATION");
  }
}

/** Wrap a handler so thrown HttpErrors map to responses and unknown errors become a generic 500. */
export function handler(fn: (event: ApiEvent) => Promise<APIGatewayProxyResultV2>) {
  return async (event: ApiEvent): Promise<APIGatewayProxyResultV2> => {
    try {
      return await fn(event);
    } catch (err) {
      if (err instanceof HttpError) {
        return json(err.statusCode, { error: err.message, code: err.code });
      }
      console.error(JSON.stringify({ level: "ERROR", message: "Unhandled error", err: String(err) }));
      return json(500, { error: "Internal error" });
    }
  };
}

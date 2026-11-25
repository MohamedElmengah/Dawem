import { HttpError } from "./http";

/**
 * Minimal multipart/form-data handling so the Lambda can forward an upload to the AI service
 * while replacing the `user_id` field with the one from the verified JWT. Bytes of the file
 * part are never decoded or copied, only re-framed.
 */

export interface MultipartInfo {
  fields: Record<string, string>;
  /** filename of the first file part, if any */
  fileName?: string;
}

function boundaryOf(contentType: string): string {
  const m = /boundary=(?:"([^"]+)"|([^;\s]+))/i.exec(contentType);
  const boundary = m?.[1] ?? m?.[2];
  if (!/^multipart\/form-data/i.test(contentType) || !boundary) {
    throw new HttpError(400, "Expected multipart/form-data", "VALIDATION");
  }
  return boundary;
}

/** Raw parts, each still holding its leading CRLF, headers, blank line, content and trailing CRLF. */
function splitParts(body: Buffer, boundary: string): Buffer[] {
  const delim = Buffer.from(`--${boundary}`);
  const parts: Buffer[] = [];
  let pos = body.indexOf(delim);
  if (pos === -1) throw new HttpError(400, "Malformed multipart body", "VALIDATION");
  for (;;) {
    const start = pos + delim.length;
    if (body.subarray(start, start + 2).toString("latin1") === "--") return parts;
    const next = body.indexOf(delim, start);
    if (next === -1) throw new HttpError(400, "Malformed multipart body", "VALIDATION");
    parts.push(body.subarray(start, next));
    pos = next;
  }
}

function headerBlock(part: Buffer): string {
  const end = part.indexOf("\r\n\r\n");
  return (end === -1 ? part : part.subarray(0, end)).toString("latin1");
}

function partName(part: Buffer): string | undefined {
  return /\bname="([^"]*)"/i.exec(headerBlock(part))?.[1];
}

export function readMultipart(body: Buffer, contentType: string): MultipartInfo {
  const info: MultipartInfo = { fields: {} };
  for (const part of splitParts(body, boundaryOf(contentType))) {
    const headers = headerBlock(part);
    const name = partName(part);
    if (!name) continue;
    const filename = /\bfilename="([^"]*)"/i.exec(headers)?.[1];
    if (filename !== undefined) {
      info.fileName ??= filename;
      continue;
    }
    const start = part.indexOf("\r\n\r\n") + 4;
    const value = part.subarray(start, Math.max(start, part.length - 2)).toString("utf8");
    info.fields[name] = value;
  }
  return info;
}

/** Remove any client-supplied `name` part and append one with `value` (always the last, authoritative). */
export function setFormField(body: Buffer, contentType: string, name: string, value: string): Buffer {
  if (/[\r\n"]/.test(name) || /[\r\n]/.test(value)) {
    throw new HttpError(400, "Invalid form field", "VALIDATION");
  }
  const boundary = boundaryOf(contentType);
  const delim = Buffer.from(`--${boundary}`);
  const kept = splitParts(body, boundary).filter((p) => partName(p) !== name);
  const ours = Buffer.from(`\r\nContent-Disposition: form-data; name="${name}"\r\n\r\n${value}\r\n`);
  const chunks: Buffer[] = [];
  for (const part of [...kept, ours]) chunks.push(delim, part);
  chunks.push(Buffer.from(`--${boundary}--\r\n`));
  return Buffer.concat(chunks);
}

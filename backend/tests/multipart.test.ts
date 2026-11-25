import { describe, it, expect } from "vitest";
import { readMultipart, setFormField } from "../shared/multipart";

const B = "----testboundary123";
const CT = `multipart/form-data; boundary=${B}`;

function body(parts: string[]): Buffer {
  return Buffer.from(parts.map((p) => `--${B}\r\n${p}\r\n`).join("") + `--${B}--\r\n`);
}
const field = (name: string, value: string) => `Content-Disposition: form-data; name="${name}"\r\n\r\n${value}`;
const file = (name: string, filename: string, content: string) =>
  `Content-Disposition: form-data; name="${name}"; filename="${filename}"\r\nContent-Type: text/plain\r\n\r\n${content}`;

describe("readMultipart", () => {
  it("reads text fields and the uploaded file name", () => {
    const info = readMultipart(body([field("file_id", "f1"), file("file", "notes.txt", "hello\r\nworld")]), CT);
    expect(info.fields).toEqual({ file_id: "f1" });
    expect(info.fileName).toBe("notes.txt");
  });

  it("rejects non-multipart content types and bodies without the boundary", () => {
    expect(() => readMultipart(Buffer.from("x"), "application/json")).toThrow();
    expect(() => readMultipart(Buffer.from("no boundary here"), CT)).toThrow();
  });
});

describe("setFormField", () => {
  it("replaces a client-supplied user_id so the JWT subject always wins", () => {
    const original = body([field("user_id", "attacker"), field("file_id", "f1"), file("file", "a.txt", "data")]);
    const out = setFormField(original, CT, "user_id", "real-user");
    const info = readMultipart(out, CT);
    expect(info.fields["user_id"]).toBe("real-user");
    expect(info.fields["file_id"]).toBe("f1");
    expect(out.toString()).not.toContain("attacker");
    expect(out.toString().match(/name="user_id"/g)).toHaveLength(1);
  });

  it("leaves file bytes untouched, including binary content", () => {
    const bin = Buffer.from([0, 255, 13, 10, 1, 2, 3, 128]);
    const original = Buffer.concat([
      Buffer.from(`--${B}\r\nContent-Disposition: form-data; name="file"; filename="a.pdf"\r\n\r\n`),
      bin,
      Buffer.from(`\r\n--${B}--\r\n`),
    ]);
    const out = setFormField(original, CT, "user_id", "u1");
    expect(out.includes(bin)).toBe(true);
  });

  it("refuses values that could break the framing", () => {
    expect(() => setFormField(body([field("a", "b")]), CT, "user_id", "x\r\ny")).toThrow();
  });
});

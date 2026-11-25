import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { ApiEvent } from "../shared/http";
import { ddb } from "../shared/ddb";
import {
  deleteFile,
  generatePlan,
  generateQuiz,
  indexFile,
  indexNotes,
  indexS3,
  submitQuiz,
} from "../functions/ai/handler";

vi.mock("../shared/ddb", () => ({ ddb: { send: vi.fn() }, isConditionFailure: (e: unknown) => (e as { name?: string })?.name === "ConditionalCheckFailedException" }));

const send = ddb.send as unknown as ReturnType<typeof vi.fn>;
type Cmd = { constructor: { name: string }; input: Record<string, any> };
const sent = (name: string) => send.mock.calls.map((c) => c[0] as Cmd).filter((c) => c.constructor.name === name);

const ev = (over: Record<string, unknown> = {}): ApiEvent =>
  ({
    requestContext: { authorizer: { jwt: { claims: { sub: "user-1" } } } },
    headers: {},
    pathParameters: {},
    ...over,
  }) as unknown as ApiEvent;
const body = (o: unknown) => JSON.stringify(o);
const run = async (fn: (e: ApiEvent) => Promise<unknown>, e: ApiEvent) =>
  (await fn(e)) as { statusCode: number; body?: string };
const parse = (r: { body?: string }) => JSON.parse(r.body ?? "{}");
const aiOk = (o: unknown, status = 200) => new Response(JSON.stringify(o), { status });

let fetchMock: ReturnType<typeof vi.fn>;
const aiCall = (i = 0) => {
  const [url, init] = fetchMock.mock.calls[i]!;
  return { url: new URL(String(url)), init, json: typeof init.body === "string" ? JSON.parse(init.body) : undefined };
};

beforeEach(() => {
  process.env.TABLE_NAME = "t";
  process.env.AI_SERVICE_URL = "https://ai.test";
  process.env.AI_SERVICE_API_KEY = "k";
  process.env.FILES_BUCKET = "my-bucket";
  send.mockReset();
  send.mockResolvedValue({});
  fetchMock = vi.fn();
  vi.stubGlobal("fetch", fetchMock);
});
afterEach(() => vi.unstubAllGlobals());

const src = { chunk_id: "c1", file_id: "f1", file_name: "book.pdf", page_number: 3, snippet: "s", score: 0.8 };

describe("POST /files/index", () => {
  const B = "XBOUNDARY";
  const ct = `multipart/form-data; boundary=${B}`;
  const part = (h: string, v: string) => `--${B}\r\n${h}\r\n\r\n${v}\r\n`;
  const multipart =
    part('Content-Disposition: form-data; name="user_id"', "attacker") +
    part('Content-Disposition: form-data; name="file_id"', "f1") +
    part('Content-Disposition: form-data; name="file"; filename="notes.txt"', "hello") +
    `--${B}--\r\n`;

  it("forwards the upload with the JWT user id (never the client's) and records the file", async () => {
    fetchMock.mockResolvedValue(aiOk({ file_id: "f1", chunk_count: 4 }));
    const r = await run(indexFile, ev({ headers: { "content-type": ct }, body: Buffer.from(multipart).toString("base64"), isBase64Encoded: true }));
    expect(r.statusCode).toBe(200);
    expect(parse(r)).toEqual({ fileId: "f1", chunkCount: 4 });
    const { url, init } = aiCall();
    expect(url.pathname).toBe("/files/index");
    expect(init.headers["content-type"]).toBe(ct);
    const forwarded = Buffer.from(init.body).toString();
    expect(forwarded).toContain("user-1");
    expect(forwarded).not.toContain("attacker");
    expect(sent("PutCommand")[0]!.input.Item).toMatchObject({
      PK: "USER#user-1", SK: "FILE#f1", fileName: "notes.txt", chunkCount: 4, origin: "upload",
    });
  });

  it("rejects a missing file_id without calling the AI service", async () => {
    const noId = part('Content-Disposition: form-data; name="file"; filename="a.txt"', "x") + `--${B}--\r\n`;
    const r = await run(indexFile, ev({ headers: { "content-type": ct }, body: noId }));
    expect(r.statusCode).toBe(400);
    expect(fetchMock).not.toHaveBeenCalled();
  });
});

describe("POST /files/index-s3", () => {
  it("uses the configured bucket and only accepts keys under the caller's prefix", async () => {
    fetchMock.mockResolvedValue(aiOk({ file_id: "f2", chunk_count: 9 }));
    const r = await run(indexS3, ev({ body: body({ fileId: "f2", s3Key: "uploads/user-1/f2/a.pdf" }) }));
    expect(r.statusCode).toBe(200);
    expect(aiCall().json).toEqual({ user_id: "user-1", file_id: "f2", bucket: "my-bucket", s3_key: "uploads/user-1/f2/a.pdf" });
  });

  it.each(["uploads/user-2/f2/a.pdf", "uploads/user-1/../user-2/a.pdf", "other/a.pdf"])("rejects key %s", async (s3Key) => {
    const r = await run(indexS3, ev({ body: body({ fileId: "f2", s3Key }) }));
    expect(r.statusCode).toBe(403);
    expect(fetchMock).not.toHaveBeenCalled();
  });
});

describe("POST /files/index-notes", () => {
  it("maps camelCase input to the AI contract", async () => {
    fetchMock.mockResolvedValue(aiOk({ file_id: "n1", chunk_count: 1 }));
    const r = await run(indexNotes, ev({ body: body({ fileId: "n1", fileName: "My notes", text: "abc" }) }));
    expect(r.statusCode).toBe(200);
    expect(aiCall().json).toEqual({ user_id: "user-1", file_id: "n1", file_name: "My notes", text: "abc" });
  });
});

describe("DELETE /files/{fileId}", () => {
  const del = () => run(deleteFile, ev({ pathParameters: { fileId: "f1" } }));

  it("deletes in the AI service and removes the record", async () => {
    fetchMock.mockResolvedValue(new Response(null, { status: 204 }));
    expect((await del()).statusCode).toBe(204);
    expect(aiCall().url.search).toBe("?user_id=user-1");
    expect(sent("DeleteCommand")[0]!.input.Key).toEqual({ PK: "USER#user-1", SK: "FILE#f1" });
  });

  it("is idempotent when the AI service no longer has the file but a record exists", async () => {
    fetchMock.mockResolvedValue(aiOk({ code: "file_not_found", detail: "File not found" }, 404));
    send.mockResolvedValue({ Attributes: { fileId: "f1" } });
    expect((await del()).statusCode).toBe(204);
  });

  it("answers 404 when neither side knows the file", async () => {
    fetchMock.mockResolvedValue(aiOk({ code: "file_not_found", detail: "File not found" }, 404));
    expect((await del()).statusCode).toBe(404);
  });

  it("keeps the record when the AI service answers 403", async () => {
    fetchMock.mockResolvedValue(aiOk({ code: "forbidden", detail: "no" }, 403));
    expect((await del()).statusCode).toBe(403);
    expect(sent("DeleteCommand")).toHaveLength(0);
  });
});

describe("POST /plans/generate", () => {
  const goal = { goalId: "g1", title: "Learn Python", description: "basics", durationDays: 3, dailyMinutes: 60, level: "beginner", startDate: "2026-11-30" };
  const plan = {
    plan_id: "plan-1", title: "Python in 3 days",
    tasks: [
      { task_id: "aaaaaaaa1111", day_number: 1, title: "Read ch1", description: "d", kind: "read", duration_minutes: 30, sources: [src] },
      { task_id: "bbbbbbbb2222", day_number: 3, title: "Quiz", description: "d", kind: "quiz", duration_minutes: 20, sources: [src] },
    ],
  };
  const withGoal = (g: Record<string, unknown> | undefined, onUpdate?: () => unknown) =>
    send.mockImplementation(async (cmd: Cmd) => {
      if (cmd.constructor.name === "GetCommand") return { Item: g };
      if (cmd.constructor.name === "UpdateCommand" && onUpdate) return onUpdate();
      return {};
    });
  const call = () => run(generatePlan, ev({ body: body({ goalId: "g1", fileIds: ["f1"] }) }));

  it("calls the AI service with the goal's data, stores one task item per AI task, marks the goal ready", async () => {
    withGoal(goal);
    fetchMock.mockResolvedValue(aiOk(plan));
    const r = await call();
    expect(r.statusCode).toBe(201);

    expect(aiCall().url.pathname).toBe("/plans/generate");
    expect(aiCall().json).toEqual({
      user_id: "user-1", file_ids: ["f1"], goal: "Learn Python: basics", duration_days: 3, daily_minutes: 60, level: "beginner",
    });

    const items = sent("BatchWriteCommand")[0]!.input.RequestItems.t.map((x: any) => x.PutRequest.Item);
    expect(items).toHaveLength(2);
    expect(items[0]).toMatchObject({ PK: "GOAL#g1", SK: "TASK#2026-11-30-aaaaaaaa", date: "2026-11-30", planId: "plan-1", source: "ai" });
    expect(items[1]).toMatchObject({ date: "2026-12-02", estimatedMinutes: 20 });

    const upd = sent("UpdateCommand")[0]!.input;
    expect(upd.Key).toEqual({ PK: "USER#user-1", SK: "GOAL#g1" });
    expect(upd.ConditionExpression).toContain("attribute_not_exists(planId)");
    expect(upd.ExpressionAttributeValues[":s"]).toBe("PLAN_READY");

    const out = parse(r);
    expect(out).toMatchObject({ goalId: "g1", planId: "plan-1", status: "PLAN_READY" });
    expect(out.tasks).toHaveLength(2);
    expect(out.tasks[0].sourceRefs[0]).toMatchObject({ fileId: "f1", pageNumber: 3 });
    expect(JSON.stringify(out)).not.toContain("USER#");
  });

  it("404s for a goal that is not the caller's, without spending an AI call", async () => {
    withGoal(undefined);
    expect((await call()).statusCode).toBe(404);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("409s when the goal already has a plan, without an AI call", async () => {
    withGoal({ ...goal, planId: "old" });
    const r = await call();
    expect(r.statusCode).toBe(409);
    expect(parse(r).code).toBe("PLAN_EXISTS");
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("refuses budgets the AI service cannot plan for (<10 min/day)", async () => {
    withGoal({ ...goal, dailyMinutes: 5 });
    expect((await call()).statusCode).toBe(400);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("passes AI errors through (422 insufficient context) and writes nothing", async () => {
    withGoal(goal);
    fetchMock.mockResolvedValue(aiOk({ code: "insufficient_context", detail: "not enough" }, 422));
    const r = await call();
    expect(r.statusCode).toBe(422);
    expect(parse(r).code).toBe("insufficient_context");
    expect(sent("BatchWriteCommand")).toHaveLength(0);
  });

  it("502s when the AI response does not match the contract", async () => {
    withGoal(goal);
    fetchMock.mockResolvedValue(aiOk({ plan_id: "x" }));
    const r = await call();
    expect(r.statusCode).toBe(502);
    expect(sent("BatchWriteCommand")).toHaveLength(0);
  });

  it("rolls back its tasks when a concurrent request already attached a plan", async () => {
    withGoal(goal, () => {
      throw Object.assign(new Error("lost the race"), { name: "ConditionalCheckFailedException" });
    });
    fetchMock.mockResolvedValue(aiOk(plan));
    const r = await call();
    expect(r.statusCode).toBe(409);
    const batches = sent("BatchWriteCommand");
    expect(batches).toHaveLength(2);
    expect(batches[1]!.input.RequestItems.t.map((x: any) => x.DeleteRequest.Key.SK)).toEqual([
      "TASK#2026-11-30-aaaaaaaa",
      "TASK#2026-12-02-bbbbbbbb",
    ]);
  });
});

describe("quizzes", () => {
  const aiQuiz = {
    quiz_id: "quiz-1", title: "Quiz",
    mcqs: [
      { question_id: "m1", question: "Question one?", options: ["a", "b", "c", "d"], correct_option_index: 1, explanation: "why-b", difficulty: "easy", sources: [src] },
      { question_id: "m2", question: "Question two?", options: ["a", "b", "c", "d"], correct_option_index: 3, explanation: "why-d", difficulty: "medium", sources: [src] },
    ],
    recall_questions: [{ question_id: "r1", question: "Recall one?", model_answer: "the answer", sources: [src] }],
  };

  it("stores the answer key server-side and returns the quiz without it", async () => {
    fetchMock.mockResolvedValue(aiOk(aiQuiz));
    const r = await run(generateQuiz, ev({ body: body({ fileIds: ["f1"], topic: "loops", numMcqs: 2, numRecall: 1 }) }));
    expect(r.statusCode).toBe(201);
    expect(aiCall().json).toEqual({ user_id: "user-1", file_ids: ["f1"], topic: "loops", num_mcqs: 2, num_recall: 1 });

    const stored = sent("PutCommand")[0]!.input.Item;
    expect(stored).toMatchObject({ PK: "USER#user-1", SK: "QUIZ#quiz-1" });
    expect(stored.mcqs[0]).toMatchObject({ correctOptionIndex: 1, explanation: "why-b" });

    const text = r.body!;
    expect(text).not.toContain("why-b");
    expect(text).not.toContain("the answer");
    expect(parse(r)).toMatchObject({ quizId: "quiz-1", mcqs: [{ questionId: "m1" }, { questionId: "m2" }] });
  });

  const stored = {
    mcqs: [
      { questionId: "m1", correctOptionIndex: 1, explanation: "why-b" },
      { questionId: "m2", correctOptionIndex: 3, explanation: "why-d" },
    ],
    recallQuestions: [{ questionId: "r1", modelAnswer: "the answer" }],
  };
  const submit = (b: unknown) => run(submitQuiz, ev({ pathParameters: { quizId: "quiz-1" }, body: body(b) }));

  it("grades server-side, persists the attempt and reveals answers", async () => {
    send.mockImplementation(async (cmd: Cmd) => (cmd.constructor.name === "GetCommand" ? { Item: stored } : {}));
    const r = await submit({ answers: [{ questionId: "m1", selectedOptionIndex: 1 }, { questionId: "m2", selectedOptionIndex: 0 }], recallAnswers: [{ questionId: "r1", answer: "mine" }] });
    expect(r.statusCode).toBe(200);
    const out = parse(r);
    expect(out).toMatchObject({ score: 1, total: 2, percentage: 50 });
    expect(out.mcqResults[1]).toMatchObject({ isCorrect: false, correctOptionIndex: 3, explanation: "why-d" });
    expect(out.recallReview[0]).toEqual({ questionId: "r1", modelAnswer: "the answer", userAnswer: "mine" });
    const saved = sent("PutCommand")[0]!.input.Item;
    expect(saved).toMatchObject({ entity: "QuizResult", userId: "user-1", quizId: "quiz-1", score: 1, total: 2 });
    expect(saved.SK).toBe(`QUIZRESULT#quiz-1#${out.resultId}`);
  });

  it("404s for a quiz the caller does not own and 400s for unknown question ids", async () => {
    send.mockResolvedValue({});
    expect((await submit({ answers: [] })).statusCode).toBe(404);
    send.mockImplementation(async () => ({ Item: stored }));
    expect((await submit({ answers: [{ questionId: "zzz", selectedOptionIndex: 0 }] })).statusCode).toBe(400);
    expect(sent("PutCommand")).toHaveLength(0);
  });
});

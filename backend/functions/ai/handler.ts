import { randomUUID } from "node:crypto";
import {
  BatchWriteCommand,
  DeleteCommand,
  GetCommand,
  PutCommand,
  QueryCommand,
  UpdateCommand,
} from "@aws-sdk/lib-dynamodb";
import { z } from "zod";
import { ddb, isConditionFailure } from "../../shared/ddb";
import { keys, TABLE_NAME } from "../../shared/keys";
import { handler, json, parseBody, getUserId, HttpError, type ApiEvent } from "../../shared/http";
import { callAi } from "../../shared/aiClient";
import { readMultipart, setFormField } from "../../shared/multipart";
import { IndexResponse, Quiz, StudyPlan } from "../../shared/aiContracts";
import { gradeQuiz, planToTaskItems, quizForClient } from "../../shared/aiMapping";

const FileId = z.string().regex(/^[A-Za-z0-9._-]{1,128}$/, "invalid fileId");
const FileIds = z.array(FileId).min(1).max(20);

/** Validate an AI response; a shape mismatch means the AI service drifted, so it is a 502, not a client error. */
function parseAi<T>(schema: z.ZodType<T>, value: unknown): T {
  const r = schema.safeParse(value);
  if (!r.success) {
    console.error(JSON.stringify({ level: "ERROR", message: "AI response failed validation" }));
    throw new HttpError(502, "AI service returned an unexpected response", "AI_BAD_RESPONSE");
  }
  return r.data;
}

async function saveFileRecord(
  userId: string,
  r: { fileId: string; fileName: string; chunkCount: number; origin: "upload" | "s3" | "notes" }
) {
  const now = new Date().toISOString();
  await ddb.send(
    new PutCommand({
      TableName: TABLE_NAME(),
      Item: {
        ...keys.learningFile(userId, r.fileId),
        entity: "LearningFile",
        userId,
        ...r,
        status: "INDEXED",
        updatedAt: now,
        createdAt: now,
      },
    })
  );
}

async function batchWrite(requests: Record<string, unknown>[]) {
  const table = TABLE_NAME();
  for (let i = 0; i < requests.length; i += 25) {
    let pending = requests.slice(i, i + 25);
    for (let attempt = 0; attempt < 5 && pending.length > 0; attempt++) {
      const res = await ddb.send(
        new BatchWriteCommand({ RequestItems: { [table]: pending as never } })
      );
      pending = (res.UnprocessedItems?.[table] as Record<string, unknown>[] | undefined) ?? [];
    }
    if (pending.length > 0) throw new Error("DynamoDB batch write did not complete");
  }
}

// ---------------------------------------------------------------- files

/** POST /files/index  (multipart: file, file_id, optional file_name). user_id is injected from the JWT. */
export const indexFile = handler(async (event: ApiEvent) => {
  const userId = getUserId(event);
  const contentType = event.headers["content-type"] ?? event.headers["Content-Type"] ?? "";
  if (!event.body) throw new HttpError(400, "Empty body", "VALIDATION");
  const raw = Buffer.from(event.body, event.isBase64Encoded ? "base64" : "utf8");

  const info = readMultipart(raw, contentType);
  const fileId = FileId.safeParse(info.fields["file_id"]);
  if (!fileId.success) throw new HttpError(400, "file_id is required (letters, digits, . _ -)", "VALIDATION");

  const forwarded = setFormField(raw, contentType, "user_id", userId);
  const out = parseAi(
    IndexResponse,
    await callAi({ method: "POST", path: "/files/index", rawBody: forwarded, contentType })
  );
  await saveFileRecord(userId, {
    fileId: out.file_id,
    fileName: (info.fields["file_name"] || info.fileName || out.file_id).slice(0, 255),
    chunkCount: out.chunk_count,
    origin: "upload",
  });
  return json(200, { fileId: out.file_id, chunkCount: out.chunk_count });
});

const IndexS3 = z.object({
  fileId: FileId,
  s3Key: z.string().min(1).max(1024),
  fileName: z.string().min(1).max(255).optional(),
});

/** POST /files/index-s3. The bucket is server-side config and the key must sit under the caller's own prefix. */
export const indexS3 = handler(async (event) => {
  const userId = getUserId(event);
  const input = parseBody(event, IndexS3);
  const bucket = process.env.FILES_BUCKET;
  if (!bucket) throw new HttpError(503, "File storage is not configured", "AI_NOT_CONFIGURED");
  if (!input.s3Key.startsWith(`uploads/${userId}/`) || input.s3Key.includes("..")) {
    throw new HttpError(403, "s3Key must be under your own uploads/ prefix", "FORBIDDEN_KEY");
  }
  const out = parseAi(
    IndexResponse,
    await callAi({
      method: "POST",
      path: "/files/index-s3",
      json: {
        user_id: userId,
        file_id: input.fileId,
        bucket,
        s3_key: input.s3Key,
        ...(input.fileName ? { file_name: input.fileName } : {}),
      },
    })
  );
  await saveFileRecord(userId, {
    fileId: out.file_id,
    fileName: input.fileName ?? input.s3Key.split("/").pop() ?? out.file_id,
    chunkCount: out.chunk_count,
    origin: "s3",
  });
  return json(200, { fileId: out.file_id, chunkCount: out.chunk_count });
});

const IndexNotes = z.object({
  fileId: FileId,
  fileName: z.string().trim().min(1).max(255),
  text: z.string().trim().min(1).max(500_000),
});

/** POST /files/index-notes */
export const indexNotes = handler(async (event) => {
  const userId = getUserId(event);
  const input = parseBody(event, IndexNotes);
  const out = parseAi(
    IndexResponse,
    await callAi({
      method: "POST",
      path: "/files/index-notes",
      json: { user_id: userId, file_id: input.fileId, file_name: input.fileName, text: input.text },
    })
  );
  await saveFileRecord(userId, {
    fileId: out.file_id,
    fileName: input.fileName,
    chunkCount: out.chunk_count,
    origin: "notes",
  });
  return json(200, { fileId: out.file_id, chunkCount: out.chunk_count });
});

/** GET /files */
export const listFiles = handler(async (event) => {
  const userId = getUserId(event);
  const res = await ddb.send(
    new QueryCommand({
      TableName: TABLE_NAME(),
      KeyConditionExpression: "PK = :pk AND begins_with(SK, :sk)",
      ExpressionAttributeValues: { ":pk": `USER#${userId}`, ":sk": "FILE#" },
    })
  );
  return json(200, {
    files: (res.Items ?? []).map((i) => ({
      fileId: i.fileId,
      fileName: i.fileName,
      chunkCount: i.chunkCount,
      status: i.status,
      createdAt: i.createdAt,
    })),
  });
});

/** DELETE /files/{fileId}. Idempotent: a file the AI service no longer knows still has its record removed. */
export const deleteFile = handler(async (event) => {
  const userId = getUserId(event);
  const parsed = FileId.safeParse(event.pathParameters?.fileId);
  if (!parsed.success) throw new HttpError(400, "invalid fileId", "VALIDATION");
  const fileId = parsed.data;

  let aiKnewIt = true;
  try {
    await callAi({ method: "DELETE", path: `/files/${encodeURIComponent(fileId)}`, query: { user_id: userId } });
  } catch (err) {
    if (err instanceof HttpError && err.statusCode === 404) aiKnewIt = false;
    else throw err;
  }
  const removed = await ddb.send(
    new DeleteCommand({
      TableName: TABLE_NAME(),
      Key: keys.learningFile(userId, fileId),
      ReturnValues: "ALL_OLD",
    })
  );
  if (!aiKnewIt && !removed.Attributes) throw new HttpError(404, "File not found", "file_not_found");
  return { statusCode: 204, body: "" };
});

// ---------------------------------------------------------------- plans

const GeneratePlan = z.object({
  goalId: z.string().min(1).max(128),
  fileIds: FileIds,
  context: z.string().trim().max(2000).optional(),
});

/**
 * POST /plans/generate: generate a grounded plan for one of the caller's goals and persist its tasks.
 * Synchronous on purpose: the HTTP API gives integrations at most 30 s, so AI_TIMEOUT_MS defaults to 25 s.
 */
export const generatePlan = handler(async (event) => {
  const userId = getUserId(event);
  const input = parseBody(event, GeneratePlan);
  const table = TABLE_NAME();

  const goalRes = await ddb.send(new GetCommand({ TableName: table, Key: keys.goal(userId, input.goalId) }));
  const goal = goalRes.Item;
  if (!goal) throw new HttpError(404, "Goal not found");
  if (goal.planId) throw new HttpError(409, "This goal already has a plan", "PLAN_EXISTS");
  if (typeof goal.dailyMinutes !== "number" || goal.dailyMinutes < 10) {
    throw new HttpError(400, "AI plans need dailyMinutes of at least 10", "VALIDATION");
  }

  const goalText = [goal.title, goal.description].filter(Boolean).join(": ").slice(0, 500);
  const plan = parseAi(
    StudyPlan,
    await callAi({
      method: "POST",
      path: "/plans/generate",
      json: {
        user_id: userId,
        file_ids: input.fileIds,
        goal: goalText,
        duration_days: goal.durationDays,
        daily_minutes: goal.dailyMinutes,
        level: goal.level,
        ...(input.context ? { context: input.context } : {}),
      },
    })
  );

  const nowIso = new Date().toISOString();
  const items = planToTaskItems(
    plan,
    { userId, goalId: input.goalId, startDate: goal.startDate, nowIso },
    keys.task
  );
  await batchWrite(items.map((Item) => ({ PutRequest: { Item } })));

  try {
    await ddb.send(
      new UpdateCommand({
        TableName: table,
        Key: keys.goal(userId, input.goalId),
        UpdateExpression: "SET #s = :s, planId = :p, planTitle = :t, fileIds = :f, planGeneratedAt = :n",
        // Two concurrent generations must not both win: the loser removes the tasks it just wrote.
        ConditionExpression: "attribute_exists(PK) AND attribute_not_exists(planId)",
        ExpressionAttributeNames: { "#s": "status" },
        ExpressionAttributeValues: {
          ":s": "PLAN_READY",
          ":p": plan.plan_id,
          ":t": plan.title,
          ":f": input.fileIds,
          ":n": nowIso,
        },
      })
    );
  } catch (err) {
    if (isConditionFailure(err)) {
      await batchWrite(
        items.map((i) => ({ DeleteRequest: { Key: { PK: i.PK, SK: i.SK } } }))
      );
      throw new HttpError(409, "This goal already has a plan", "PLAN_EXISTS");
    }
    throw err;
  }

  return json(201, {
    goalId: input.goalId,
    planId: plan.plan_id,
    title: plan.title,
    status: "PLAN_READY",
    tasks: items.map(({ PK, SK, entity, userId: _u, ...task }) => task),
  });
});

// ---------------------------------------------------------------- quizzes

const GenerateQuiz = z.object({
  fileIds: FileIds,
  topic: z.string().trim().max(300).optional(),
  numMcqs: z.number().int().min(1).max(30).optional(),
  numRecall: z.number().int().min(0).max(10).optional(),
});

/** POST /quizzes/generate: the full quiz (with answers) is stored; the app receives it without answers. */
export const generateQuiz = handler(async (event) => {
  const userId = getUserId(event);
  const input = parseBody(event, GenerateQuiz);
  const quiz = parseAi(
    Quiz,
    await callAi({
      method: "POST",
      path: "/quizzes/generate",
      json: {
        user_id: userId,
        file_ids: input.fileIds,
        ...(input.topic ? { topic: input.topic } : {}),
        ...(input.numMcqs !== undefined ? { num_mcqs: input.numMcqs } : {}),
        ...(input.numRecall !== undefined ? { num_recall: input.numRecall } : {}),
      },
    })
  );
  const quizId = quiz.quiz_id;
  await ddb.send(
    new PutCommand({
      TableName: TABLE_NAME(),
      Item: {
        ...keys.quiz(userId, quizId),
        entity: "Quiz",
        quizId,
        userId,
        title: quiz.title,
        fileIds: input.fileIds,
        topic: input.topic,
        mcqs: quiz.mcqs.map((q) => ({
          questionId: q.question_id,
          question: q.question,
          options: q.options,
          correctOptionIndex: q.correct_option_index,
          explanation: q.explanation,
          difficulty: q.difficulty,
        })),
        recallQuestions: quiz.recall_questions.map((q) => ({
          questionId: q.question_id,
          question: q.question,
          modelAnswer: q.model_answer,
        })),
        createdAt: new Date().toISOString(),
      },
      ConditionExpression: "attribute_not_exists(PK)",
    })
  );
  return json(201, quizForClient(quizId, quiz.title, quiz));
});

const SubmitQuiz = z.object({
  answers: z
    .array(z.object({ questionId: z.string().min(1), selectedOptionIndex: z.number().int().min(0).max(3) }))
    .max(30),
  recallAnswers: z
    .array(z.object({ questionId: z.string().min(1), answer: z.string().max(2000) }))
    .max(10)
    .optional(),
});

/** POST /quizzes/{quizId}/submit: grade MCQs server-side, store the attempt, reveal answers + explanations. */
export const submitQuiz = handler(async (event) => {
  const userId = getUserId(event);
  const quizId = event.pathParameters?.quizId;
  if (!quizId) throw new HttpError(400, "quizId required", "VALIDATION");
  const input = parseBody(event, SubmitQuiz);

  const res = await ddb.send(new GetCommand({ TableName: TABLE_NAME(), Key: keys.quiz(userId, quizId) }));
  if (!res.Item) throw new HttpError(404, "Quiz not found");
  const mcqs = res.Item.mcqs as { questionId: string; correctOptionIndex: number; explanation: string }[];
  const recall = (res.Item.recallQuestions ?? []) as { questionId: string; modelAnswer: string }[];

  const known = new Set(mcqs.map((q) => q.questionId));
  if (input.answers.some((a) => !known.has(a.questionId))) {
    throw new HttpError(400, "Answer for an unknown question", "VALIDATION");
  }

  const graded = gradeQuiz(mcqs, input.answers);
  const resultId = randomUUID();
  const submittedAt = new Date().toISOString();
  const typed = new Map((input.recallAnswers ?? []).map((a) => [a.questionId, a.answer]));
  const recallReview = recall.map((q) => ({
    questionId: q.questionId,
    modelAnswer: q.modelAnswer,
    userAnswer: typed.get(q.questionId) ?? null,
  }));

  await ddb.send(
    new PutCommand({
      TableName: TABLE_NAME(),
      Item: {
        ...keys.quizResult(userId, quizId, resultId),
        entity: "QuizResult",
        userId,
        quizId,
        resultId,
        score: graded.score,
        total: graded.total,
        percentage: graded.percentage,
        answers: input.answers,
        recallAnswers: input.recallAnswers ?? [],
        submittedAt,
      },
    })
  );
  return json(200, {
    quizId,
    resultId,
    score: graded.score,
    total: graded.total,
    percentage: graded.percentage,
    mcqResults: graded.mcqResults,
    recallReview,
    submittedAt,
  });
});

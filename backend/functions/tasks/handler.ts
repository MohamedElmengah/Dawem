import { randomUUID } from "node:crypto";
import { GetCommand, PutCommand, TransactWriteCommand } from "@aws-sdk/lib-dynamodb";
import { z } from "zod";
import { ddb, isConditionFailure } from "../../shared/ddb";
import { keys, TABLE_NAME } from "../../shared/keys";
import { handler, json, getUserId, parseBody, HttpError } from "../../shared/http";
import { requireProfile } from "../../shared/profile";
import { xpIncrement } from "../../shared/tx";
import { applyCompletion, INITIAL_STREAK, toLocalDate, type StreakState } from "../../shared/domain/streak";
import { xpFor, levelForXp } from "../../shared/domain/xp";

const CreateTask = z.object({
  title: z.string().trim().min(1).max(200),
  date: z.string().regex(/^\d{4}-\d{2}-\d{2}$/),
  estimatedMinutes: z.number().int().min(1).max(600).optional(),
});

/** POST /goals/{goalId}/tasks: manual task. (AI plan generation will write the same item shape.) */
export const create = handler(async (event) => {
  const userId = getUserId(event);
  const goalId = event.pathParameters?.goalId;
  if (!goalId) throw new HttpError(400, "goalId required");
  const input = parseBody(event, CreateTask);

  const goal = await ddb.send(new GetCommand({ TableName: TABLE_NAME(), Key: keys.goal(userId, goalId) }));
  if (!goal.Item) throw new HttpError(404, "Goal not found");

  const taskId = `${input.date}-${randomUUID().slice(0, 8)}`;
  await ddb.send(
    new PutCommand({
      TableName: TABLE_NAME(),
      Item: {
        ...keys.task(goalId, taskId),
        entity: "Task",
        taskId,
        goalId,
        userId,
        ...input,
        createdAt: new Date().toISOString(),
      },
      ConditionExpression: "attribute_not_exists(PK)",
    })
  );
  return json(201, { taskId, goalId, date: input.date });
});

/**
 * POST /tasks/{taskId}/complete?goalId=...
 * One TransactWriteItems: completion marker (conditional => idempotent), streak, XP ledger, XP increment.
 * A repeated request fails the condition on the completion item and returns 409.
 * Badges and challenge progress are NOT done here: the DynamoDB Streams consumer reacts to the
 * Completion item this transaction writes.
 */
export const complete = handler(async (event) => {
  const userId = getUserId(event);
  const taskId = event.pathParameters?.taskId;
  const goalId = event.queryStringParameters?.goalId;
  if (!taskId || !goalId) throw new HttpError(400, "taskId and goalId required");

  const table = TABLE_NAME();
  const profile = await requireProfile(userId);
  const now = new Date();
  const today = toLocalDate(now, profile.timezone);

  // Ownership (goal key includes userId) and existence of the task: no XP for made-up task ids.
  const [goal, task] = await Promise.all([
    ddb.send(new GetCommand({ TableName: table, Key: keys.goal(userId, goalId) })),
    ddb.send(new GetCommand({ TableName: table, Key: keys.task(goalId, taskId) })),
  ]);
  if (!goal.Item) throw new HttpError(403, "Not your goal");
  if (!task.Item) throw new HttpError(404, "Task not found");
  if (task.Item.date > today) throw new HttpError(409, "Task is scheduled for a future day", "FUTURE_TASK");

  const streakRes = await ddb.send(new GetCommand({ TableName: table, Key: keys.streak(userId), ConsistentRead: true }));
  const prevStreak: StreakState = (streakRes.Item as StreakState | undefined) ?? INITIAL_STREAK;
  const { state: nextStreak, freezesConsumed } = applyCompletion(prevStreak, today);
  const gained = xpFor("TASK_COMPLETED");
  const nowIso = now.toISOString();

  try {
    await ddb.send(
      new TransactWriteCommand({
        TransactItems: [
          {
            Put: {
              TableName: table,
              Item: {
                ...keys.completion(userId, goalId, taskId),
                entity: "Completion",
                userId,
                taskId,
                goalId,
                date: today,
                completedAt: nowIso,
              },
              ConditionExpression: "attribute_not_exists(PK)",
            },
          },
          {
            Put: {
              TableName: table,
              Item: { ...keys.streak(userId), entity: "Streak", userId, ...nextStreak },
              // Optimistic concurrency: only write if streak is unchanged since we read it.
              ...(streakRes.Item
                ? {
                    ConditionExpression: "lastCompletedDate = :last AND currentStreak = :cur",
                    ExpressionAttributeValues: {
                      ":last": prevStreak.lastCompletedDate,
                      ":cur": prevStreak.currentStreak,
                    },
                  }
                : { ConditionExpression: "attribute_not_exists(PK)" }),
            },
          },
          {
            Put: {
              TableName: table,
              Item: {
                ...keys.xpLedger(userId, `task#${goalId}#${taskId}`),
                entity: "XpLedger",
                userId,
                event: "TASK_COMPLETED",
                amount: gained,
                createdAt: nowIso,
              },
              ConditionExpression: "attribute_not_exists(PK)",
            },
          },
          xpIncrement(userId, gained, nowIso, 1),
        ],
      })
    );
  } catch (err) {
    if (isConditionFailure(err)) {
      throw new HttpError(409, "Task already completed or concurrent update; retry", "CONFLICT");
    }
    throw err;
  }

  const xpRes = await ddb.send(new GetCommand({ TableName: table, Key: keys.xp(userId), ConsistentRead: true }));
  const totalXP: number = xpRes.Item?.totalXP ?? gained;
  return json(200, {
    taskId,
    date: today,
    xpGained: gained,
    totalXP,
    level: levelForXp(totalXP),
    streak: nextStreak,
    freezesConsumed,
  });
});

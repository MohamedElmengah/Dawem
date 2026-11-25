import { QueryCommand } from "@aws-sdk/lib-dynamodb";
import { batchGetKeys, ddb } from "./ddb";
import { keys, TABLE_NAME } from "./keys";

export interface TodayTask {
  taskId: string;
  goalId: string;
  goalTitle: string;
  title: string;
  date: string;
  estimatedMinutes?: number;
  completed: boolean;
  // Present on AI-generated tasks (and on manual tasks that set them).
  description?: string;
  dayNumber?: number;
  kind?: string;
  durationMinutes?: number;
  sourceRefs?: unknown[];
}

/** Today's tasks across the user's active goals, with a completed flag. `today` is in the user's timezone. */
export async function getTodayTasks(userId: string, today: string): Promise<TodayTask[]> {
  const table = TABLE_NAME();
  const goalsRes = await ddb.send(
    new QueryCommand({
      TableName: table,
      KeyConditionExpression: "PK = :pk AND begins_with(SK, :sk)",
      ExpressionAttributeValues: { ":pk": `USER#${userId}`, ":sk": "GOAL#" },
    })
  );
  const goals = (goalsRes.Items ?? []).filter(
    (g) => g.status !== "ARCHIVED" && (!g.startDate || g.startDate <= today)
  );

  const perGoal = await Promise.all(
    goals.map(async (g) => {
      const res = await ddb.send(
        new QueryCommand({
          TableName: table,
          KeyConditionExpression: "PK = :pk AND begins_with(SK, :sk)",
          ExpressionAttributeValues: { ":pk": `GOAL#${g.goalId}`, ":sk": `TASK#${today}` },
        })
      );
      return (res.Items ?? []).map((t) => ({ goal: g, task: t }));
    })
  );
  const rows = perGoal.flat();
  if (rows.length === 0) return [];

  const done = await batchGetKeys(rows.map((r) => keys.completion(userId, r.goal.goalId, r.task.taskId)));
  const doneSet = new Set(done.map((d) => `${d.goalId}#${d.taskId}`));

  return rows.map(({ goal, task }) => ({
    taskId: task.taskId,
    goalId: goal.goalId,
    goalTitle: goal.title,
    title: task.title,
    date: task.date,
    estimatedMinutes: task.estimatedMinutes,
    description: task.description,
    dayNumber: task.dayNumber,
    kind: task.kind,
    durationMinutes: task.durationMinutes,
    sourceRefs: task.sourceRefs,
    completed: doneSet.has(`${goal.goalId}#${task.taskId}`),
  }));
}

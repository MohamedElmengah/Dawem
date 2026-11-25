import { GetCommand, QueryCommand } from "@aws-sdk/lib-dynamodb";
import { ddb } from "../../shared/ddb";
import { keys, TABLE_NAME } from "../../shared/keys";
import { handler, json, getUserId } from "../../shared/http";
import { requireProfile } from "../../shared/profile";
import { getTodayTasks } from "../../shared/todayTasks";
import { displayStreak, INITIAL_STREAK, toLocalDate, type StreakState } from "../../shared/domain/streak";
import { levelForXp, xpThreshold } from "../../shared/domain/xp";
import { BADGE_RULES } from "../../shared/domain/badges";

/** GET /tasks/today */
export const today = handler(async (event) => {
  const userId = getUserId(event);
  const profile = await requireProfile(userId);
  const date = toLocalDate(new Date(), profile.timezone);
  const tasks = await getTodayTasks(userId, date);
  return json(200, {
    date,
    total: tasks.length,
    completed: tasks.filter((t) => t.completed).length,
    tasks,
  });
});

/** GET /streak: the streak as it stands today (a lapsed streak reads 0 without waiting for a write). */
export const streak = handler(async (event) => {
  const userId = getUserId(event);
  const profile = await requireProfile(userId);
  const date = toLocalDate(new Date(), profile.timezone);
  const res = await ddb.send(new GetCommand({ TableName: TABLE_NAME(), Key: keys.streak(userId) }));
  const s: StreakState = (res.Item as StreakState | undefined) ?? INITIAL_STREAK;
  return json(200, {
    currentStreak: displayStreak(s, date),
    longestStreak: s.longestStreak,
    lastCompletedDate: s.lastCompletedDate,
    freezesRemaining: s.freezesRemaining,
    completedToday: s.lastCompletedDate === date,
  });
});

/** GET /xp */
export const xp = handler(async (event) => {
  const userId = getUserId(event);
  const res = await ddb.send(new GetCommand({ TableName: TABLE_NAME(), Key: keys.xp(userId) }));
  const totalXP: number = res.Item?.totalXP ?? 0;
  const level = levelForXp(totalXP);
  return json(200, {
    totalXP,
    level,
    xpIntoLevel: totalXP - xpThreshold(level),
    xpForNextLevel: xpThreshold(level + 1) - xpThreshold(level),
    totalCompletions: res.Item?.totalCompletions ?? 0,
  });
});

/** GET /badges: the full catalog, each marked earned or locked. */
export const badges = handler(async (event) => {
  const userId = getUserId(event);
  const res = await ddb.send(
    new QueryCommand({
      TableName: TABLE_NAME(),
      KeyConditionExpression: "PK = :pk AND begins_with(SK, :sk)",
      ExpressionAttributeValues: { ":pk": `USER#${userId}`, ":sk": "BADGE#" },
    })
  );
  const earned = new Map((res.Items ?? []).map((b) => [b.badgeId as string, b.awardedAt as string]));
  return json(200, {
    badges: BADGE_RULES.map((r) => ({
      badgeId: r.badgeId,
      name: r.name,
      description: r.description,
      earned: earned.has(r.badgeId),
      awardedAt: earned.get(r.badgeId) ?? null,
    })),
  });
});

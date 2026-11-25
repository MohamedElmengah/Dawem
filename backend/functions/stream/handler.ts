import type { DynamoDBBatchResponse, DynamoDBRecord, DynamoDBStreamEvent } from "aws-lambda";
import { unmarshall } from "@aws-sdk/util-dynamodb";
import type { AttributeValue } from "@aws-sdk/client-dynamodb";
import { GetCommand, QueryCommand, TransactWriteCommand } from "@aws-sdk/lib-dynamodb";
import { ddb, isConditionFailure } from "../../shared/ddb";
import { keys, TABLE_NAME } from "../../shared/keys";
import { xpIncrement } from "../../shared/tx";
import { xpFor } from "../../shared/domain/xp";
import { badgesToAward } from "../../shared/domain/badges";
import { isWithinWindow, reachedTarget } from "../../shared/domain/challenges";

/**
 * DynamoDB Streams consumer: reacts to new Completion items and
 *  1. awards badges (+ bonus XP), 2. advances the user's challenge progress.
 * Streams deliver at-least-once, so every write here is conditional and safe to repeat.
 * Failed records are reported individually so only they are retried (then sent to the DLQ).
 */
export const handler = async (event: DynamoDBStreamEvent): Promise<DynamoDBBatchResponse> => {
  const failures: { itemIdentifier: string }[] = [];
  for (const record of event.Records) {
    try {
      await processRecord(record);
    } catch (err) {
      console.error(JSON.stringify({ level: "ERROR", message: "stream record failed", err: String(err) }));
      const id = record.dynamodb?.SequenceNumber;
      if (id) failures.push({ itemIdentifier: id });
    }
  }
  return { batchItemFailures: failures };
};

interface CompletionItem {
  userId: string;
  goalId: string;
  taskId: string;
  date: string;
}

async function processRecord(record: DynamoDBRecord): Promise<void> {
  if (record.eventName !== "INSERT" || !record.dynamodb?.NewImage) return;
  const item = unmarshall(record.dynamodb.NewImage as Record<string, AttributeValue>);
  if (item.entity !== "Completion") return;
  const c = item as unknown as CompletionItem;

  await awardBadges(c.userId);
  await advanceChallenges(c);
}

async function awardBadges(userId: string): Promise<void> {
  const table = TABLE_NAME();
  const [streakRes, xpRes, ownedRes] = await Promise.all([
    ddb.send(new GetCommand({ TableName: table, Key: keys.streak(userId), ConsistentRead: true })),
    ddb.send(new GetCommand({ TableName: table, Key: keys.xp(userId), ConsistentRead: true })),
    ddb.send(
      new QueryCommand({
        TableName: table,
        KeyConditionExpression: "PK = :pk AND begins_with(SK, :sk)",
        ExpressionAttributeValues: { ":pk": `USER#${userId}`, ":sk": "BADGE#" },
      })
    ),
  ]);
  const owned = (ownedRes.Items ?? []).map((b) => b.badgeId as string);
  const toAward = badgesToAward(
    { totalCompletions: xpRes.Item?.totalCompletions ?? 0, currentStreak: streakRes.Item?.currentStreak ?? 0 },
    owned
  );

  for (const badge of toAward) {
    const now = new Date().toISOString();
    try {
      await ddb.send(
        new TransactWriteCommand({
          TransactItems: [
            {
              Put: {
                TableName: table,
                Item: { ...keys.userBadge(userId, badge.badgeId), entity: "UserBadge", userId, badgeId: badge.badgeId, awardedAt: now },
                ConditionExpression: "attribute_not_exists(PK)",
              },
            },
            ...(badge.bonusXp
              ? [
                  {
                    Put: {
                      TableName: table,
                      Item: { ...keys.xpLedger(userId, `badge#${badge.badgeId}`), entity: "XpLedger", userId, event: "BADGE", amount: badge.bonusXp, createdAt: now },
                      ConditionExpression: "attribute_not_exists(PK)",
                    },
                  },
                  xpIncrement(userId, badge.bonusXp, now),
                ]
              : []),
          ],
        })
      );
    } catch (err) {
      if (!isConditionFailure(err)) throw err; // already awarded by an earlier delivery
    }
  }
}

async function advanceChallenges(c: CompletionItem): Promise<void> {
  const table = TABLE_NAME();
  const mine = await ddb.send(
    new QueryCommand({
      TableName: table,
      KeyConditionExpression: "PK = :pk AND begins_with(SK, :sk)",
      ExpressionAttributeValues: { ":pk": `USER#${c.userId}`, ":sk": "CHALLENGE#" },
    })
  );

  for (const ch of mine.Items ?? []) {
    if (!isWithinWindow(c.date, ch as { startDate: string; endDate: string })) continue;

    try {
      // Marker + progress increment in one transaction: a redelivered record cannot count twice.
      await ddb.send(
        new TransactWriteCommand({
          TransactItems: [
            {
              Put: {
                TableName: table,
                Item: { ...keys.challengeProgress(c.userId, ch.challengeId, c.goalId, c.taskId), entity: "ChallengeProgressMarker", createdAt: new Date().toISOString() },
                ConditionExpression: "attribute_not_exists(PK)",
              },
            },
            {
              Update: {
                TableName: table,
                Key: keys.challengeMember(ch.challengeId, c.userId),
                UpdateExpression: "ADD progress :one",
                ConditionExpression: "attribute_exists(PK)",
                ExpressionAttributeValues: { ":one": 1 },
              },
            },
          ],
        })
      );
    } catch (err) {
      if (!isConditionFailure(err)) throw err; // already counted
    }
    // Always re-check completion, so a retry after a partial failure still pays the bonus once.
    await completeIfTargetReached(c.userId, ch.challengeId, ch.targetCompletions);
  }
}

async function completeIfTargetReached(userId: string, challengeId: string, target: number): Promise<void> {
  const table = TABLE_NAME();
  const member = (
    await ddb.send(new GetCommand({ TableName: table, Key: keys.challengeMember(challengeId, userId), ConsistentRead: true }))
  ).Item;
  if (!member || member.completedAt || !reachedTarget(member.progress ?? 0, target)) return;

  const now = new Date().toISOString();
  const bonus = xpFor("CHALLENGE_COMPLETED");
  try {
    await ddb.send(
      new TransactWriteCommand({
        TransactItems: [
          {
            Update: {
              TableName: table,
              Key: keys.challengeMember(challengeId, userId),
              UpdateExpression: "SET completedAt = :now",
              ConditionExpression: "attribute_not_exists(completedAt)",
              ExpressionAttributeValues: { ":now": now },
            },
          },
          {
            Put: {
              TableName: table,
              Item: { ...keys.xpLedger(userId, `challenge#${challengeId}`), entity: "XpLedger", userId, event: "CHALLENGE_COMPLETED", amount: bonus, createdAt: now },
              ConditionExpression: "attribute_not_exists(PK)",
            },
          },
          xpIncrement(userId, bonus, now),
        ],
      })
    );
  } catch (err) {
    if (!isConditionFailure(err)) throw err;
  }
}

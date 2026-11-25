import { randomUUID } from "node:crypto";
import { GetCommand, QueryCommand, TransactWriteCommand } from "@aws-sdk/lib-dynamodb";
import { z } from "zod";
import { batchGetKeys, ddb, isConditionFailure } from "../../shared/ddb";
import { keys, TABLE_NAME } from "../../shared/keys";
import { handler, json, parseBody, getUserId, HttpError } from "../../shared/http";
import { requireProfile } from "../../shared/profile";
import { toLocalDate } from "../../shared/domain/streak";
import { isChallengeOpen } from "../../shared/domain/challenges";

const date = z.string().regex(/^\d{4}-\d{2}-\d{2}$/);
const CreateChallenge = z
  .object({
    title: z.string().trim().min(1).max(100),
    type: z.enum(["individual", "group"]),
    targetCompletions: z.number().int().min(1).max(1000),
    startDate: date,
    endDate: date,
  })
  .refine((c) => c.endDate >= c.startDate, "endDate must not be before startDate");

/** The pointer item the stream consumer reads: it carries everything needed to count progress. */
const pointer = (userId: string, c: { challengeId: string; title: string; type: string; targetCompletions: number; startDate: string; endDate: string }) => ({
  ...keys.userChallenge(userId, c.challengeId),
  entity: "UserChallenge",
  userId,
  ...c,
});

/** POST /challenges: the creator is joined automatically. */
export const create = handler(async (event) => {
  const userId = getUserId(event);
  await requireProfile(userId);
  const input = parseBody(event, CreateChallenge);
  const table = TABLE_NAME();
  const challengeId = randomUUID();
  const now = new Date().toISOString();
  const c = { challengeId, ...input };

  await ddb.send(
    new TransactWriteCommand({
      TransactItems: [
        { Put: { TableName: table, Item: { ...keys.challenge(challengeId), entity: "Challenge", ownerId: userId, createdAt: now, ...c } } },
        { Put: { TableName: table, Item: { ...keys.challengeMember(challengeId, userId), entity: "ChallengeMember", challengeId, userId, progress: 0, joinedAt: now } } },
        { Put: { TableName: table, Item: pointer(userId, c) } },
      ],
    })
  );
  return json(201, c);
});

/** POST /challenges/{challengeId}/join: group challenges only, while the window is still open. */
export const join = handler(async (event) => {
  const userId = getUserId(event);
  const profile = await requireProfile(userId);
  const challengeId = event.pathParameters?.challengeId;
  if (!challengeId) throw new HttpError(400, "challengeId required");
  const table = TABLE_NAME();

  const res = await ddb.send(new GetCommand({ TableName: table, Key: keys.challenge(challengeId) }));
  const ch = res.Item;
  if (!ch) throw new HttpError(404, "Challenge not found");
  if (ch.type !== "group") throw new HttpError(403, "This challenge is individual");
  const today = toLocalDate(new Date(), profile.timezone);
  if (!isChallengeOpen(today, ch as { startDate: string; endDate: string })) {
    throw new HttpError(409, "Challenge has ended", "CLOSED");
  }

  const c = {
    challengeId,
    title: ch.title,
    type: ch.type,
    targetCompletions: ch.targetCompletions,
    startDate: ch.startDate,
    endDate: ch.endDate,
  };
  try {
    await ddb.send(
      new TransactWriteCommand({
        TransactItems: [
          {
            Put: {
              TableName: table,
              Item: { ...keys.challengeMember(challengeId, userId), entity: "ChallengeMember", challengeId, userId, progress: 0, joinedAt: new Date().toISOString() },
              ConditionExpression: "attribute_not_exists(PK)",
            },
          },
          { Put: { TableName: table, Item: pointer(userId, c) } },
        ],
      })
    );
  } catch (err) {
    if (isConditionFailure(err)) throw new HttpError(409, "Already joined", "ALREADY_JOINED");
    throw err;
  }
  return json(200, { joined: true, ...c });
});

/** GET /challenges: the caller's challenges with their own progress. */
export const list = handler(async (event) => {
  const userId = getUserId(event);
  const res = await ddb.send(
    new QueryCommand({
      TableName: TABLE_NAME(),
      KeyConditionExpression: "PK = :pk AND begins_with(SK, :sk)",
      ExpressionAttributeValues: { ":pk": `USER#${userId}`, ":sk": "CHALLENGE#" },
    })
  );
  const mine = res.Items ?? [];
  const members = await batchGetKeys(mine.map((m) => keys.challengeMember(m.challengeId, userId)));
  const byId = new Map(members.map((m) => [m.challengeId as string, m]));
  return json(200, {
    challenges: mine.map((m) => ({
      challengeId: m.challengeId,
      title: m.title,
      type: m.type,
      targetCompletions: m.targetCompletions,
      startDate: m.startDate,
      endDate: m.endDate,
      progress: byId.get(m.challengeId)?.progress ?? 0,
      completedAt: byId.get(m.challengeId)?.completedAt ?? null,
    })),
  });
});

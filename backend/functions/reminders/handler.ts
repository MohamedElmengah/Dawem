import { PublishCommand, SNSClient } from "@aws-sdk/client-sns";
import { QueryCommand, UpdateCommand } from "@aws-sdk/lib-dynamodb";
import { ddb, isConditionFailure } from "../../shared/ddb";
import { keys, TABLE_NAME } from "../../shared/keys";
import { getTodayTasks } from "../../shared/todayTasks";
import { toLocalDate } from "../../shared/domain/streak";
import { candidateLocalSlots, localSlot, reminderIndexKey } from "../../shared/domain/reminders";

const sns = new SNSClient({});

/**
 * Runs every 15 minutes (EventBridge rule). For each local time slot that is "now" somewhere in the
 * world, find users who chose that slot, confirm it really is that slot in THEIR timezone, and send one
 * reminder per day if they still have incomplete tasks.
 */
export const handler = async (): Promise<{ checked: number; sent: number }> => {
  const table = TABLE_NAME();
  const now = new Date();
  let checked = 0;
  let sent = 0;

  for (const slot of candidateLocalSlots(now)) {
    let startKey: Record<string, unknown> | undefined;
    do {
      const page = await ddb.send(
        new QueryCommand({
          TableName: table,
          IndexName: "GSI1",
          KeyConditionExpression: "GSI1PK = :p",
          ExpressionAttributeValues: { ":p": reminderIndexKey(slot) },
          ExclusiveStartKey: startKey,
        })
      );
      startKey = page.LastEvaluatedKey;

      for (const user of page.Items ?? []) {
        checked++;
        try {
          if (await remindUser(user, slot, now)) sent++;
        } catch (err) {
          console.error(JSON.stringify({ level: "ERROR", message: "reminder failed", userId: user.userId, err: String(err) }));
        }
      }
    } while (startKey);
  }

  console.log(JSON.stringify({ level: "INFO", message: "reminder sweep", checked, sent }));
  return { checked, sent };
};

async function remindUser(user: Record<string, any>, slot: string, now: Date): Promise<boolean> {
  const table = TABLE_NAME();
  const userId: string = user.userId;
  const tz: string = user.timezone;

  // The index only narrows candidates; the user's own timezone decides.
  if (!user.remindersEnabled || localSlot(now, tz) !== slot) return false;
  const today = toLocalDate(now, tz);
  if (user.lastReminderDate === today) return false;

  const incomplete = (await getTodayTasks(userId, today)).filter((t) => !t.completed);
  if (incomplete.length === 0) return false;

  // Claim today's reminder first so overlapping or retried runs cannot send it twice.
  try {
    await ddb.send(
      new UpdateCommand({
        TableName: table,
        Key: keys.user(userId),
        UpdateExpression: "SET lastReminderDate = :d",
        ConditionExpression: "attribute_not_exists(lastReminderDate) OR lastReminderDate <> :d",
        ExpressionAttributeValues: { ":d": today },
      })
    );
  } catch (err) {
    if (isConditionFailure(err)) return false;
    throw err;
  }

  const devices = await ddb.send(
    new QueryCommand({
      TableName: table,
      KeyConditionExpression: "PK = :pk AND begins_with(SK, :sk)",
      ExpressionAttributeValues: { ":pk": `USER#${userId}`, ":sk": "DEVICE#" },
    })
  );

  const n = incomplete.length;
  const body = `You have ${n} task${n === 1 ? "" : "s"} left today. Keep your streak going!`;
  let delivered = false;
  for (const d of devices.Items ?? []) {
    if (!d.enabled || !d.endpointArn) continue;
    if (await publish(userId, d.deviceId, d.endpointArn, "Dawem", body)) delivered = true;
  }
  return delivered;
}

async function publish(userId: string, deviceId: string, endpointArn: string, title: string, body: string): Promise<boolean> {
  try {
    await sns.send(
      new PublishCommand({
        TargetArn: endpointArn,
        MessageStructure: "json",
        // FCM HTTP v1 payload wrapped for SNS. Verify against your SNS platform application type.
        Message: JSON.stringify({
          default: body,
          GCM: JSON.stringify({ fcmV1Message: { message: { notification: { title, body } } } }),
        }),
      })
    );
    return true;
  } catch (err) {
    const name = (err as { name?: string }).name;
    if (name === "EndpointDisabledException" || name === "InvalidParameterException") {
      // Token is dead (app uninstalled / token rotated): stop sending until the app re-registers.
      await ddb.send(
        new UpdateCommand({
          TableName: TABLE_NAME(),
          Key: keys.device(userId, deviceId),
          UpdateExpression: "SET enabled = :f",
          ExpressionAttributeValues: { ":f": false },
        })
      );
      return false;
    }
    throw err;
  }
}

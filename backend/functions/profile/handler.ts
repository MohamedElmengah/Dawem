import { GetCommand, UpdateCommand } from "@aws-sdk/lib-dynamodb";
import { z } from "zod";
import { ddb } from "../../shared/ddb";
import { keys, TABLE_NAME } from "../../shared/keys";
import { handler, json, parseBody, getUserId, HttpError } from "../../shared/http";
import { isValidReminderTime, isValidTimeZone, reminderIndexKey } from "../../shared/domain/reminders";

const ProfileInput = z.object({
  displayName: z.string().trim().min(1).max(60).optional(),
  timezone: z.string().refine(isValidTimeZone, "Unknown IANA timezone").optional(),
  remindersEnabled: z.boolean().optional(),
  reminderTime: z.string().refine(isValidReminderTime, "Use HH:mm on a 15-minute boundary").optional(),
});

const publicView = (item: Record<string, unknown>) => ({
  displayName: item.displayName,
  timezone: item.timezone,
  remindersEnabled: item.remindersEnabled,
  reminderTime: item.reminderTime,
  createdAt: item.createdAt,
  updatedAt: item.updatedAt,
});

/** GET /me: 404 until the app has created the profile. */
export const get = handler(async (event) => {
  const userId = getUserId(event);
  const res = await ddb.send(new GetCommand({ TableName: TABLE_NAME(), Key: keys.user(userId) }));
  if (!res.Item) throw new HttpError(404, "Profile not found", "PROFILE_REQUIRED");
  return json(200, publicView(res.Item));
});

/**
 * PUT /me: create or partially update. `timezone` is mandatory the first time.
 * Reminder settings also maintain the GSI1 entry that the reminder sweep queries.
 */
export const put = handler(async (event) => {
  const userId = getUserId(event);
  const input = parseBody(event, ProfileInput);
  const table = TABLE_NAME();

  const existing = (await ddb.send(new GetCommand({ TableName: table, Key: keys.user(userId) }))).Item;
  const merged = {
    displayName: input.displayName ?? existing?.displayName,
    timezone: input.timezone ?? existing?.timezone,
    remindersEnabled: input.remindersEnabled ?? existing?.remindersEnabled ?? true,
    reminderTime: input.reminderTime ?? existing?.reminderTime ?? "20:00",
  };
  if (!merged.timezone) throw new HttpError(400, "timezone is required", "VALIDATION");

  const now = new Date().toISOString();
  const names: Record<string, string> = {
    "#e": "entity",
    "#u": "userId",
    "#tz": "timezone",
    "#re": "remindersEnabled",
    "#rt": "reminderTime",
    "#ua": "updatedAt",
    "#ca": "createdAt",
  };
  const values: Record<string, unknown> = {
    ":e": "Profile",
    ":u": userId,
    ":tz": merged.timezone,
    ":re": merged.remindersEnabled,
    ":rt": merged.reminderTime,
    ":ua": now,
    ":now": now,
  };
  const sets = ["#e = :e", "#u = :u", "#tz = :tz", "#re = :re", "#rt = :rt", "#ua = :ua", "#ca = if_not_exists(#ca, :now)"];
  const removes: string[] = [];

  if (merged.displayName) {
    names["#dn"] = "displayName";
    values[":dn"] = merged.displayName;
    sets.push("#dn = :dn");
  }
  if (merged.remindersEnabled) {
    names["#g1p"] = "GSI1PK";
    names["#g1s"] = "GSI1SK";
    values[":g1p"] = reminderIndexKey(merged.reminderTime);
    values[":g1s"] = `USER#${userId}`;
    sets.push("#g1p = :g1p", "#g1s = :g1s");
  } else {
    removes.push("GSI1PK", "GSI1SK");
  }

  const res = await ddb.send(
    new UpdateCommand({
      TableName: table,
      Key: keys.user(userId),
      UpdateExpression: `SET ${sets.join(", ")}` + (removes.length ? ` REMOVE ${removes.join(", ")}` : ""),
      ExpressionAttributeNames: names,
      ExpressionAttributeValues: values,
      ReturnValues: "ALL_NEW",
    })
  );
  return json(200, publicView(res.Attributes ?? {}));
});

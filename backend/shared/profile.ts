import { GetCommand } from "@aws-sdk/lib-dynamodb";
import { ddb } from "./ddb";
import { keys, TABLE_NAME } from "./keys";
import { HttpError } from "./http";

export interface Profile {
  userId: string;
  timezone: string;
  displayName?: string;
  remindersEnabled: boolean;
  reminderTime: string; // local "HH:mm"
  lastReminderDate?: string;
}

/**
 * Anything that depends on "today" needs the user's timezone, so it needs a profile.
 * There is deliberately no silent UTC fallback: the app must call PUT /me first.
 */
export async function requireProfile(userId: string): Promise<Profile> {
  const res = await ddb.send(new GetCommand({ TableName: TABLE_NAME(), Key: keys.user(userId) }));
  if (!res.Item?.timezone) {
    throw new HttpError(409, "Create your profile first (PUT /me with a timezone)", "PROFILE_REQUIRED");
  }
  return res.Item as Profile;
}

import {
  CreatePlatformEndpointCommand,
  DeleteEndpointCommand,
  GetEndpointAttributesCommand,
  SNSClient,
  SetEndpointAttributesCommand,
} from "@aws-sdk/client-sns";
import { DeleteCommand, GetCommand, PutCommand } from "@aws-sdk/lib-dynamodb";
import { z } from "zod";
import { ddb } from "../../shared/ddb";
import { keys, TABLE_NAME } from "../../shared/keys";
import { handler, json, parseBody, getUserId, HttpError } from "../../shared/http";

const sns = new SNSClient({});

const RegisterDevice = z.object({
  token: z.string().min(20).max(4096), // FCM registration token
  platform: z.enum(["android", "ios"]),
});
const DeviceId = z.string().regex(/^[A-Za-z0-9_-]{1,128}$/);

/**
 * PUT /devices/{deviceId}: idempotent register/refresh. deviceId is a stable id the app generates once.
 * Both platforms use FCM tokens (Flutter), so one SNS platform application covers them.
 * If PLATFORM_APP_ARN is not configured yet, the token is stored and pushEnabled is false.
 */
export const register = handler(async (event) => {
  const userId = getUserId(event);
  const parsedId = DeviceId.safeParse(event.pathParameters?.deviceId);
  if (!parsedId.success) throw new HttpError(400, "Invalid deviceId", "VALIDATION");
  const deviceId = parsedId.data;
  const { token, platform } = parseBody(event, RegisterDevice);
  const table = TABLE_NAME();
  const appArn = process.env.PLATFORM_APP_ARN;

  const existing = (await ddb.send(new GetCommand({ TableName: table, Key: keys.device(userId, deviceId) }))).Item;
  let endpointArn: string | undefined = existing?.endpointArn;

  if (appArn) {
    if (endpointArn) {
      try {
        const attrs = (await sns.send(new GetEndpointAttributesCommand({ EndpointArn: endpointArn }))).Attributes;
        if (attrs?.Token !== token || attrs?.Enabled !== "true") {
          await sns.send(
            new SetEndpointAttributesCommand({ EndpointArn: endpointArn, Attributes: { Token: token, Enabled: "true" } })
          );
        }
      } catch (err) {
        if ((err as { name?: string }).name !== "NotFoundException") throw err;
        endpointArn = undefined; // endpoint was deleted: create a fresh one
      }
    }
    if (!endpointArn) {
      const res = await sns.send(
        new CreatePlatformEndpointCommand({ PlatformApplicationArn: appArn, Token: token, CustomUserData: userId })
      );
      endpointArn = res.EndpointArn;
    }
  }

  await ddb.send(
    new PutCommand({
      TableName: table,
      Item: {
        ...keys.device(userId, deviceId),
        entity: "Device",
        userId,
        deviceId,
        platform,
        token,
        endpointArn,
        enabled: true,
        updatedAt: new Date().toISOString(),
      },
    })
  );
  return json(200, { deviceId, pushEnabled: Boolean(endpointArn) });
});

/** DELETE /devices/{deviceId}: call on logout so the device stops receiving this user's reminders. */
export const unregister = handler(async (event) => {
  const userId = getUserId(event);
  const parsedId = DeviceId.safeParse(event.pathParameters?.deviceId);
  if (!parsedId.success) throw new HttpError(400, "Invalid deviceId", "VALIDATION");
  const table = TABLE_NAME();

  const existing = (await ddb.send(new GetCommand({ TableName: table, Key: keys.device(userId, parsedId.data) }))).Item;
  if (existing?.endpointArn) {
    try {
      await sns.send(new DeleteEndpointCommand({ EndpointArn: existing.endpointArn }));
    } catch (err) {
      if ((err as { name?: string }).name !== "NotFoundException") throw err;
    }
  }
  await ddb.send(new DeleteCommand({ TableName: table, Key: keys.device(userId, parsedId.data) }));
  return { statusCode: 204, body: "" };
});

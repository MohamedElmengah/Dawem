import type { TransactWriteCommandInput } from "@aws-sdk/lib-dynamodb";
import { keys, TABLE_NAME } from "./keys";

export type TxItem = NonNullable<TransactWriteCommandInput["TransactItems"]>[number];

/**
 * Atomic XP increment. Never read-modify-write the total: two concurrent completions would lose XP.
 * Level is derived from totalXP when read, so it is never stored.
 */
export function xpIncrement(userId: string, amount: number, nowIso: string, completions = 0): TxItem {
  return {
    Update: {
      TableName: TABLE_NAME(),
      Key: keys.xp(userId),
      UpdateExpression: "SET #e = :e, #u = :u, #t = :t ADD #x :a" + (completions ? ", #c :c" : ""),
      ExpressionAttributeNames: {
        "#e": "entity",
        "#u": "userId",
        "#t": "updatedAt",
        "#x": "totalXP",
        ...(completions ? { "#c": "totalCompletions" } : {}),
      },
      ExpressionAttributeValues: {
        ":e": "UserXP",
        ":u": userId,
        ":t": nowIso,
        ":a": amount,
        ...(completions ? { ":c": completions } : {}),
      },
    },
  };
}

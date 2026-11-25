import {
  ConditionalCheckFailedException,
  DynamoDBClient,
  TransactionCanceledException,
} from "@aws-sdk/client-dynamodb";
import { BatchGetCommand, DynamoDBDocumentClient } from "@aws-sdk/lib-dynamodb";
import { TABLE_NAME } from "./keys";

export const ddb = DynamoDBDocumentClient.from(new DynamoDBClient({}), {
  marshallOptions: { removeUndefinedValues: true },
});

/** True when a write failed only because its ConditionExpression was false (e.g. "already exists"). */
export function isConditionFailure(err: unknown): boolean {
  if (err instanceof ConditionalCheckFailedException) return true;
  if (err instanceof TransactionCanceledException) {
    return (err.CancellationReasons ?? []).some((r) => r.Code === "ConditionalCheckFailed");
  }
  return false;
}

type Key = { PK: string; SK: string };

/** BatchGet of up to many keys: chunks of 100, retries unprocessed keys a few times. */
export async function batchGetKeys(allKeys: Key[]): Promise<Record<string, unknown>[]> {
  const table = TABLE_NAME();
  const out: Record<string, unknown>[] = [];
  for (let i = 0; i < allKeys.length; i += 100) {
    let pending: Key[] = allKeys.slice(i, i + 100);
    for (let attempt = 0; attempt < 3 && pending.length > 0; attempt++) {
      const res = await ddb.send(new BatchGetCommand({ RequestItems: { [table]: { Keys: pending } } }));
      out.push(...((res.Responses?.[table] as Record<string, unknown>[] | undefined) ?? []));
      pending = (res.UnprocessedKeys?.[table]?.Keys as Key[] | undefined) ?? [];
    }
  }
  return out;
}

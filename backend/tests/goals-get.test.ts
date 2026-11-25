import { beforeEach, describe, expect, it, vi } from "vitest";
import type { ApiEvent } from "../shared/http";
import { ddb } from "../shared/ddb";
import { get } from "../functions/goals/handler";

vi.mock("../shared/ddb", () => ({ ddb: { send: vi.fn() }, batchGetKeys: vi.fn(), isConditionFailure: () => false }));

import { batchGetKeys } from "../shared/ddb";

const send = ddb.send as unknown as ReturnType<typeof vi.fn>;
const batchGet = batchGetKeys as unknown as ReturnType<typeof vi.fn>;
const ev = (goalId = "g1") =>
  ({
    requestContext: { authorizer: { jwt: { claims: { sub: "user-1" } } } },
    pathParameters: { goalId },
  }) as unknown as ApiEvent;
const call = async (e: ApiEvent) => (await get(e)) as { statusCode: number; body: string };

beforeEach(() => {
  process.env.TABLE_NAME = "t";
  send.mockReset();
  batchGet.mockReset();
});

describe("GET /goals/{goalId}", () => {
  it("returns the goal with its tasks (AI and manual) and a completed flag, without the tasks' internal keys", async () => {
    send.mockImplementation(async (cmd: { constructor: { name: string } }) =>
      cmd.constructor.name === "GetCommand"
        ? { Item: { PK: "USER#user-1", SK: "GOAL#g1", goalId: "g1", title: "Learn" } }
        : {
            Items: [
              { PK: "GOAL#g1", SK: "TASK#2026-11-30-a", entity: "Task", userId: "user-1", taskId: "2026-11-30-a", goalId: "g1", title: "One", date: "2026-11-30" },
              { PK: "GOAL#g1", SK: "TASK#2026-12-01-b", entity: "Task", userId: "user-1", taskId: "2026-12-01-b", goalId: "g1", title: "Two", date: "2026-12-01" },
            ],
          }
    );
    batchGet.mockResolvedValue([{ goalId: "g1", taskId: "2026-11-30-a" }]);

    const r = await call(ev());
    expect(r.statusCode).toBe(200);
    const out = JSON.parse(r.body);
    expect(out.goalId).toBe("g1");
    expect(out.tasks.map((t: { taskId: string; completed: boolean }) => [t.taskId, t.completed])).toEqual([
      ["2026-11-30-a", true],
      ["2026-12-01-b", false],
    ]);
    expect("PK" in out.tasks[0]).toBe(false);
    expect("userId" in out.tasks[0]).toBe(false);
  });

  it("404s for a goal that is not the caller's", async () => {
    send.mockResolvedValue({});
    expect((await call(ev())).statusCode).toBe(404);
  });
});

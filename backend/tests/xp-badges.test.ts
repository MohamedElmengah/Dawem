import { describe, it, expect } from "vitest";
import { levelForXp, xpFor } from "../shared/domain/xp";
import { earnedBadgeIds } from "../shared/domain/badges";

describe("xp", () => {
  it("awards configured XP", () => {
    expect(xpFor("TASK_COMPLETED")).toBe(10);
  });
  it("computes levels at thresholds", () => {
    expect(levelForXp(0)).toBe(1);
    expect(levelForXp(99)).toBe(1);
    expect(levelForXp(100)).toBe(2);
    expect(levelForXp(300)).toBe(3);
  });
});

describe("badges", () => {
  it("awards daily-starter after first completion", () => {
    expect(earnedBadgeIds({ totalCompletions: 1, currentStreak: 1 })).toEqual(["daily-starter"]);
  });
  it("awards 7-day-streak at 7", () => {
    expect(earnedBadgeIds({ totalCompletions: 7, currentStreak: 7 })).toContain("7-day-streak");
  });
  it("awards nothing with no progress", () => {
    expect(earnedBadgeIds({ totalCompletions: 0, currentStreak: 0 })).toEqual([]);
  });
});

import { describe, it, expect } from "vitest";
import { badgesToAward } from "../shared/domain/badges";
import { isChallengeOpen, isWithinWindow, reachedTarget } from "../shared/domain/challenges";
import { levelForXp, xpThreshold } from "../shared/domain/xp";

describe("badgesToAward", () => {
  it("awards only badges the user does not own", () => {
    const ctx = { totalCompletions: 7, currentStreak: 7 };
    expect(badgesToAward(ctx, []).map((b) => b.badgeId)).toEqual(["daily-starter", "7-day-streak"]);
    expect(badgesToAward(ctx, ["daily-starter"]).map((b) => b.badgeId)).toEqual(["7-day-streak"]);
    expect(badgesToAward(ctx, ["daily-starter", "7-day-streak"])).toEqual([]);
  });
  it("gives bonus XP for the streak badge only", () => {
    const all = badgesToAward({ totalCompletions: 7, currentStreak: 7 }, []);
    expect(all.find((b) => b.badgeId === "7-day-streak")?.bonusXp).toBe(50);
    expect(all.find((b) => b.badgeId === "daily-starter")?.bonusXp).toBeUndefined();
  });
});

describe("challenge window", () => {
  const w = { startDate: "2026-11-10", endDate: "2026-11-20" };
  it("is inclusive on both ends", () => {
    expect(isWithinWindow("2026-11-10", w)).toBe(true);
    expect(isWithinWindow("2026-11-20", w)).toBe(true);
    expect(isWithinWindow("2026-11-09", w)).toBe(false);
    expect(isWithinWindow("2026-11-21", w)).toBe(false);
  });
  it("is open until the end date", () => {
    expect(isChallengeOpen("2026-11-20", w)).toBe(true);
    expect(isChallengeOpen("2026-11-21", w)).toBe(false);
  });
  it("detects the target", () => {
    expect(reachedTarget(9, 10)).toBe(false);
    expect(reachedTarget(10, 10)).toBe(true);
  });
});

describe("xp thresholds", () => {
  it("are consistent with levelForXp", () => {
    for (let l = 1; l < 20; l++) {
      expect(levelForXp(xpThreshold(l))).toBe(l);
      expect(levelForXp(xpThreshold(l + 1) - 1)).toBe(l);
    }
  });
});

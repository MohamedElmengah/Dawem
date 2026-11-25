import { describe, it, expect } from "vitest";
import { applyCompletion, displayStreak, INITIAL_STREAK, toLocalDate, daysBetween } from "../shared/domain/streak";

const s = (over: Partial<typeof INITIAL_STREAK> = {}) => ({ ...INITIAL_STREAK, ...over });

describe("applyCompletion", () => {
  it("starts a streak on first completion", () => {
    const r = applyCompletion(INITIAL_STREAK, "2026-10-18");
    expect(r.state.currentStreak).toBe(1);
    expect(r.state.longestStreak).toBe(1);
  });

  it("is idempotent for the same day", () => {
    const first = applyCompletion(INITIAL_STREAK, "2026-10-18").state;
    const again = applyCompletion(first, "2026-10-18");
    expect(again.alreadyCountedToday).toBe(true);
    expect(again.state).toEqual(first);
  });

  it("increments on consecutive days", () => {
    const d1 = applyCompletion(INITIAL_STREAK, "2026-10-18").state;
    const d2 = applyCompletion(d1, "2026-10-19").state;
    expect(d2.currentStreak).toBe(2);
  });

  it("consumes a freeze for one missed day and keeps the streak", () => {
    const base = s({ currentStreak: 5, longestStreak: 5, lastCompletedDate: "2026-10-16", freezesRemaining: 2 });
    const r = applyCompletion(base, "2026-10-18"); // missed 10-17
    expect(r.freezesConsumed).toBe(1);
    expect(r.state.currentStreak).toBe(6);
    expect(r.state.freezesRemaining).toBe(1);
  });

  it("resets to 1 when missed days exceed freezes", () => {
    const base = s({ currentStreak: 9, longestStreak: 9, lastCompletedDate: "2026-10-10", freezesRemaining: 1 });
    const r = applyCompletion(base, "2026-10-18");
    expect(r.state.currentStreak).toBe(1);
    expect(r.state.longestStreak).toBe(9);
    expect(r.freezesConsumed).toBe(0);
  });

  it("handles month and year boundaries", () => {
    expect(daysBetween("2026-12-31", "2027-01-01")).toBe(1);
    expect(daysBetween("2026-02-28", "2026-03-01")).toBe(1);
  });
});

describe("displayStreak", () => {
  it("shows 0 once the gap can't be covered by freezes", () => {
    const base = s({ currentStreak: 4, lastCompletedDate: "2026-10-10", freezesRemaining: 1 });
    expect(displayStreak(base, "2026-10-18")).toBe(0);
  });
  it("keeps the streak when freezes cover the gap", () => {
    const base = s({ currentStreak: 4, lastCompletedDate: "2026-10-16", freezesRemaining: 1 });
    expect(displayStreak(base, "2026-10-18")).toBe(4);
  });
});

describe("toLocalDate", () => {
  it("uses the user's timezone, not UTC, for the day boundary", () => {
    const instant = new Date("2026-10-18T22:30:00Z"); // 01:30 on the 19th in Cairo (UTC+3)
    expect(toLocalDate(instant, "UTC")).toBe("2026-10-18");
    expect(toLocalDate(instant, "Africa/Cairo")).toBe("2026-10-19");
  });
});

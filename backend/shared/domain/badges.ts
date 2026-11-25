/** Deterministic, rule-driven badge criteria. Awarding is idempotent at the data layer. */
import { xpFor } from "./xp";

export interface BadgeContext {
  totalCompletions: number;
  currentStreak: number;
}

export interface BadgeRule {
  badgeId: string;
  name: string;
  description: string;
  bonusXp?: number;
  isEarned: (ctx: BadgeContext) => boolean;
}

export const BADGE_RULES: BadgeRule[] = [
  {
    badgeId: "daily-starter",
    name: "Daily Starter",
    description: "Complete your first task.",
    isEarned: (c) => c.totalCompletions >= 1,
  },
  {
    badgeId: "7-day-streak",
    name: "7-Day Streak",
    description: "Reach a 7-day streak.",
    bonusXp: xpFor("STREAK_7"),
    isEarned: (c) => c.currentStreak >= 7,
  },
];

export function earnedBadgeIds(ctx: BadgeContext): string[] {
  return BADGE_RULES.filter((r) => r.isEarned(ctx)).map((r) => r.badgeId);
}

/** Badges the user has now earned but does not own yet. */
export function badgesToAward(ctx: BadgeContext, owned: Iterable<string>): BadgeRule[] {
  const have = new Set(owned);
  return BADGE_RULES.filter((r) => r.isEarned(ctx) && !have.has(r.badgeId));
}

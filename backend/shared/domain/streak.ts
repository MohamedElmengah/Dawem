/**
 * Pure streak engine. No AWS imports: (state, completion date, timezone) -> new state.
 * Dates are calendar days in the user's timezone, formatted YYYY-MM-DD.
 */

export interface StreakState {
  currentStreak: number;
  longestStreak: number;
  lastCompletedDate: string | null; // YYYY-MM-DD in user's timezone
  freezesRemaining: number;
}

export const INITIAL_STREAK: StreakState = {
  currentStreak: 0,
  longestStreak: 0,
  lastCompletedDate: null,
  freezesRemaining: 2, // two configurable protections (MVP)
};

/** Calendar date (YYYY-MM-DD) of an instant in the given IANA timezone. */
export function toLocalDate(instant: Date, timeZone: string): string {
  const parts = new Intl.DateTimeFormat("en-CA", {
    timeZone,
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).format(instant);
  return parts; // en-CA yields YYYY-MM-DD
}

/** Whole days between two YYYY-MM-DD dates (b - a). */
export function daysBetween(a: string, b: string): number {
  const ms = Date.parse(`${b}T00:00:00Z`) - Date.parse(`${a}T00:00:00Z`);
  return Math.round(ms / 86_400_000);
}

export interface StreakResult {
  state: StreakState;
  freezesConsumed: number;
  alreadyCountedToday: boolean;
}

/**
 * Apply a completion on `today` to the streak.
 * - same day as last completion: no change (idempotent)
 * - next day: streak + 1
 * - gap of N missed days: consume one freeze per missed day if enough remain,
 *   otherwise reset to 1 (today's completion starts a new streak).
 */
export function applyCompletion(prev: StreakState, today: string): StreakResult {
  const last = prev.lastCompletedDate;

  if (last === null) {
    return {
      state: {
        ...prev,
        currentStreak: 1,
        longestStreak: Math.max(prev.longestStreak, 1),
        lastCompletedDate: today,
      },
      freezesConsumed: 0,
      alreadyCountedToday: false,
    };
  }

  const gap = daysBetween(last, today);

  if (gap <= 0) {
    return { state: prev, freezesConsumed: 0, alreadyCountedToday: true };
  }

  const missed = gap - 1;
  let freezes = prev.freezesRemaining;
  let consumed = 0;
  let current: number;

  if (missed === 0) {
    current = prev.currentStreak + 1;
  } else if (missed <= freezes) {
    consumed = missed;
    freezes -= missed;
    current = prev.currentStreak + 1;
  } else {
    current = 1;
  }

  return {
    state: {
      currentStreak: current,
      longestStreak: Math.max(prev.longestStreak, current),
      lastCompletedDate: today,
      freezesRemaining: freezes,
    },
    freezesConsumed: consumed,
    alreadyCountedToday: false,
  };
}

/**
 * Streak as it should be displayed on `today` without a new completion:
 * if the user has missed more days than freezes can cover, show 0.
 */
export function displayStreak(prev: StreakState, today: string): number {
  if (prev.lastCompletedDate === null) return 0;
  const missed = Math.max(0, daysBetween(prev.lastCompletedDate, today) - 1);
  return missed <= prev.freezesRemaining ? prev.currentStreak : 0;
}

/** Pure challenge rules. Progress is only ever changed by the backend, from completion events. */

export interface ChallengeWindow {
  startDate: string; // YYYY-MM-DD
  endDate: string; // YYYY-MM-DD, inclusive
}

/** ISO dates compare correctly as strings. */
export function isWithinWindow(date: string, w: ChallengeWindow): boolean {
  return date >= w.startDate && date <= w.endDate;
}

export function isChallengeOpen(today: string, w: ChallengeWindow): boolean {
  return today <= w.endDate;
}

export function reachedTarget(progress: number, target: number): boolean {
  return progress >= target;
}

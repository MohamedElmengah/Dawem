/**
 * Reminder time helpers. Users pick a LOCAL reminder time (15-minute resolution).
 * A sweep runs every 15 minutes in UTC, so we must work out which local slots are "now" somewhere.
 * Matching is done in the user's own timezone, so daylight-saving changes are handled correctly.
 */

export const SLOT_MINUTES = 15;

export function isValidTimeZone(tz: string): boolean {
  try {
    new Intl.DateTimeFormat("en", { timeZone: tz });
    return true;
  } catch {
    return false;
  }
}

/** "HH:mm" on a 15-minute boundary. */
export function isValidReminderTime(s: string): boolean {
  return /^([01]\d|2[0-3]):(00|15|30|45)$/.test(s);
}

function floorToSlot(hour: number, minute: number): string {
  const m = minute - (minute % SLOT_MINUTES);
  return `${String(hour).padStart(2, "0")}:${String(m).padStart(2, "0")}`;
}

/** The 15-minute local slot ("HH:mm") that `instant` falls in, for the given timezone. */
export function localSlot(instant: Date, timeZone: string): string {
  const parts = new Intl.DateTimeFormat("en-GB", {
    timeZone,
    hour: "2-digit",
    minute: "2-digit",
    hourCycle: "h23",
  }).formatToParts(instant);
  const hour = Number(parts.find((p) => p.type === "hour")?.value ?? "0");
  const minute = Number(parts.find((p) => p.type === "minute")?.value ?? "0");
  return floorToSlot(hour, minute);
}

/** UTC offsets (minutes) that exist in the IANA database today. */
const WHOLE_HOUR_OFFSETS = Array.from({ length: 27 }, (_, i) => (i - 12) * 60); // -12h .. +14h
const ODD_OFFSETS = [-570, -210, -150, 210, 270, 330, 345, 390, 525, 570, 630, 765, 825];
const KNOWN_OFFSETS_MIN = [...WHOLE_HOUR_OFFSETS, ...ODD_OFFSETS];

/**
 * Every local slot that could be "now" in some real timezone.
 * The sweep queries one index partition per slot, then re-checks each user's own timezone.
 */
export function candidateLocalSlots(instant: Date): string[] {
  const slots = new Set<string>();
  for (const offset of KNOWN_OFFSETS_MIN) {
    const t = new Date(instant.getTime() + offset * 60_000);
    slots.add(floorToSlot(t.getUTCHours(), t.getUTCMinutes()));
  }
  return [...slots].sort();
}

export const reminderIndexKey = (slot: string) => `REMIND#${slot}`;

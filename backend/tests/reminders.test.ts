import { describe, it, expect } from "vitest";
import {
  candidateLocalSlots,
  isValidReminderTime,
  isValidTimeZone,
  localSlot,
} from "../shared/domain/reminders";

describe("validation", () => {
  it("accepts real IANA timezones and rejects junk", () => {
    expect(isValidTimeZone("Africa/Cairo")).toBe(true);
    expect(isValidTimeZone("Mars/Olympus")).toBe(false);
  });
  it("accepts only 15-minute reminder times", () => {
    expect(isValidReminderTime("20:00")).toBe(true);
    expect(isValidReminderTime("08:45")).toBe(true);
    expect(isValidReminderTime("08:10")).toBe(false);
    expect(isValidReminderTime("24:00")).toBe(false);
    expect(isValidReminderTime("8:00")).toBe(false);
  });
});

describe("localSlot", () => {
  it("floors to the 15-minute slot in the user's timezone", () => {
    const t = new Date("2026-11-20T18:07:00Z"); // Cairo is UTC+2 in November
    expect(localSlot(t, "Africa/Cairo")).toBe("20:00");
    expect(localSlot(new Date("2026-11-20T18:59:00Z"), "Africa/Cairo")).toBe("20:45");
  });
  it("handles midnight rollover", () => {
    expect(localSlot(new Date("2026-11-20T22:20:00Z"), "Africa/Cairo")).toBe("00:15");
  });
});

describe("candidateLocalSlots", () => {
  // The sweep only finds a user if their current local slot is among the candidates,
  // including half-hour/45-minute zones and across DST changes.
  const zones = [
    "Africa/Cairo", "America/New_York", "America/Los_Angeles", "Europe/London", "Asia/Kolkata",
    "Asia/Kathmandu", "America/St_Johns", "Australia/Eucla", "Australia/Adelaide", "Pacific/Chatham",
    "Pacific/Auckland", "Asia/Tehran", "Asia/Kabul", "Pacific/Kiritimati", "Pacific/Pago_Pago",
  ];
  const instants = [
    "2026-01-15T00:00:00Z", "2026-03-29T00:50:00Z", "2026-06-21T12:07:00Z",
    "2026-10-25T01:20:00Z", "2026-11-20T18:07:00Z", "2026-12-31T23:59:00Z",
  ];
  for (const tz of zones) {
    it(`covers ${tz}`, () => {
      for (const iso of instants) {
        const t = new Date(iso);
        expect(candidateLocalSlots(t)).toContain(localSlot(t, tz));
      }
    });
  }
  it("is a small set (not all 96 slots)", () => {
    expect(candidateLocalSlots(new Date("2026-11-20T18:07:00Z")).length).toBeLessThan(40);
  });
});

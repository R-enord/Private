import { describe, expect, it } from "vitest";
import { bytes, compactNumber, duration, relativeTime } from "./format";
import { qs } from "./api";

describe("format helpers", () => {
  const now = new Date("2026-11-22T12:00:00Z");
  it("relative time", () => {
    expect(relativeTime("2026-11-22T11:59:30Z", now)).toBe("30 seconds ago");
    expect(relativeTime("2026-11-22T11:00:00Z", now)).toBe("1 hour ago");
    expect(relativeTime("2026-11-20T12:00:00Z", now)).toBe("2 days ago");
    expect(relativeTime(null, now)).toBe("unknown");
    expect(relativeTime("not a date", now)).toBe("unknown");
    expect(relativeTime("2026-11-22T12:05:00Z", now)).toBe("in 5 minutes");
  });
  it("compact numbers", () => {
    expect(compactNumber(999)).toBe("999");
    expect(compactNumber(1500)).toBe("1,500");
    expect(compactNumber(25000)).toBe("25K");
    expect(compactNumber(2_300_000)).toBe("2.3M");
    expect(compactNumber(undefined)).toBe("–");
  });
  it("bytes and duration", () => {
    expect(bytes(2048)).toBe("2.0 KB");
    expect(duration(900)).toBe("15 min");
    expect(duration(7200)).toBe("2 h");
  });
  it("query strings drop empty values", () => {
    expect(qs({ a: 1, b: "", c: undefined, d: false, e: "x y" })).toBe("?a=1&e=x+y");
    expect(qs({})).toBe("");
  });
});

import { describe, expect, it } from "vitest";

import i18n from "../i18n";
import { describeCron } from "../lib/cron";

describe("describeCron", () => {
  const it_ = i18n.getFixedT("it");
  const en = i18n.getFixedT("en");

  it("describes the common schedules in the user's language", () => {
    expect(describeCron("0 3 * * *", it_, "it")).toBe("Ogni giorno alle 03:00");
    expect(describeCron("30 2 * * 1", it_, "it")).toBe("Ogni lunedì alle 02:30");
    expect(describeCron("0 */6 * * *", en, "en")).toBe("Every 6 hours");
    expect(describeCron("0 */1 * * *", en, "en")).toBe("Every hour");
    expect(describeCron("15 4 1 * *", en, "en")).toBe("Every month on day 1 at 04:15");
    expect(describeCron("0 3 * * 0", en, "en")).toBe("Every Sunday at 03:00");
    expect(describeCron("0 3 * * 7", en, "en")).toBe("Every Sunday at 03:00");
  });

  it("keeps unusual or missing schedules readable", () => {
    expect(describeCron("0 3 1 1 *", en, "en")).toBe("0 3 1 1 *");
    expect(describeCron("*/5 * * * *", en, "en")).toBe("*/5 * * * *");
    expect(describeCron("", it_, "it")).toBe("Non definita");
    expect(describeCron(null, en, "en")).toBe("Not set");
  });
});

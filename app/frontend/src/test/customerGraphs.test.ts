import { describe, expect, it } from "vitest";

import { chartColumns } from "../api/customerGraphs";

describe("chartColumns", () => {
  it("turns API points into uPlot columns and keeps gaps", () => {
    const columns = chartColumns({
      id: "traffic", title: "", unit: "bps",
      series: [
        { label: "In", points: [["2026-10-14T10:05:00+00:00", 1000], ["2026-10-14T10:15:00+00:00", null]] },
        { label: "Out", points: [["2026-10-14T10:05:00+00:00", 200], ["2026-10-14T10:15:00+00:00", 300]] },
      ],
    });
    expect(columns.timestamps).toEqual([1791972300, 1791972900]);
    expect(columns.values).toEqual([[1000, null], [200, 300]]);
  });
});

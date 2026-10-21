import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import i18n from "../i18n";
import { HopBar, LogResult, RttChart } from "../pages/DeviceDiagnostics";

describe("diagnostic charts", () => {
  it("draws one bar per ping reply, lost replies in red, and the average line", async () => {
    await i18n.changeLanguage("it");
    const replies = [
      { seq: 0, host: "192.0.2.1", size: "56", ttl: "64", rtt: 10, status: "" },
      { seq: 1, host: "192.0.2.1", size: "56", ttl: "64", rtt: 20, status: "" },
      { seq: 2, host: null, size: null, ttl: null, rtt: null, status: "timeout" },
    ];
    const { container } = render(<RttChart replies={replies} avg={15} />);
    expect(container.querySelectorAll("rect.ui-rtt-bar")).toHaveLength(2);
    expect(container.querySelectorAll("rect.ui-rtt-lost")).toHaveLength(1);
    expect(container.querySelector("line.ui-rtt-avg")?.getAttribute("y1")).toBe("13");
    expect(screen.getByRole("img").getAttribute("aria-label")).toBe("3 risposte, massimo 20 ms, 1 perse");
    expect(screen.getByText("1 persa")).toBeTruthy();
  });

  it("scales hop bars on the slowest hop and colours them by loss", () => {
    const hop = { hop: 2, address: "198.51.100.1", loss: 10, last: 12, avg: 25, best: 10, worst: 40, silent: false };
    const { container } = render(<HopBar hop={hop} scale={50} />);
    expect(container.querySelector("rect.ui-hop-avg")?.getAttribute("width")).toBe("50");
    expect(container.querySelector("rect.ui-hop-range.is-warning")?.getAttribute("x")).toBe("20");
    const silent = render(<HopBar hop={{ ...hop, silent: true, avg: null }} scale={50} />);
    expect(silent.container.querySelector("svg")).toBeNull();
  });

  it("filters log lines by level", async () => {
    await i18n.changeLanguage("en");
    const entries = [
      { time: "10:00:00", topics: "system,error", message: "login failure", level: "error" },
      { time: "10:00:01", topics: "interface,warning", message: "ether1 link down", level: "warning" },
      { time: "10:00:02", topics: "system,info", message: "user logged in", level: "info" },
    ];
    render(<LogResult entries={entries} />);
    expect(screen.getAllByRole("row")).toHaveLength(4);
    fireEvent.click(screen.getByRole("button", { name: /Warning/ }));
    expect(screen.getAllByRole("row")).toHaveLength(2);
    expect(screen.getByText("ether1 link down")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: /All/ }));
    expect(screen.getAllByRole("row")).toHaveLength(4);
  });
});

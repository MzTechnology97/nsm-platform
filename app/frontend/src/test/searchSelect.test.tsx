import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { SearchSelect, customerLabel } from "../app/SearchSelect";
import i18n from "../i18n";

const options = [
  { value: "1", label: "Niccolò Rossi" },
  { value: "2", label: "Chiara Bianchi" },
  { value: "3", label: "Esempio Rete S.R.L." },
  ...Array.from({ length: 150 }, (_, i) => ({ value: `x${i}`, label: `Cliente Esempio ${i}` })),
];

afterEach(() => cleanup());

describe("searchable select", () => {
  it("filters by every typed word, ignoring case and accents, and picks with the keyboard", async () => {
    await i18n.changeLanguage("it");
    const onChange = vi.fn();
    render(<SearchSelect value="" options={options} onChange={onChange} ariaLabel="Cliente" />);
    const input = screen.getByRole("combobox", { name: "Cliente" });
    fireEvent.focus(input);
    fireEvent.change(input, { target: { value: "nicco ROSS" } });
    expect(screen.getAllByRole("option").map((o) => o.textContent)).toEqual(["Niccolò Rossi"]);
    fireEvent.keyDown(input, { key: "Enter" });
    expect(onChange).toHaveBeenCalledWith("1");
  });

  it("shows the customer code next to the name and finds a customer by code", async () => {
    await i18n.changeLanguage("it");
    const onChange = vi.fn();
    const customers = [{ id: "a", name: "Mario Rossi", code: "C840" }, { id: "b", name: "Mario Rossi", code: "C618" }, { id: "c", name: "Senza Codice", code: null }];
    render(<SearchSelect value="" options={customers.map((c) => ({ value: c.id, label: customerLabel(c) }))} onChange={onChange} ariaLabel="Cliente" />);
    const input = screen.getByRole("combobox", { name: "Cliente" });
    fireEvent.focus(input);
    expect(screen.getAllByRole("option").map((o) => o.textContent)).toEqual(["Mario Rossi · C840", "Mario Rossi · C618", "Senza Codice"]);
    fireEvent.change(input, { target: { value: "c618" } });
    expect(screen.getAllByRole("option").map((o) => o.textContent)).toEqual(["Mario Rossi · C618"]);
  });

  it("offers the empty choice first, caps long lists and says how many are left", async () => {
    await i18n.changeLanguage("it");
    const onChange = vi.fn();
    render(<SearchSelect value="3" options={options} onChange={onChange} emptyLabel="Tutti i clienti" ariaLabel="Cliente" />);
    const input = screen.getByRole("combobox", { name: "Cliente" });
    expect((input as HTMLInputElement).value).toBe("Esempio Rete S.R.L.");
    fireEvent.focus(input);
    const shown = screen.getAllByRole("option");
    expect(shown[0].textContent).toBe("Tutti i clienti");
    expect(shown).toHaveLength(101);
    expect(screen.getByText(/Altri 53/)).toBeTruthy();
    fireEvent.mouseDown(shown[0]);
    expect(onChange).toHaveBeenCalledWith("");
    fireEvent.focus(input);
    fireEvent.change(input, { target: { value: "zzz" } });
    expect(screen.getByText("Nessun risultato.")).toBeTruthy();
  });
});

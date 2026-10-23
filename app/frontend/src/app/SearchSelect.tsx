import { type KeyboardEvent, useEffect, useId, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";

export interface SearchOption {
  value: string;
  label: string;
}

const MAX_SHOWN = 100;

/** "Name · code" of a customer, so the same name can be told apart and the code can be searched. */
export const customerLabel = (customer: { name: string; code?: string | null }) => (customer.code ? `${customer.name} · ${customer.code}` : customer.name);
const fold = (text: string) => text.normalize("NFD").replace(/[̀-ͯ]/g, "").toLowerCase();
/** Every typed word must appear in the label, in any order ("nicco ross" finds "Niccolò Rossi"). */
const matches = (label: string, words: string[]) => {
  const text = fold(label);
  return words.every((word) => text.includes(word));
};

/** A select with a search box, for long lists such as hundreds of customers: type to filter (case and accents ignored), arrows and Enter to choose. */
export function SearchSelect({ value, options, onChange, placeholder, emptyLabel, required, disabled, ariaLabel }: {
  value: string;
  options: SearchOption[];
  onChange: (value: string) => void;
  placeholder?: string;
  /** Label of the "no value" choice, offered first when the field is optional. */
  emptyLabel?: string;
  required?: boolean;
  disabled?: boolean;
  ariaLabel?: string;
}) {
  const { t } = useTranslation();
  const listId = useId();
  const box = useRef<HTMLDivElement>(null);
  const [open, setOpen] = useState(false);
  const [text, setText] = useState("");
  const [active, setActive] = useState(0);
  const selected = options.find((option) => option.value === value);

  const [choices, hidden] = useMemo(() => {
    const words = fold(text).split(/\s+/).filter(Boolean);
    const found = words.length ? options.filter((option) => matches(option.label, words)) : options;
    const list = found.slice(0, MAX_SHOWN);
    return [emptyLabel && !words.length ? [{ value: "", label: emptyLabel }, ...list] : list, found.length - list.length] as const;
  }, [options, text, emptyLabel]);

  useEffect(() => {
    if (!open) return undefined;
    const close = (event: MouseEvent) => {
      if (box.current && !box.current.contains(event.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", close);
    return () => document.removeEventListener("mousedown", close);
  }, [open]);

  const choose = (option: SearchOption) => {
    onChange(option.value);
    setText("");
    setOpen(false);
  };

  const onKey = (event: KeyboardEvent<HTMLInputElement>) => {
    if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      event.preventDefault();
      setOpen(true);
      setActive((index) => Math.max(0, Math.min(choices.length - 1, index + (event.key === "ArrowDown" ? 1 : -1))));
    } else if (event.key === "Enter" && open) {
      event.preventDefault();
      if (choices[active]) choose(choices[active]);
    } else if (event.key === "Escape") {
      setOpen(false);
      setText("");
    }
  };

  return (
    <div className="ui-search-select" ref={box}>
      <input
        role="combobox"
        aria-expanded={open}
        aria-controls={listId}
        aria-autocomplete="list"
        aria-label={ariaLabel}
        aria-required={required}
        value={open ? text : selected?.label ?? ""}
        placeholder={selected ? selected.label : placeholder ?? emptyLabel ?? t("searchSelect.placeholder")}
        disabled={disabled}
        onFocus={() => { setOpen(true); setActive(0); }}
        onChange={(event) => { setText(event.target.value); setOpen(true); setActive(0); }}
        onKeyDown={onKey}
        autoComplete="off"
        spellCheck={false}
      />
      {open && !disabled && (
        <ul className="ui-search-select-list" id={listId} role="listbox">
          {choices.length === 0 && <li className="ui-search-select-empty">{t("searchSelect.none")}</li>}
          {choices.map((option, index) => (
            <li key={option.value || "__empty"} role="option" aria-selected={option.value === value}
              className={`ui-search-select-item${index === active ? " is-active" : ""}${option.value === value ? " is-selected" : ""}`}
              onMouseDown={(event) => { event.preventDefault(); choose(option); }} onMouseEnter={() => setActive(index)}>
              {option.label}
            </li>
          ))}
          {hidden > 0 && <li className="ui-search-select-empty">{t("searchSelect.more", { count: hidden })}</li>}
        </ul>
      )}
    </div>
  );
}

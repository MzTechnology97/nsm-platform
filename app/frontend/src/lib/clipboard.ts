/**
 * Copy text to the clipboard. ``navigator.clipboard`` exists only on HTTPS (or localhost): when NSM is reached over
 * plain HTTP the text is copied through a hidden text area and ``document.execCommand("copy")`` instead.
 * Resolves to false when the browser refuses both, so the caller can ask the user to copy by hand.
 */
export async function copyText(text: string): Promise<boolean> {
  if (window.isSecureContext && navigator.clipboard?.writeText) {
    try {
      await navigator.clipboard.writeText(text);
      return true;
    } catch {
      // fall back below
    }
  }
  const area = document.createElement("textarea");
  area.value = text;
  area.setAttribute("readonly", "");
  area.className = "ui-copy-buffer";
  document.body.appendChild(area);
  area.select();
  area.setSelectionRange(0, text.length);
  let ok = false;
  try {
    ok = document.execCommand("copy");
  } catch {
    ok = false;
  }
  area.remove();
  return ok;
}

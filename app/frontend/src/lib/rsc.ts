/** RouterOS export (.rsc) lines split into tokens for highlighting: section paths, commands, key=value pairs, strings, comments. */
export type RscToken = { kind: "path" | "command" | "key" | "value" | "string" | "comment" | "text"; text: string };

const COMMANDS = new Set(["add", "set", "remove", "disable", "enable", "print", "export", "move", "unset", "edit", "comment", "find"]);

export function tokenizeRsc(line: string): RscToken[] {
  const trimmed = line.trimStart();
  if (!trimmed) return [{ kind: "text", text: line }];
  if (trimmed.startsWith("#")) return [{ kind: "comment", text: line }];
  const out: RscToken[] = [];
  let rest = line;
  if (trimmed.startsWith("/")) {
    // "/ip firewall filter" alone, or "/ip address add ..." on one line: the path is every word up to the first command or key=value.
    const words = trimmed.split(/(\s+)/);
    let path = "";
    for (const word of words) {
      if (COMMANDS.has(word) || word.includes("=")) break;
      path += word;
    }
    const lead = line.length - trimmed.length;
    const trimmedPath = path.trimEnd();
    out.push({ kind: "text", text: line.slice(0, lead) }, { kind: "path", text: trimmedPath });
    rest = line.slice(lead + trimmedPath.length);
  }
  const pattern = /("(?:[^"\\]|\\.)*"?)|([^\s=]+)=("(?:[^"\\]|\\.)*"?|[^\s]*)|(\s+)|([^\s]+)/g;
  for (const match of rest.matchAll(pattern)) {
    const [, quoted, key, value, space, word] = match;
    if (quoted !== undefined) out.push({ kind: "string", text: quoted });
    else if (key !== undefined) {
      out.push({ kind: "key", text: key }, { kind: "text", text: "=" });
      if (value) out.push({ kind: value.startsWith('"') ? "string" : "value", text: value });
    } else if (space !== undefined) out.push({ kind: "text", text: space });
    else if (word !== undefined) {
      out.push({ kind: COMMANDS.has(word) ? "command" : "text", text: word });
    }
  }
  return out.filter((token) => token.text !== "");
}

/** Section headers ("/ip firewall filter") with the 1-based line where each starts and how many lines it has. */
export function rscSections(lines: string[]): { path: string; line: number; size: number }[] {
  const sections: { path: string; line: number; size: number }[] = [];
  lines.forEach((text, index) => {
    const trimmed = text.trimStart();
    if (!trimmed.startsWith("/")) return;
    const path = tokenizeRsc(text).find((token) => token.kind === "path")?.text;
    if (!path) return;
    const last = sections.at(-1);
    if (last && last.path === path) return;
    sections.push({ path, line: index + 1, size: 0 });
  });
  sections.forEach((section, index) => {
    section.size = (sections[index + 1]?.line ?? lines.length + 1) - section.line;
  });
  return sections;
}

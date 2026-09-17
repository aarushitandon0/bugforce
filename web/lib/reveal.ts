import { byteRangeToIndices } from "./format";

/**
 * Splits the mutated line around the single changed token, for the reveal.
 *
 * The site's col_start/col_end are UTF-8 byte offsets into original_line
 * (bugforge/models.py). Every byte outside that range is untouched by
 * bugforge/mutate.py, so the mutated line is before + mutated_token + after.
 * If the offsets ever disagree with the text, the token is located by
 * content instead, and as a last resort by the common prefix and suffix.
 */

export interface TokenSplit {
  before: string;
  token: string;
  after: string;
}

export interface RevealSite {
  original_line: string;
  mutated_line: string;
  original_token: string;
  mutated_token: string;
  col_start: number;
  col_end: number;
}

export interface RevealSplit {
  original: TokenSplit;
  mutated: TokenSplit;
}

function splitPair(site: RevealSite, start: number): RevealSplit | null {
  const { original_line: a, mutated_line: b, original_token: from, mutated_token: to } = site;
  if (a.slice(start, start + from.length) !== from) return null;
  const before = a.slice(0, start);
  const after = a.slice(start + from.length);
  if (b !== before + to + after) return null;
  return {
    original: { before, token: from, after },
    mutated: { before, token: to, after },
  };
}

export function splitReveal(site: RevealSite): RevealSplit {
  const [start] = byteRangeToIndices(site.original_line, site.col_start, site.col_end);
  const byOffset = splitPair(site, start);
  if (byOffset) return byOffset;

  if (site.original_token) {
    for (let i = site.original_line.indexOf(site.original_token); i !== -1; i = site.original_line.indexOf(site.original_token, i + 1)) {
      const byContent = splitPair(site, i);
      if (byContent) return byContent;
    }
  }

  const a = site.original_line;
  const b = site.mutated_line;
  let prefix = 0;
  while (prefix < a.length && prefix < b.length && a[prefix] === b[prefix]) prefix++;
  let suffix = 0;
  while (suffix < a.length - prefix && suffix < b.length - prefix && a[a.length - 1 - suffix] === b[b.length - 1 - suffix]) suffix++;
  return {
    original: { before: a.slice(0, prefix), token: a.slice(prefix, a.length - suffix), after: a.slice(a.length - suffix) },
    mutated: { before: b.slice(0, prefix), token: b.slice(prefix, b.length - suffix), after: b.slice(b.length - suffix) },
  };
}

/** Leading whitespace shared by both lines, removed so the big diff isn't mostly indent. */
export function dedentSplit(split: RevealSplit): RevealSplit {
  const indent = /^[ \t]*/.exec(split.original.before)![0].length;
  return {
    original: { ...split.original, before: split.original.before.slice(indent) },
    mutated: { ...split.mutated, before: split.mutated.before.slice(indent) },
  };
}

export type DiffLineKind = "file" | "hunk" | "context" | "removed" | "added";

export interface DiffLine {
  kind: DiffLineKind;
  text: string;
  /** line number in the original file, for context and removed lines */
  oldLine: number | null;
  /** line number in the mutated file, for context and added lines */
  newLine: number | null;
}

/** A unified diff as display rows, with each side's line numbers. */
export function parseUnifiedDiff(diff: string): DiffLine[] {
  const rows: DiffLine[] = [];
  let oldLine = 0;
  let newLine = 0;
  const lines = diff.replace(/\r\n?/g, "\n").split("\n");
  if (lines[lines.length - 1] === "") lines.pop();
  for (const raw of lines) {
    const hunk = /^@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@/.exec(raw);
    if (hunk) {
      oldLine = Number(hunk[1]);
      newLine = Number(hunk[2]);
      rows.push({ kind: "hunk", text: raw, oldLine: null, newLine: null });
    } else if (raw.startsWith("--- ") || raw.startsWith("+++ ")) {
      rows.push({ kind: "file", text: raw, oldLine: null, newLine: null });
    } else if (raw.startsWith("-")) {
      rows.push({ kind: "removed", text: raw.slice(1), oldLine: oldLine++, newLine: null });
    } else if (raw.startsWith("+")) {
      rows.push({ kind: "added", text: raw.slice(1), oldLine: null, newLine: newLine++ });
    } else if (raw.startsWith("\\")) {
      continue; // "\ No newline at end of file"
    } else {
      rows.push({ kind: "context", text: raw.slice(1), oldLine: oldLine++, newLine: newLine++ });
    }
  }
  return rows;
}

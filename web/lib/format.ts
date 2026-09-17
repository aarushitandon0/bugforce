export function plural(count: number, singular: string, pluralForm = `${singular}s`): string {
  return `${count} ${count === 1 ? singular : pluralForm}`;
}

export function thousands(n: number): string {
  return n.toLocaleString("en-US");
}

/** 271000 -> "04:31"; an hour or more -> "1:02:03". */
export function clock(ms: number): string {
  const total = Math.max(0, Math.floor(ms / 1000));
  const h = Math.floor(total / 3600);
  const m = Math.floor((total % 3600) / 60);
  const s = total % 60;
  const mm = String(m).padStart(2, "0");
  const ss = String(s).padStart(2, "0");
  return h > 0 ? `${h}:${mm}:${ss}` : `${mm}:${ss}`;
}

export function slug(text: string): string {
  return text
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "");
}

/** "jd__tenacity" -> "jd/tenacity" (repo names use "__" for the owner separator). */
export function repoDisplay(repo: string): string {
  return repo.replace("__", "/");
}

/** "jd__tenacity" -> "tenacity" */
export function repoShort(repo: string): string {
  const display = repoDisplay(repo);
  return display.slice(display.indexOf("/") + 1);
}

export function repoGithubUrl(repo: string): string {
  return `https://github.com/${repoDisplay(repo)}`;
}

/**
 * Accepts what people paste: "jd/tenacity", "github.com/jd/tenacity",
 * "https://github.com/jd/tenacity.git/", "git@github.com:jd/tenacity.git".
 * Returns "owner/name" or null.
 */
export function parseRepoInput(raw: string): string | null {
  const cleaned = raw
    .trim()
    .replace(/^git@github\.com:/i, "")
    .replace(/^https?:\/\//i, "")
    .replace(/^(?:www\.)?github\.com\//i, "")
    .replace(/\/+$/, "")
    .replace(/\.git$/i, "")
    .replace(/\/+$/, "");
  const match = /^([A-Za-z0-9](?:[A-Za-z0-9-]{0,38}))\/([A-Za-z0-9._-]{1,100})$/.exec(cleaned);
  return match ? `${match[1]}/${match[2]}` : null;
}

/** "https://github.com/JD/tenacity.git" -> "jd/tenacity", for comparing against the vetted list. */
export function normalizeRepoUrl(url: string): string {
  return (parseRepoInput(url) ?? url).toLowerCase();
}

/** Python's ast reports UTF-8 byte offsets; JS strings index UTF-16 code units. */
export function byteRangeToIndices(text: string, start: number, end: number): [number, number] {
  const bytes = new TextEncoder().encode(text);
  const decoder = new TextDecoder();
  return [
    decoder.decode(bytes.subarray(0, start)).length,
    decoder.decode(bytes.subarray(0, end)).length,
  ];
}

export function byteLength(text: string): number {
  return new TextEncoder().encode(text).length;
}

/** Keeps the end of a path, which is the informative part. */
export function truncateLeft(text: string, max: number): string {
  return text.length <= max ? text : "…" + text.slice(text.length - max + 1);
}

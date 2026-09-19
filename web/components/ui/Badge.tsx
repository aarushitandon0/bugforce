import type { ReactNode } from "react";

/**
 * Three kinds of badge, and the kind decides the colour treatment.
 *
 * `difficulty` is the only one that gets a colour ramp, and all three bands
 * carry one -- an uncoloured "medium" next to a green "easy" reads as a
 * missing state rather than a middle one.
 *
 * `language` is deliberately neutral: it is a fact about the repo, not a
 * judgement, and colouring it competed with the difficulty band.
 *
 * `status` borrows the terminal vocabulary (keep / drop / gap) so a verdict
 * means the same thing on a card as it does in the stream.
 */

export type DifficultyBand = "easy" | "medium" | "hard";
export type StatusKind = "keep" | "drop" | "gap";

const BASE = "inline-flex items-center gap-1 rounded border px-2 t-label";

const DIFFICULTY: Record<DifficultyBand, string> = {
  easy: "border-keep/40 bg-keep/10 text-keep",
  medium: "border-count/40 bg-count/10 text-count",
  hard: "border-gap/40 bg-gap/10 text-gap",
};

const STATUS: Record<StatusKind, string> = {
  keep: "border-keep/40 bg-keep/10 text-keep",
  drop: "border-line bg-transparent text-drop",
  gap: "border-gap/40 bg-gap/10 text-gap",
};

export function DifficultyBadge({ band, title }: { band: DifficultyBand; title?: string }) {
  return (
    <span className={`${BASE} ${DIFFICULTY[band]}`} title={title}>
      {band}
    </span>
  );
}

export function LanguageBadge({ language }: { language: string }) {
  return <span className={`${BASE} border-line text-muted`}>{language.toLowerCase()}</span>;
}

export function StatusBadge({ kind, children }: { kind: StatusKind; children?: ReactNode }) {
  return <span className={`${BASE} ${STATUS[kind]}`}>{children ?? kind}</span>;
}

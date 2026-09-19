"use client";

import Link from "next/link";
import type { ChallengeCard as Card, DifficultyLabel } from "@/lib/api";
import { repoDisplay } from "@/lib/format";
import { DifficultyBars } from "./DifficultyBars";

export const LABEL_COLOR: Record<DifficultyLabel, string> = {
  easy: "text-keep",
  medium: "text-text",
  hard: "text-gap",
};

export function solveHref(challengeId: string): string {
  return `/solve/?id=${encodeURIComponent(challengeId)}`;
}

export function ChallengeCard({
  card,
  index,
  marker,
}: {
  card: Card;
  index?: number;
  marker?: "solved" | "next";
}) {
  return (
    // The title link is stretched over the whole card; the bars sit above it
    // so their tooltips stay reachable without nesting buttons inside a link.
    <article
      className={`group relative flex h-full flex-col border p-4 transition-colors duration-[120ms] hover:border-line-strong focus-within:border-line-strong ${
        marker === "next" ? "border-line-strong" : "border-line"
      }`}
    >
      <div className="flex items-start justify-between gap-4">
        <p className="label">
          {index !== undefined && <span className="mr-2 text-muted">{String(index).padStart(2, "0")}</span>}
          <span className={LABEL_COLOR[card.difficulty_label]}>{card.difficulty_label}</span>
          <span className="text-muted"> · {card.language.toLowerCase()}</span>
          {card.operator && (
            <span className="ml-2 border border-line px-1 py-px text-[9.5px] text-muted" title="mutation operator">
              {card.operator}
            </span>
          )}
        </p>
        <div className="relative z-10">
          <DifficultyBars
            breakdown={card.breakdown}
            failing={card.failing_test_count}
            total={card.total_tests}
            size="lg"
          />
        </div>
      </div>

      <h3 className="mt-1 min-w-0 break-words text-[17px] font-bold leading-snug text-text">
        <Link
          href={solveHref(card.challenge_id)}
          className="outline-none after:absolute after:inset-0 after:content-['']"
        >
          {card.title || "untitled"}
        </Link>
      </h3>
      <p className="mt-2 min-w-0 flex-1 break-words text-text/90">{card.description}</p>

      <p className="mt-4 flex items-center justify-between gap-4 text-[11px] text-muted">
        <span>
          {repoDisplay(card.repo)}
          {card.license && <> · {card.license}</>}
        </span>
        {marker === "solved" ? (
          <span className="text-keep">✓ solved</span>
        ) : (
          <span className={`transition-colors duration-[120ms] group-hover:text-text ${marker === "next" ? "text-text" : "text-muted"}`}>
            {marker === "next" ? "next up →" : "solve →"}
          </span>
        )}
      </p>
    </article>
  );
}

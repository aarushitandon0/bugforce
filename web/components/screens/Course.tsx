"use client";

import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { useEffect, useState } from "react";
import { getChallenges, getRepos, type DifficultyLabel } from "@/lib/api";
import { plural, repoDisplay, repoShort } from "@/lib/format";
import { readSolved } from "@/lib/progress";
import { useApi } from "@/lib/useApi";
import { ChallengeCard } from "../ChallengeCard";
import { ErrorLine, Loading, PageHeader } from "../Status";

const FILTERS: Array<DifficultyLabel | "all"> = ["all", "easy", "medium", "hard"];

export function Course() {
  const repo = useSearchParams().get("name") ?? "";
  const challenges = useApi(repo ? () => getChallenges(repo) : null, `course:${repo}`);
  const repos = useApi(getRepos, "repos");
  const [filter, setFilter] = useState<DifficultyLabel | "all">("all");
  const [solved, setSolved] = useState<Set<string>>(new Set());

  useEffect(() => setSolved(readSolved()), []);

  if (!repo) return <ErrorLine message="no repo named in the URL" />;

  const list = challenges.data?.challenges ?? [];
  const meta = repos.data?.repos.find((r) => r.repo === repo);
  const shown = filter === "all" ? list : list.filter((c) => c.difficulty_label === filter);
  const nextUp = list.find((c) => !solved.has(c.challenge_id))?.challenge_id;
  const solvedCount = list.filter((c) => solved.has(c.challenge_id)).length;

  return (
    <>
      <PageHeader eyebrow={`course · ${repoDisplay(repo)}`} title={<>learn {repoShort(repo)} in {plural(list.length, "bug")}.</>}>
        Ordered easiest first. Each one is a real, single-token mutation of {repoDisplay(repo)} that its own test suite
        catches. You get the broken tree and the stack trace; the suite decides whether you fixed it.
      </PageHeader>

      {challenges.loading && <Loading text="loading challenges" />}
      {challenges.error && <ErrorLine message={challenges.error} onRetry={challenges.reload} />}

      {challenges.data && (
        <>
          <div className="flex flex-wrap items-center justify-between gap-4 border-y border-line py-2 text-[12px]">
            <div className="flex gap-5" role="group" aria-label="filter by difficulty">
              {FILTERS.map((f) => {
                const count = f === "all" ? list.length : list.filter((c) => c.difficulty_label === f).length;
                return (
                  <button
                    key={f}
                    type="button"
                    aria-pressed={filter === f}
                    onClick={() => setFilter(f)}
                    className={`border-b py-0.5 transition-colors duration-[120ms] ${
                      filter === f ? "border-text text-text" : "border-transparent text-dim hover:text-text"
                    }`}
                  >
                    {f} <span className="text-dim">{count}</span>
                  </button>
                );
              })}
            </div>
            <div className="flex gap-5 text-dim">
              <span className="tabular-nums">
                <span className={solvedCount ? "text-success" : ""}>{solvedCount}</span> of {list.length} solved
              </span>
              {meta?.license && <span>{meta.license}</span>}
              {meta && meta.gap_count > 0 && (
                <Link href={`/gaps/?repo=${encodeURIComponent(repo)}`} className="link">
                  {plural(meta.gap_count, "test gap")} →
                </Link>
              )}
            </div>
          </div>

          {list.length === 0 && <p className="py-6 text-dim">no challenges for {repoDisplay(repo)} yet.</p>}

          <ol className="mt-4 grid grid-cols-1 gap-3 md:grid-cols-2">
            {shown.map((card) => (
              <li key={card.challenge_id} className="relative flex min-w-0">
                <div className="min-w-0 flex-1">
                  <ChallengeCard
                    card={card}
                    index={list.indexOf(card) + 1}
                    marker={solved.has(card.challenge_id) ? "solved" : card.challenge_id === nextUp ? "next" : undefined}
                  />
                </div>
              </li>
            ))}
          </ol>
        </>
      )}
    </>
  );
}

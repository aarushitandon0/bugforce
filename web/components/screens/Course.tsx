"use client";

import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { useEffect, useMemo, useState } from "react";
import { getChallenges, getRepos, type ChallengeCard as Card, type DifficultyLabel } from "@/lib/api";
import { challengeScope, plural, repoDisplay, repoShort } from "@/lib/format";
import { loadSolved } from "@/lib/progress";
import { useSession } from "@/lib/session";
import { useApi } from "@/lib/useApi";
import { ChallengeCard } from "../ChallengeCard";
import { ErrorLine, Loading, PageHeader } from "../Status";
import { StatLine } from "../ui/StatLine";

const FILTERS: Array<DifficultyLabel | "all"> = ["all", "easy", "medium", "hard"];

type Sort = "difficulty" | "scope";

/* Two selects with the same box. Not a Button: they are not actions, and
   giving them the secondary treatment made them read as things to press. */
const CONTROL =
  "h-8 rounded border border-line bg-surface-1 px-2 t-small text-text transition-colors duration-[120ms] hover:bg-surface-2";

export function Course() {
  const repo = useSearchParams().get("name") ?? "";
  const challenges = useApi(repo ? () => getChallenges(repo) : null, `course:${repo}`);
  const repos = useApi(getRepos, "repos");
  const [filter, setFilter] = useState<DifficultyLabel | "all">("all");
  const [scope, setScope] = useState("all");
  const [sort, setSort] = useState<Sort>("difficulty");
  const [solved, setSolved] = useState<Set<string>>(new Set());

  // Re-read when the signed-in user changes: signing in mid-course should
  // pull in what you solved on another device without a reload.
  const { user } = useSession();
  useEffect(() => {
    let live = true;
    loadSolved(repo).then((s) => live && setSolved(s));
    return () => {
      live = false;
    };
  }, [repo, user?.user_id]);

  const list = useMemo(() => challenges.data?.challenges ?? [], [challenges.data]);

  /* The order the course is taught in -- easiest first -- and the index
     printed on each card. Both filters and both sorts are views over it, so
     card 03 stays card 03 whichever way the grid is arranged. */
  const position = useMemo(() => new Map(list.map((c, i) => [c.challenge_id, i + 1])), [list]);

  const scopes = useMemo(() => {
    const counts = new Map<string, number>();
    for (const card of list) {
      const key = challengeScope(card.title);
      counts.set(key, (counts.get(key) ?? 0) + 1);
    }
    return [...counts.entries()].sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]));
  }, [list]);

  const shown = useMemo(() => {
    let out: Card[] = list;
    if (filter !== "all") out = out.filter((c) => c.difficulty_label === filter);
    if (scope !== "all") out = out.filter((c) => challengeScope(c.title) === scope);
    if (sort === "scope") {
      // Cluster related bugs; inside a cluster keep the taught order.
      out = [...out].sort(
        (a, b) =>
          challengeScope(a.title).localeCompare(challengeScope(b.title)) ||
          (position.get(a.challenge_id) ?? 0) - (position.get(b.challenge_id) ?? 0),
      );
    }
    return out;
  }, [list, filter, scope, sort, position]);

  if (!repo) return <ErrorLine message="no repo named in the URL" />;

  const meta = repos.data?.repos.find((r) => r.repo === repo);
  const nextUp = list.find((c) => !solved.has(c.challenge_id))?.challenge_id;
  const solvedCount = list.filter((c) => solved.has(c.challenge_id)).length;

  return (
    <>
      <PageHeader
        eyebrow={`course · ${repoDisplay(repo)}`}
        title={
          <>
            learn {repoShort(repo)} in {plural(list.length, "bug")}.
          </>
        }
      >
        Ordered easiest first. Each one is a real, single-token break in {repoDisplay(repo)} that its own test suite
        catches. You get the broken tree and the stack trace; the suite decides whether you fixed it.
      </PageHeader>

      {challenges.loading && <Loading text="loading bugs" />}
      {challenges.error && <ErrorLine message={challenges.error} onRetry={challenges.reload} />}

      {challenges.data && (
        <>
          {/*
           * Repo and licence live here, once. They used to repeat at the foot
           * of every card on a page whose own title already names the repo.
           */}
          <div className="flex flex-wrap items-baseline justify-between gap-4 border-b border-line pb-3">
            <StatLine
              stats={[
                { value: repoDisplay(repo), label: meta?.license || "license unknown" },
                {
                  value: solvedCount,
                  label: `of ${list.length} solved`,
                  tone: solvedCount ? "text-keep" : "text-text",
                },
              ]}
            />
            {meta && meta.gap_count > 0 && (
              <Link href={`/gaps/?repo=${encodeURIComponent(repo)}`} className="link t-small">
                {plural(meta.gap_count, "test gap")} &rarr;
              </Link>
            )}
          </div>

          <div className="flex flex-wrap items-center justify-between gap-4 border-b border-line py-2">
            <div className="flex flex-wrap gap-5" role="group" aria-label="filter by difficulty">
              {FILTERS.map((f) => {
                const count = f === "all" ? list.length : list.filter((c) => c.difficulty_label === f).length;
                return (
                  <button
                    key={f}
                    type="button"
                    aria-pressed={filter === f}
                    onClick={() => setFilter(f)}
                    className={`t-small border-b py-0.5 transition-colors duration-[120ms] ${
                      filter === f ? "border-text text-text" : "border-transparent text-muted hover:text-text"
                    }`}
                  >
                    {f} <span className="tabular-nums text-muted">{count}</span>
                  </button>
                );
              })}
            </div>

            {/*
             * Difficulty tabs alone do not make a 56-card grid navigable.
             * Scope rather than file: the file a bug lives in is withheld on
             * purpose, and the enclosing scope is already on every card.
             */}
            <div className="flex flex-wrap items-center gap-2">
              <label className="t-small text-muted" htmlFor="scope-filter">
                scope
              </label>
              <select id="scope-filter" value={scope} onChange={(e) => setScope(e.target.value)} className={CONTROL}>
                <option value="all">all ({list.length})</option>
                {scopes.map(([name, count]) => (
                  <option key={name} value={name}>
                    {name} ({count})
                  </option>
                ))}
              </select>
              <label className="t-small text-muted" htmlFor="sort-order">
                sort
              </label>
              <select
                id="sort-order"
                value={sort}
                onChange={(e) => setSort(e.target.value as Sort)}
                className={CONTROL}
              >
                <option value="difficulty">by difficulty</option>
                <option value="scope">by scope</option>
              </select>
            </div>
          </div>

          {list.length === 0 && <p className="py-6 text-muted">no bugs forged for {repoDisplay(repo)} yet.</p>}
          {list.length > 0 && shown.length === 0 && <p className="py-6 text-muted">nothing matches that filter.</p>}

          <ol className="mt-4 grid grid-cols-1 gap-3 md:grid-cols-2">
            {shown.map((card) => (
              <li key={card.challenge_id} className="relative flex min-w-0">
                <div className="min-w-0 flex-1">
                  <ChallengeCard
                    card={card}
                    index={position.get(card.challenge_id)}
                    marker={
                      solved.has(card.challenge_id) ? "solved" : card.challenge_id === nextUp ? "next" : undefined
                    }
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

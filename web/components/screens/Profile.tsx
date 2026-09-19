"use client";

/**
 * The learner's own record: what has been solved, when, and how it is spread
 * across difficulty.
 *
 * Read-only and derived. Every number here comes from endpoints that already
 * existed -- GET /me/progress, /leaderboard, /repos and /challenges -- joined
 * in the browser. Nothing new is written, and no other screen's behaviour
 * changes: a profile that fails to load costs a profile, not a solve.
 *
 * Signed out there is still something to show. localStorage carries the solved
 * set for an anonymous learner (see lib/progress.ts), so the difficulty
 * breakdown and the totals work without a session; only the rank and the
 * per-solve timestamps need one, and those sections say so rather than
 * rendering as zeroes.
 */

import Link from "next/link";
import { useEffect, useMemo, useState } from "react";
import {
  getChallenges,
  getLeaderboard,
  getProgress,
  getRepos,
  type ChallengeCard,
  type DifficultyLabel,
  type LeaderboardRow,
  type ProgressResponse,
} from "@/lib/api";
import { clock, plural, repoDisplay, thousands } from "@/lib/format";
import {
  ago,
  BANDS,
  byRepo,
  calendarWeeks,
  dayKey,
  mergeSolves,
  tally,
  streaks,
  type Solve,
} from "@/lib/profile";
import { readSolved } from "@/lib/progress";
import { useSession } from "@/lib/session";
import { solveHref } from "../ChallengeCard";
import { ErrorLine, Loading, PageHeader } from "../Status";
import { Panel } from "../ui/Panel";

const BAND_TEXT: Record<DifficultyLabel, string> = {
  easy: "text-keep",
  medium: "text-count",
  hard: "text-gap",
};

const BAND_BG: Record<DifficultyLabel, string> = {
  easy: "bg-keep",
  medium: "bg-count",
  hard: "bg-gap",
};

export function Profile() {
  const { user, loading: sessionLoading } = useSession();
  const [progress, setProgress] = useState<ProgressResponse | null>(null);
  const [rank, setRank] = useState<LeaderboardRow | null>(null);
  const [catalogue, setCatalogue] = useState<ChallengeCard[] | null>(null);
  const [localSolved, setLocalSolved] = useState<Set<string>>(new Set());
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    setLocalSolved(readSolved());

    // Every challenge on every repo this stack serves, so a solved id can be
    // given a difficulty and a title. /repos first because /challenges is
    // per-repo; one stack is one repo today, and this still holds for more.
    const loadCatalogue = getRepos().then((r) =>
      Promise.all(r.repos.map((repo) => getChallenges(repo.repo))).then((pages) =>
        pages.flatMap((p) => p.challenges),
      ),
    );

    Promise.allSettled([loadCatalogue, getProgress(), getLeaderboard()]).then((results) => {
      if (cancelled) return;
      const [cat, prog, board] = results;
      if (cat.status === "fulfilled") setCatalogue(cat.value);
      else setError(cat.reason instanceof Error ? cat.reason.message : String(cat.reason));
      if (prog.status === "fulfilled") setProgress(prog.value);
      if (board.status === "fulfilled") setRank(board.value.leaderboard.find((row) => row.is_you) ?? null);
      setLoading(false);
    });

    return () => {
      cancelled = true;
    };
  }, [user?.user_id]);

  const byId = useMemo(
    () => new Map((catalogue ?? []).map((c) => [c.challenge_id, c])),
    [catalogue],
  );

  // The server's record and the local one are merged, never chosen between:
  // solving a few signed out and then signing in must not look like losing
  // them. lib/progress.ts makes the same choice for the tick marks.
  const solves = useMemo(() => mergeSolves(localSolved, progress, byId), [localSolved, progress, byId]);

  const totals = useMemo(() => tally(catalogue ?? [], solves), [catalogue, solves]);

  const dated = solves.filter((s): s is Solve & { at: number } => s.at !== null);
  const days = dated.map((s) => dayKey(s.at));
  const { longest, current } = streaks(days);
  const activeDays = new Set(days).size;
  const solvedCount = solves.length;
  const totalCount = catalogue?.length ?? 0;

  // Both, not either. The session answer is cached and lands almost at once,
  // while the four API calls behind these numbers serialize through the local
  // API and take a few seconds; gating on the session alone rendered a profile
  // full of zeroes ("1 of 0") until they arrived.
  if (loading || sessionLoading) return <Loading text="loading your profile" />;
  if (error && !catalogue) return <ErrorLine message={error} onRetry={() => window.location.reload()} />;

  return (
    <>
      <PageHeader eyebrow="profile" title={user ? user.login : "your record"}>
        {user ? (
          <p>
            Signed in as {user.login}
            {rank ? ` · rank ${thousands(rank.rank)} · ${rank.score.toFixed(1)} points` : ""}.
          </p>
        ) : (
          <p>
            Signed out. This is the record kept in this browser. Sign in to keep it across devices and to
            appear on the leaderboard.
          </p>
        )}
      </PageHeader>

      <div className="grid gap-4 lg:grid-cols-[320px_1fr]">
        <div className="flex flex-col gap-4">
          <IdentityCard login={user?.login ?? "local record"} avatar={user?.avatar_url} rank={rank} />
          <StatsCard
            activeDays={activeDays}
            longest={longest}
            current={current}
            attempts={dated.length}
            fastest={dated.reduce<number | null>(
              (best, s) => (s.seconds && (best === null || s.seconds < best) ? s.seconds : best),
              null,
            )}
          />
          <ReposCard solves={solves} />
        </div>

        <div className="flex flex-col gap-4">
          <SolvedCard solved={totals.solved} total={totals.total} count={solvedCount} outOf={totalCount} />
          <ActivityCard days={days} />
          <RecentCard solves={solves} />
        </div>
      </div>
    </>
  );
}

function IdentityCard({
  login,
  avatar,
  rank,
}: {
  login: string;
  avatar?: string;
  rank: LeaderboardRow | null;
}) {
  return (
    <Panel>
      <div className="flex items-center gap-3">
        {avatar ? (
          // eslint-disable-next-line @next/next/no-img-element -- one avatar, from github's CDN
          <img src={avatar} alt="" className="h-14 w-14 rounded border border-line" />
        ) : (
          <div className="flex h-14 w-14 items-center justify-center rounded border border-line bg-surface-3 text-xl text-muted">
            {login.slice(0, 1).toUpperCase()}
          </div>
        )}
        <div className="min-w-0">
          <p className="truncate font-bold text-text">{login}</p>
          <p className="t-small text-muted">
            {rank ? `rank ${thousands(rank.rank)}` : "unranked"}
          </p>
        </div>
      </div>
    </Panel>
  );
}

function StatsCard({
  activeDays,
  longest,
  current,
  attempts,
  fastest,
}: {
  activeDays: number;
  longest: number;
  current: number;
  attempts: number;
  fastest: number | null;
}) {
  const rows: [string, string][] = [
    ["active days", thousands(activeDays)],
    ["current streak", plural(current, "day")],
    ["longest streak", plural(longest, "day")],
    ["timed solves", thousands(attempts)],
    ["fastest solve", fastest === null ? "—" : clock(fastest * 1000)],
  ];
  return (
    <Panel header={<h2 className="label">stats</h2>}>
      <dl className="space-y-2">
        {rows.map(([label, value]) => (
          <div key={label} className="flex items-baseline justify-between gap-3 t-small">
            <dt className="text-muted">{label}</dt>
            <dd className="tabular-nums text-text">{value}</dd>
          </div>
        ))}
      </dl>
    </Panel>
  );
}

function ReposCard({ solves }: { solves: Solve[] }) {
  const rows = byRepo(solves);
  return (
    <Panel header={<h2 className="label">repos</h2>}>
      {rows.length === 0 ? (
        <p className="t-small text-muted">Nothing solved yet.</p>
      ) : (
        <dl className="space-y-2">
          {rows.map(([repo, count]) => (
            <div key={repo} className="flex items-baseline justify-between gap-3 t-small">
              <dt className="truncate text-muted">{repoDisplay(repo)}</dt>
              <dd className="shrink-0 tabular-nums text-text">{plural(count, "bug")}</dd>
            </div>
          ))}
        </dl>
      )}
    </Panel>
  );
}

function SolvedCard({
  solved,
  total,
  count,
  outOf,
}: {
  solved: Record<DifficultyLabel, number>;
  total: Record<DifficultyLabel, number>;
  count: number;
  outOf: number;
}) {
  const pct = outOf > 0 ? Math.round((count / outOf) * 100) : 0;
  return (
    <Panel header={<h2 className="label">solved bugs</h2>}>
      <div className="flex flex-wrap items-center gap-8">
        <Dial solved={count} total={outOf} pct={pct} />
        <dl className="min-w-[220px] flex-1 space-y-3">
          {BANDS.map((band) => {
            const done = solved[band];
            const all = total[band];
            const width = all > 0 ? (done / all) * 100 : 0;
            return (
              <div key={band}>
                <div className="flex items-baseline justify-between gap-3 t-small">
                  <dt className={`uppercase ${BAND_TEXT[band]}`}>{band}</dt>
                  <dd className="tabular-nums text-muted">
                    <span className="text-text">{thousands(done)}</span> / {thousands(all)}
                  </dd>
                </div>
                <div className="mt-1.5 h-1 w-full rounded bg-surface-3">
                  <div
                    className={`h-1 rounded ${BAND_BG[band]}`}
                    style={{ width: `${width}%` }}
                    role="progressbar"
                    aria-valuenow={done}
                    aria-valuemin={0}
                    aria-valuemax={all}
                    aria-label={`${band} solved`}
                  />
                </div>
              </div>
            );
          })}
        </dl>
      </div>
    </Panel>
  );
}

/** The donut. Stroke-dasharray on one circle, so there is no chart library here. */
function Dial({ solved, total, pct }: { solved: number; total: number; pct: number }) {
  const r = 42;
  const circumference = 2 * Math.PI * r;
  return (
    <div className="relative h-[120px] w-[120px] shrink-0">
      <svg width="120" height="120" viewBox="0 0 120 120" role="img" aria-label={`${solved} of ${total} solved`}>
        <circle cx="60" cy="60" r={r} fill="none" stroke="var(--color-line)" strokeWidth="8" />
        <circle
          cx="60"
          cy="60"
          r={r}
          fill="none"
          stroke="var(--color-keep)"
          strokeWidth="8"
          strokeLinecap="round"
          strokeDasharray={`${(pct / 100) * circumference} ${circumference}`}
          transform="rotate(-90 60 60)"
        />
      </svg>
      <div className="absolute inset-0 flex flex-col items-center justify-center">
        <span className="text-2xl tabular-nums text-text">{thousands(solved)}</span>
        {/* "1 of 0" is not a fact about anything; with no catalogue, say the count alone. */}
        <span className="t-small text-muted">{total > 0 ? `of ${thousands(total)}` : "solved"}</span>
      </div>
    </div>
  );
}

/**
 * A year of calendar cells, most recent week last.
 *
 * Solves only. There is no per-attempt log to draw from -- a submission row is
 * keyed by id, not by day -- so this is "days you fixed something", which is
 * what the streak counts too. Calling it submissions would overstate it.
 */
const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

/** The five shades, lightest to darkest, matching the legend below the grid. */
function cellClass(n: number): string {
  if (n === 0) return "bg-surface-3";
  if (n === 1) return "bg-keep/30";
  if (n === 2) return "bg-keep/55";
  if (n < 5) return "bg-keep/80";
  return "bg-keep";
}

/**
 * A year of activity, one column per week, the way GitHub and LeetCode draw it.
 *
 * Solves only. There is no per-attempt log to draw from: a submission row is
 * keyed by id, not by day, so "submissions" would overstate what this shows.
 * It is the same quantity the streak counts, which keeps the two honest with
 * each other.
 */
function ActivityCard({ days }: { days: number[] }) {
  const counts = new Map<number, number>();
  for (const d of days) counts.set(d, (counts.get(d) ?? 0) + 1);

  const today = dayKey(Date.now());
  const weeks = calendarWeeks();
  const active = new Set(days).size;

  // A month label sits above the first column whose month differs from the
  // column before it, so labels land where the month actually starts.
  const monthLabel = weeks.map((column, i) => {
    const month = new Date(column[0]).getMonth();
    if (i === 0) return null; // the first column is usually a partial week
    return month !== new Date(weeks[i - 1][0]).getMonth() ? MONTHS[month] : null;
  });

  return (
    <Panel
      header={
        <div className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1">
          <h2 className="label">activity</h2>
          <p className="t-small text-muted tabular-nums">
            {plural(days.length, "solve")} in the last year &middot; {plural(active, "active day")}
          </p>
        </div>
      }
    >
      <div className="overflow-x-auto pb-1">
        <div className="inline-flex flex-col gap-1">
          {/* month row, aligned to the columns it labels */}
          <div className="flex gap-[3px] pl-[28px]">
            {weeks.map((_, i) => (
              <div key={i} className="w-[11px] shrink-0 t-small text-faint">
                {monthLabel[i] && <span className="relative -top-px block whitespace-nowrap">{monthLabel[i]}</span>}
              </div>
            ))}
          </div>

          <div className="flex gap-[3px]">
            {/* weekday gutter: Mon, Wed, Fri, as both sites label it */}
            <div className="flex w-[25px] shrink-0 flex-col gap-[3px] pr-1 text-right">
              {["", "Mon", "", "Wed", "", "Fri", ""].map((label, i) => (
                <div key={i} className="h-[11px] text-[9px] leading-[11px] text-faint">
                  {label}
                </div>
              ))}
            </div>

            {weeks.map((column, i) => (
              <div key={i} className="flex flex-col gap-[3px]">
                {column.map((day) => {
                  const n = counts.get(day) ?? 0;
                  if (day > today) {
                    return <div key={day} className="h-[11px] w-[11px]" />;
                  }
                  return (
                    <div
                      key={day}
                      title={`${new Date(day).toDateString()}: ${plural(n, "bug")} fixed`}
                      className={`h-[11px] w-[11px] rounded-[2px] ${cellClass(n)}`}
                    />
                  );
                })}
              </div>
            ))}
          </div>
        </div>
      </div>

      <div className="mt-3 flex flex-wrap items-center justify-between gap-x-4 gap-y-2">
        <p className="t-small text-faint">
          {days.length === 0
            ? "No solves yet. Each cell is a day; it fills in when you fix a bug."
            : "One cell per day. Shade is how many bugs you fixed that day."}
        </p>
        <div className="flex items-center gap-1.5 t-small text-faint">
          <span>less</span>
          {[0, 1, 2, 4, 6].map((n) => (
            <span key={n} className={`h-[11px] w-[11px] rounded-[2px] ${cellClass(n)}`} />
          ))}
          <span>more</span>
        </div>
      </div>
    </Panel>
  );
}

function RecentCard({ solves }: { solves: Solve[] }) {
  const rows = solves.slice(0, 15);
  return (
    <Panel header={<h2 className="label">recent solves</h2>}>
      {rows.length === 0 ? (
        <p className="t-small text-muted">
          Nothing yet. Pick a repo on the <Link href="/repos/" className="link text-text">repos</Link> page and
          fix your first bug.
        </p>
      ) : (
        <ul className="divide-y divide-line">
          {rows.map((s) => (
            <li key={s.challengeId} className="flex items-baseline justify-between gap-4 py-2">
              <Link href={solveHref(s.challengeId)} className="min-w-0 flex-1 truncate text-text hover:text-keep">
                {s.title}
              </Link>
              <span className="flex shrink-0 items-baseline gap-3 t-small tabular-nums text-muted">
                {s.band && <span className={`uppercase ${BAND_TEXT[s.band]}`}>{s.band}</span>}
                {s.seconds ? <span>{clock(s.seconds * 1000)}</span> : null}
                <span className="text-faint">{s.at === null ? "this browser" : ago(s.at)}</span>
              </span>
            </li>
          ))}
        </ul>
      )}
    </Panel>
  );
}

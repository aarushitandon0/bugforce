"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { getRepos, type RepoSummary } from "@/lib/api";
import { GAPS_BY_FILE } from "@/lib/forge-data";
import { plural, repoDisplay, repoShort } from "@/lib/format";
import { useApi } from "@/lib/useApi";
import { ErrorLine, Loading, PageHeader } from "../Status";

export function courseHref(repo: string) {
  return `/repo/?name=${encodeURIComponent(repo)}`;
}

/**
 * Challenges per score band. It used to be 18px of 5px bars, which was a
 * texture rather than a chart; at 44px with the band under each bar it can
 * actually be read.
 */
function Histogram({ counts, edges }: { counts: number[]; edges: number[] }) {
  const max = Math.max(1, ...counts);
  const described = counts.map((c, i) => `${edges[i]}-${edges[i + 1]}: ${c}`).join(", ");
  return (
    <span
      className="inline-flex items-end gap-[3px]"
      role="img"
      aria-label={`difficulty histogram, ${described}`}
    >
      {counts.map((count, i) => (
        <span key={i} className="flex w-[16px] flex-col items-center gap-1" title={`score ${edges[i]}-${edges[i + 1]}: ${count}`}>
          <span className="text-[9px] leading-none tabular-nums text-dim">{count || ""}</span>
          <span className="flex h-[44px] w-full items-end border-b border-line">
            <span
              className={count ? "w-full bg-text" : "w-full"}
              style={{ height: count ? `${Math.max(8, (count / max) * 100)}%` : 0 }}
            />
          </span>
          <span className="text-[9px] leading-none tabular-nums text-dim">{edges[i]}</span>
        </span>
      ))}
    </span>
  );
}

/**
 * The gap report's headline, on the page that otherwise has one row and a lot
 * of empty space. It is the most interesting number the pipeline produces and
 * it was sitting unused.
 */
function GapSummary() {
  const { files, total, repo } = GAPS_BY_FILE;
  if (total === 0 || files.length === 0) return null;
  const top = files[0];
  const max = files[0].count;

  return (
    <section className="mt-10 border border-line" aria-label="test gap summary">
      <div className="flex flex-wrap items-baseline justify-between gap-3 border-b border-line px-4 py-2.5">
        <h2 className="label">where the tests are not looking</h2>
        <Link href={`/gaps/?repo=${encodeURIComponent(repo)}`} className="link text-[11px]">
          full report &rarr;
        </Link>
      </div>
      <div className="px-4 py-4">
        <p className="max-w-[72ch] text-[13px] leading-[1.7] text-text">
          <span className="text-error">{top.count}</span> of {total} mutations that no test noticed are in one file,{" "}
          <span className="text-text">{top.path}</span>. Every one of them is a line the suite executes but never
          checks the result of.
        </p>
        <ul className="mt-4 space-y-1.5">
          {files.map((file) => (
            <li key={file.path} className="flex items-center gap-3 text-[12px]">
              <span className="w-[26ch] shrink-0 truncate text-dim" title={file.path}>
                {file.path}
              </span>
              <span className="flex h-[10px] min-w-0 flex-1 items-center">
                <span
                  className="block h-[10px] bg-error"
                  style={{ width: `${(file.count / max) * 100}%` }}
                  aria-hidden
                />
              </span>
              <span className="w-[3ch] shrink-0 text-right tabular-nums text-text">{file.count}</span>
            </li>
          ))}
        </ul>
        <p className="mt-4 text-[11px] text-dim">
          counted from the last forge. a gap is a mutation the repo&apos;s own suite ran straight past.
        </p>
      </div>
    </section>
  );
}

export function Repos() {
  const router = useRouter();
  const { data, error, loading, reload } = useApi(getRepos, "repos");

  const repos: RepoSummary[] = data?.repos ?? [];
  const totalChallenges = repos.reduce((n, r) => n + r.challenge_count, 0);
  const totalGaps = repos.reduce((n, r) => n + r.gap_count, 0);

  return (
    <>
      <PageHeader title="repos">
        Every repo that has been forged. Pick one and learn its codebase the way its maintainers did: one real bug at
        a time, easiest first.
      </PageHeader>

      {loading && <Loading text="loading repos" />}
      {error && <ErrorLine message={error} onRetry={reload} />}

      {data && repos.length === 0 && (
        <p className="text-dim">
          nothing forged yet.{" "}
          <Link href="/" className="link">
            forge a repo →
          </Link>
        </p>
      )}

      {data && repos.length > 0 && (
        <>
          <p className="mb-3 text-[12px] text-dim">
            {plural(repos.length, "repo")} · {plural(totalChallenges, "challenge")} · {plural(totalGaps, "test gap")}
          </p>
          <div className="overflow-x-auto">
            <table className="w-full min-w-[760px] border-collapse text-left tabular-nums">
              <thead>
                <tr className="border-y border-line">
                  {["repo", "lang", "licence", "challenges", "difficulty", "avg score", "gaps", ""].map((h, i) => (
                    <th
                      key={h || i}
                      scope="col"
                      className={`label px-3 py-2 font-normal ${i === 3 || i === 5 || i === 6 ? "text-right" : ""}`}
                    >
                      {h}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {repos.map((repo) => (
                  <tr
                    key={repo.repo}
                    onClick={() => router.push(courseHref(repo.repo))}
                    className="cursor-pointer border-b border-line transition-colors duration-[120ms] hover:bg-panel"
                  >
                    <td className="px-3 py-3">
                      <Link href={courseHref(repo.repo)} className="font-bold text-text" onClick={(e) => e.stopPropagation()}>
                        {repoDisplay(repo.repo)}
                      </Link>
                    </td>
                    <td className="px-3 py-3 text-dim">{repo.language.toLowerCase()}</td>
                    <td className="px-3 py-3 text-dim">{repo.license || "—"}</td>
                    <td className="px-3 py-3 text-right text-text">{repo.challenge_count}</td>
                    <td className="px-3 py-3">
                      <Histogram counts={repo.histogram} edges={data.histogram_edges} />
                    </td>
                    <td className="px-3 py-3 text-right text-text">{repo.avg_difficulty.toFixed(1)}</td>
                    <td className="px-3 py-3 text-right">
                      <Link
                        href={`/gaps/?repo=${encodeURIComponent(repo.repo)}`}
                        onClick={(e) => e.stopPropagation()}
                        className={repo.gap_count ? "link text-error" : "text-dim"}
                      >
                        {repo.gap_count}
                      </Link>
                    </td>
                    <td className="px-3 py-3 text-right whitespace-nowrap text-dim">
                      learn {repoShort(repo.repo)} in {plural(repo.challenge_count, "bug")} →
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <p className="mt-3 text-[11px] text-dim">
            difficulty: challenges per score band, {data.histogram_edges[0]} to{" "}
            {data.histogram_edges[data.histogram_edges.length - 1]}, easiest on the left
          </p>

          <GapSummary />
        </>
      )}
    </>
  );
}

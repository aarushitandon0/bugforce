"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { getRepos, type ReposResponse, type RepoSummary } from "@/lib/api";
import { GAPS_BY_FILE } from "@/lib/forge-data";
import { plural, repoDisplay, repoShort } from "@/lib/format";
import { useApi } from "@/lib/useApi";
import { ErrorLine, Loading, PageHeader } from "../Status";
import { ArrowLink } from "../ui/ArrowLink";
import { LanguageBadge } from "../ui/Badge";
import { Panel } from "../ui/Panel";
import { StatLine } from "../ui/StatLine";

export function courseHref(repo: string) {
  return `/repo/?name=${encodeURIComponent(repo)}`;
}

/**
 * The difficulty spread, in words.
 *
 * This was a seven-bar histogram of a distribution that, at one repo and one
 * commit, occupies two adjacent bands. The bars were pure black -- heavier
 * than the page's own H1 -- to say something a line of text says exactly:
 * `avg 7.0 · range 6-7`. When there is enough spread to be worth a chart, the
 * chart can come back.
 */
function difficultySpread(repo: RepoSummary, edges: number[]): string {
  const filled = repo.histogram.flatMap((count, i) => (count > 0 ? [i] : []));
  if (filled.length === 0) return "—";
  const low = edges[filled[0]];
  const high = edges[filled[filled.length - 1] + 1];
  const avg = repo.avg_difficulty.toFixed(1);
  return low === high ? `avg ${avg}` : `avg ${avg} · range ${low}–${high}`;
}

/**
 * Below four repos a table is mostly chrome: five headers, one or two rows,
 * and a horizontal scrollbar on a phone. Cards carry the same facts and read
 * at any width. The table earns its keep once rows are worth comparing
 * column by column.
 */
const TABLE_AT = 4;

function RepoCard({ repo, edges }: { repo: RepoSummary; edges: number[] }) {
  return (
    <Panel className="group relative transition-colors duration-[120ms] hover:border-line-strong focus-within:border-line-strong">
      <div className="flex flex-wrap items-center gap-2">
        <LanguageBadge language={repo.language} />
        {repo.license && <span className="t-small text-faint">{repo.license}</span>}
      </div>
      <h2 className="t-h2 mt-2 break-words text-text">
        <Link href={courseHref(repo.repo)} className="after:absolute after:inset-0 after:content-['']">
          {repoDisplay(repo.repo)}
        </Link>
      </h2>
      <StatLine
        className="mt-3"
        stats={[
          { value: repo.challenge_count, label: "bugs" },
          { value: repo.gap_count, label: "test gaps", tone: repo.gap_count ? "text-gap" : undefined },
          { value: difficultySpread(repo, edges), label: "difficulty" },
        ]}
      />
      <p className="mt-4 t-small text-muted group-hover:text-text">
        learn {repoShort(repo.repo)} in {plural(repo.challenge_count, "bug")} <span aria-hidden>&rarr;</span>
      </p>
    </Panel>
  );
}

function RepoTable({ data }: { data: ReposResponse }) {
  const router = useRouter();
  return (
    <div className="overflow-x-auto">
      <table className="w-full min-w-[640px] border-collapse text-left tabular-nums">
        <thead>
          <tr className="border-y border-line">
            {[
              ["repo", ""],
              ["lang", ""],
              ["bugs", "text-right"],
              ["difficulty", ""],
              ["test gaps", "text-right"],
            ].map(([head, align]) => (
              <th key={head} scope="col" className={`t-label px-3 py-2 font-medium text-muted ${align}`}>
                {head}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {data.repos.map((repo) => (
            <tr
              key={repo.repo}
              onClick={() => router.push(courseHref(repo.repo))}
              className="cursor-pointer border-b border-line transition-colors duration-[120ms] hover:bg-surface-2"
            >
              {/* The CTA used to be a sixth, unlabelled column. It belongs to
                  the row, so it sits under the row's name. */}
              <td className="px-3 py-4">
                <Link
                  href={courseHref(repo.repo)}
                  className="font-bold text-text"
                  onClick={(e) => e.stopPropagation()}
                >
                  {repoDisplay(repo.repo)}
                </Link>
                <span className="block t-small text-muted">
                  learn {repoShort(repo.repo)} in {plural(repo.challenge_count, "bug")} <span aria-hidden>&rarr;</span>
                </span>
              </td>
              <td className="px-3 py-4 t-small text-muted">{repo.language.toLowerCase()}</td>
              <td className="px-3 py-4 text-right text-text">{repo.challenge_count}</td>
              <td className="px-3 py-4 t-small whitespace-nowrap text-muted">
                {difficultySpread(repo, data.histogram_edges)}
              </td>
              <td className="px-3 py-4 text-right">
                <Link
                  href={`/gaps/?repo=${encodeURIComponent(repo.repo)}`}
                  onClick={(e) => e.stopPropagation()}
                  className={repo.gap_count ? "link text-gap" : "text-muted"}
                >
                  {repo.gap_count}
                </Link>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/**
 * The gap report's headline, on a page that otherwise has one row and a lot
 * of empty space. It is the most interesting number the pipeline produces and
 * it was sitting unused.
 */
function GapSummary() {
  const { files, total, repo } = GAPS_BY_FILE;
  if (total === 0 || files.length === 0) return null;
  const top = files[0];
  const max = files[0].count;

  return (
    <Panel
      className="mt-10"
      aria-label="test gap summary"
      header={
        <>
          <h2 className="t-label text-muted">where the tests are not looking</h2>
          <ArrowLink href={`/gaps/?repo=${encodeURIComponent(repo)}`} tone="text-muted hover:text-text">
            full report
          </ArrowLink>
        </>
      }
      footer={
        <p className="t-small text-muted">
          counted from the last forge. a gap is a bug the repo&apos;s own suite ran straight past.
        </p>
      }
    >
      <p className="max-w-[72ch] t-body text-text">
        <span className="text-gap">{top.count}</span> of {total} bugs that no test noticed are in one file,{" "}
        <span className="text-text">{top.path}</span>. Every one of them is a line the suite executes but never checks
        the result of.
      </p>
      <ul className="mt-4 space-y-2">
        {files.map((file) => (
          <li key={file.path} className="flex items-center gap-3 t-small">
            <span className="w-[26ch] shrink-0 truncate text-muted" title={file.path}>
              {file.path}
            </span>
            {/* 12px between the end of a bar and its number, and the numbers in
                a fixed column, so the eye reads one ragged edge and not two. */}
            <span className="flex h-[10px] min-w-0 flex-1 items-center pr-3">
              <span
                className="block h-[10px] rounded bg-accent"
                style={{ width: `${(file.count / max) * 100}%` }}
                aria-hidden
              />
            </span>
            <span className="w-[4ch] shrink-0 text-right tabular-nums text-text">{file.count}</span>
          </li>
        ))}
      </ul>
    </Panel>
  );
}

export function Repos() {
  const { data, error, loading, reload } = useApi(getRepos, "repos");

  const repos: RepoSummary[] = data?.repos ?? [];
  const totalBugs = repos.reduce((n, r) => n + r.challenge_count, 0);
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
        <Panel className="max-w-[60ch]">
          <h2 className="t-h2 text-text">nothing forged yet.</h2>
          <p className="mt-2 t-body text-muted">
            A repo becomes a course the moment it is forged: its suite is run once, every line is mapped to the tests
            that cover it, and one token at a time gets flipped. Paste a repo and watch it happen.
          </p>
          <ArrowLink href="/" className="mt-4" tone="text-accent">
            forge a repo
          </ArrowLink>
        </Panel>
      )}

      {data && repos.length > 0 && (
        <>
          <StatLine
            className="mb-3"
            stats={[
              { value: repos.length, label: plural(repos.length, "repo").split(" ")[1] },
              { value: totalBugs, label: "bugs" },
              { value: totalGaps, label: "test gaps", tone: totalGaps ? "text-gap" : undefined },
            ]}
          />

          {repos.length < TABLE_AT ? (
            <ul className="grid grid-cols-1 gap-3 md:grid-cols-2">
              {repos.map((repo) => (
                <li key={repo.repo} className="flex min-w-0">
                  <div className="min-w-0 flex-1">
                    <RepoCard repo={repo} edges={data.histogram_edges} />
                  </div>
                </li>
              ))}
            </ul>
          ) : (
            <RepoTable data={data} />
          )}

          <GapSummary />
        </>
      )}
    </>
  );
}

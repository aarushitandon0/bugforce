"use client";

import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { useState } from "react";
import { getGaps, getRepos, type Gap } from "@/lib/api";
import { plural, repoDisplay, repoGithubUrl } from "@/lib/format";
import { useApi } from "@/lib/useApi";
import { ErrorLine, Loading, PageHeader } from "../Status";

const OPERATOR_NAMES: Record<string, string> = {
  COMPARISON: "comparison",
  ARITHMETIC: "arithmetic",
  BOOLEAN: "boolean",
  BOUNDARY: "boundary",
  NEGATION: "negation",
  RETURN: "return value",
  DEFAULT_ARG: "default argument",
};

function token(text: string): string {
  return text === "" ? "(removed)" : `\`${text.trim()}\``;
}

/** Deterministic, per operator. It says what the suite failed to pin down, never what the fix is. */
export function whyItMatters(gap: Gap): string {
  const o = token(gap.original_token);
  const m = token(gap.mutated_token);
  switch (gap.operator) {
    case "COMPARISON":
      return `No test pins down this boundary: changing ${o} to ${m} passes the whole suite, so an off-by-one here would ship.`;
    case "BOUNDARY":
      return `No test exercises the edge value: ${o} can become ${m} without a single failure.`;
    case "ARITHMETIC":
      return `The result of this calculation is never checked closely enough to notice ${o} becoming ${m}.`;
    case "BOOLEAN":
      return `The suite can't tell ${o} from ${m} here, so at least one side of this condition is untested.`;
    case "NEGATION":
      return "Inverting this condition changes no test outcome: behaviour on one side of the branch is unverified.";
    case "RETURN":
      return `Nothing checks what this returns: replacing it with ${m} passes every test.`;
    case "DEFAULT_ARG":
      return `No test depends on this default: changing ${o} to ${m} goes unnoticed.`;
    default:
      return `Changing ${o} to ${m} passes every test.`;
  }
}

function coverage(gap: Gap): string {
  if (gap.covering_test_count === 0) return "No test executes this line.";
  const n = gap.covering_test_count;
  return `${plural(n, "test")} ${n === 1 ? "executes" : "execute"} this line; none of them failed.`;
}

function lineUrl(gap: Gap): string {
  return `${repoGithubUrl(gap.repo)}/blob/${gap.commit_sha}/${gap.file_path}#L${gap.lineno}`;
}

function toMarkdown(repo: string, gaps: Gap[]): string {
  const sha = gaps[0]?.commit_sha.slice(0, 10) ?? "";
  const lines = [
    `## Test gaps in ${repoDisplay(repo)} @ ${sha}`,
    "",
    `${plural(gaps.length, "single-token change")} to lines the test suite executes that no test noticed.`,
    "Found by mutation testing with BugForge: each line was mutated and the tests covering it were run.",
    "",
  ];
  for (const gap of gaps) {
    lines.push(
      `- [\`${gap.file_path}:${gap.lineno}\`](${lineUrl(gap)}) ${OPERATOR_NAMES[gap.operator] ?? gap.operator}: ` +
        `${token(gap.original_token)} → ${token(gap.mutated_token)}` +
        (gap.enclosing_function ? ` in \`${gap.enclosing_function}()\`` : ""),
      `  ${coverage(gap)} ${whyItMatters(gap)}`,
    );
  }
  return lines.join("\n") + "\n";
}

function RepoReport({ repo, gaps }: { repo: string; gaps: Gap[] }) {
  const [copied, setCopied] = useState(false);
  const byFile = new Map<string, Gap[]>();
  for (const gap of gaps) byFile.set(gap.file_path, [...(byFile.get(gap.file_path) ?? []), gap]);

  return (
    <section className="mt-10" aria-labelledby={`report-${repo}`}>
      <div className="flex flex-wrap items-baseline justify-between gap-4 border-b border-line pb-2">
        <h2 id={`report-${repo}`} className="font-bold text-text">
          {repoDisplay(repo)}
          <span className="font-normal text-dim">
            {" "}
            @ {gaps[0]?.commit_sha.slice(0, 10)} · {plural(gaps.length, "untested mutation")} in {plural(byFile.size, "file")}
          </span>
        </h2>
        <button
          type="button"
          className="link text-[12px]"
          onClick={() =>
            navigator.clipboard.writeText(toMarkdown(repo, gaps)).then(
              () => {
                setCopied(true);
                setTimeout(() => setCopied(false), 1600);
              },
              () => setCopied(false),
            )
          }
        >
          {copied ? "copied" : "copy as markdown"}
        </button>
      </div>

      {[...byFile.entries()].map(([file, fileGaps]) => (
        <div key={file} className="mt-6">
          <h3 className="text-text">
            {file} <span className="text-dim">· {fileGaps.length}</span>
          </h3>
          <ol className="mt-1">
            {fileGaps.map((gap) => (
              <li key={gap.gap_id} className="grid grid-cols-[72px_1fr] gap-x-4 border-t border-line py-3 first:border-t-0">
                <a href={lineUrl(gap)} target="_blank" rel="noreferrer" className="text-dim hover:text-text" title="open this line on GitHub">
                  L{gap.lineno}
                </a>
                <div className="min-w-0">
                  <p className="break-words text-text">
                    {token(gap.original_token)} → {token(gap.mutated_token)}
                    <span className="text-dim">
                      {" "}
                      · {OPERATOR_NAMES[gap.operator] ?? gap.operator}
                      {gap.enclosing_function && <> · in {gap.enclosing_function}()</>}
                    </span>
                  </p>
                  <p className="text-dim">{coverage(gap)}</p>
                  <p className="mt-1 max-w-[80ch] text-text">
                    <span className="text-dim">why it matters: </span>
                    {whyItMatters(gap)}
                  </p>
                </div>
              </li>
            ))}
          </ol>
        </div>
      ))}
    </section>
  );
}

export function Gaps() {
  const repo = useSearchParams().get("repo") ?? undefined;
  const gaps = useApi(() => getGaps(repo), `gaps:${repo ?? "*"}`);
  const repos = useApi(getRepos, "repos");

  const byRepo = new Map<string, Gap[]>();
  for (const gap of gaps.data?.gaps ?? []) byRepo.set(gap.repo, [...(byRepo.get(gap.repo) ?? []), gap]);
  const repoNames = repos.data?.repos.filter((r) => r.gap_count > 0).map((r) => r.repo) ?? [];

  return (
    <>
      <PageHeader eyebrow="maintainer report" title="test gaps">
        Each entry is a one-token change to a line the test suite runs, and no test noticed. These are not bugs in
        the code; they are the places where a real bug would ship. Found by mutating each covered line and running
        the tests that execute it. Nothing here was judged by a model.
      </PageHeader>

      {repoNames.length > 1 && (
        <p className="flex flex-wrap gap-x-5 text-[12px] text-dim">
          <Link href="/gaps/" className={repo ? "hover:text-text" : "text-text"}>
            all repos
          </Link>
          {repoNames.map((name) => (
            <Link key={name} href={`/gaps/?repo=${encodeURIComponent(name)}`} className={repo === name ? "text-text" : "hover:text-text"}>
              {repoDisplay(name)}
            </Link>
          ))}
        </p>
      )}

      {gaps.loading && <Loading text="loading the report" />}
      {gaps.error && <ErrorLine message={gaps.error} onRetry={gaps.reload} />}
      {gaps.data && gaps.data.count === 0 && (
        <p className="text-dim">no test gaps recorded{repo ? ` for ${repoDisplay(repo)}` : ""}.</p>
      )}
      {[...byRepo.entries()].map(([name, list]) => (
        <RepoReport key={name} repo={name} gaps={list} />
      ))}
    </>
  );
}

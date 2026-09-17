"use client";

import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { useEffect, useMemo, useState } from "react";
import { getChallenges, getReveal, getSubmission, type ChallengeCard as Card } from "@/lib/api";
import { clock, repoDisplay, repoShort, thousands } from "@/lib/format";
import { readLocal, readSolved } from "@/lib/progress";
import { dedentSplit, parseUnifiedDiff, splitReveal, type TokenSplit } from "@/lib/reveal";
import { useApi } from "@/lib/useApi";
import { solveHref } from "../ChallengeCard";
import { Replay } from "../result/Replay";
import { Cursor } from "../Cursor";
import { ErrorLine, Loading } from "../Status";
import { solveKey, type SolveRecord } from "./Solve";

const OPERATOR: Record<string, string> = {
  COMPARISON: "comparison operator swapped",
  ARITHMETIC: "arithmetic operator swapped",
  BOOLEAN: "boolean operator swapped",
  BOUNDARY: "boundary constant shifted",
  NEGATION: "negation removed",
  RETURN: "return value replaced",
};

/** One operator fills the width; a whole returned expression has to wrap. */
function heroSize(length: number): string {
  if (length <= 4) return "text-[clamp(64px,12vw,136px)] leading-none";
  if (length <= 10) return "text-[clamp(44px,8vw,96px)] leading-none";
  if (length <= 24) return "text-[clamp(28px,4.6vw,56px)] leading-[1.1]";
  return "text-[clamp(20px,2.8vw,34px)] leading-[1.25]";
}

function Token({ text, tone }: { text: string; tone: string }) {
  if (text.trim() === "") {
    return (
      <span className="inline-flex items-baseline gap-3 text-dim">
        ∅<span className="text-[max(11px,0.16em)] font-normal tracking-normal">nothing</span>
      </span>
    );
  }
  return <span className={`min-w-0 break-all ${tone}`}>{text.trim()}</span>;
}

function Line({ split, tone }: { split: TokenSplit; tone: "original" | "mutated" }) {
  const box = tone === "original" ? "border-success/70 bg-success/10 text-success" : "border-error bg-error/15 text-error";
  return (
    <>
      {split.before}
      <mark className={`border px-[2px] font-bold ${box}`}>{split.token || "∅"}</mark>
      {split.after}
    </>
  );
}

export function Result() {
  const submissionId = useSearchParams().get("submission") ?? "";
  const reveal = useApi(submissionId ? () => getReveal(submissionId) : null, `reveal:${submissionId}`);
  const data = reveal.data;
  const course = useApi(data ? () => getChallenges(data.repo) : null, `course:${data?.repo ?? ""}`);
  // the log was POSTed with the submission; the local record carries what the
  // API has no way to know (the traceback the learner was given, and when they started)
  const submission = useApi(submissionId ? () => getSubmission(submissionId) : null, `submission:${submissionId}`);

  const [record, setRecord] = useState<SolveRecord | null>(null);
  const [next, setNext] = useState<Card | null>(null);

  useEffect(() => {
    if (data) setRecord(readLocal<SolveRecord>(solveKey(data.challenge_id)));
  }, [data]);

  useEffect(() => {
    if (!data || !course.data) return;
    const solved = readSolved();
    const list = course.data.challenges;
    const after = list.slice(list.findIndex((c) => c.challenge_id === data.challenge_id) + 1);
    setNext([...after, ...list].find((c) => c.challenge_id !== data.challenge_id && !solved.has(c.challenge_id)) ?? null);
  }, [data, course.data]);

  const split = useMemo(() => (data ? dedentSplit(splitReveal(data)) : null), [data]);
  const diff = useMemo(() => (data ? parseUnifiedDiff(data.diff) : []), [data]);

  if (!submissionId) return <ErrorLine message="no submission named in the URL" />;
  if (reveal.loading) return <Loading text="checking the verdict" />;
  if (reveal.error || !data || !split) {
    return <ErrorLine message={reveal.error ?? "nothing to reveal"} onRetry={reveal.reload} />;
  }

  const solvedCount = course.data ? course.data.challenges.filter((c) => readSolved().has(c.challenge_id)).length : null;
  const total = course.data?.challenges.length ?? null;
  const shortSha = data.commit_sha.slice(0, 7);

  return (
    <article className="pt-14">
      {/* verdict */}
      <p className="text-[12px] text-dim">$ pytest · {data.submission_id}</p>
      <h1 className="mt-2 text-[clamp(26px,4.4vw,44px)] font-bold leading-[1.1] tracking-[-0.02em] text-success animate-fade">
        PASS — {thousands(data.tests_passed)} tests green
        <Cursor className="ml-3 !bg-success" />
      </h1>
      <p className="mt-3 text-dim">
        <span className="text-text">{data.title}</span>
        {record && (
          <>
            {" "}
            · solved in <span className="tabular-nums text-text">{clock(record.elapsedMs)}</span>
          </>
        )}
      </p>

      {/* the payoff: one token */}
      <section className="mt-16" aria-label="the mutation">
        <h2 className="label">it all hinged on one token</h2>
        <div
          className={`mt-6 flex flex-wrap items-baseline gap-x-[0.5em] gap-y-3 font-bold tracking-[-0.03em] ${heroSize(
            Math.max(split.original.token.length, split.mutated.token.length),
          )}`}
        >
          <Token text={split.original.token} tone="text-success" />
          <span className="text-[0.45em] font-normal text-dim" aria-label="became">
            →
          </span>
          <Token text={split.mutated.token} tone="text-error" />
        </div>
        <p className="mt-5 max-w-[80ch] text-dim">
          {split.mutated.token === "" ? (
            <>
              BugForge deleted <code className="text-success">{split.original.token.trim()}</code>
            </>
          ) : split.original.token === "" ? (
            <>
              BugForge inserted <code className="text-error">{split.mutated.token.trim()}</code>
            </>
          ) : (
            <>
              BugForge changed <code className="text-success">{split.original.token}</code> to{" "}
              <code className="text-error">{split.mutated.token}</code>
            </>
          )}
          {OPERATOR[data.operator] ? ` (${OPERATOR[data.operator]})` : ""}. Nothing else in the repo was touched.
        </p>

        <div className="mt-8 overflow-x-auto border border-line bg-panel">
          <div className="flex items-center justify-between gap-4 border-b border-line px-4 py-2 text-[11px] text-dim">
            <span className="truncate">
              {data.file_path}:{data.lineno}
            </span>
            <span className="shrink-0">line {data.lineno}</span>
          </div>
          <div className="px-4 py-4 text-[clamp(13px,1.6vw,17px)] leading-[1.9]">
            <div className="whitespace-pre">
              <span className="mr-4 select-none text-success">-</span>
              <Line split={split.original} tone="original" />
              <span className="ml-6 select-none text-[11px] text-dim">original</span>
            </div>
            <div className="whitespace-pre">
              <span className="mr-4 select-none text-error">+</span>
              <Line split={split.mutated} tone="mutated" />
              <span className="ml-6 select-none text-[11px] text-dim">what you were given</span>
            </div>
          </div>
        </div>

        <details className="group mt-3 border border-line">
          <summary className="flex cursor-pointer list-none items-center justify-between px-4 py-2 text-[12px] text-dim hover:text-text">
            <span>the full mutation diff, as applied</span>
            <span aria-hidden className="group-open:hidden">▸</span>
            <span aria-hidden className="hidden group-open:inline">▾</span>
          </summary>
          <div className="overflow-x-auto border-t border-line text-[12px] leading-[1.7]">
            <table className="w-full border-collapse">
              <tbody>
                {diff.map((row, i) => {
                  const tone =
                    row.kind === "removed"
                      ? "bg-success/[0.06] text-text"
                      : row.kind === "added"
                        ? "bg-error/[0.08] text-text"
                        : row.kind === "context"
                          ? "text-dim"
                          : "text-dim/70";
                  const sign = row.kind === "removed" ? "-" : row.kind === "added" ? "+" : " ";
                  const isSite = row.kind === "removed" || row.kind === "added";
                  return (
                    <tr key={i} className={tone}>
                      <td className="w-[1%] select-none px-2 text-right tabular-nums text-dim/70">{row.oldLine ?? ""}</td>
                      <td className="w-[1%] select-none px-2 text-right tabular-nums text-dim/70">{row.newLine ?? ""}</td>
                      <td className={`w-[1%] select-none px-1 ${row.kind === "removed" ? "text-success" : row.kind === "added" ? "text-error" : ""}`}>
                        {row.kind === "file" || row.kind === "hunk" ? "" : sign}
                      </td>
                      <td className="whitespace-pre pr-4">
                        {isSite && row.text === (row.kind === "removed" ? data.original_line : data.mutated_line) ? (
                          <Line split={splitReveal(data)[row.kind === "removed" ? "original" : "mutated"]} tone={row.kind === "removed" ? "original" : "mutated"} />
                        ) : (
                          row.text
                        )}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </details>

        {record?.patch && (
          <details className="group mt-3 border border-line">
            <summary className="flex cursor-pointer list-none items-center justify-between px-4 py-2 text-[12px] text-dim hover:text-text">
              <span>your fix</span>
              <span aria-hidden className="group-open:hidden">▸</span>
              <span aria-hidden className="hidden group-open:inline">▾</span>
            </summary>
            <div className="overflow-x-auto whitespace-pre border-t border-line px-4 py-3 text-[12px] leading-[1.7]">
              {record.patch.replace(/\s+$/, "").split(/\r?\n/).map((line, i) => (
                <div
                  key={i}
                  className={
                    line.startsWith("+++") || line.startsWith("---") || line.startsWith("@@")
                      ? "text-dim"
                      : line.startsWith("+")
                        ? "text-success"
                        : line.startsWith("-")
                          ? "text-error"
                          : "text-dim"
                  }
                >
                  {line || " "}
                </div>
              ))}
            </div>
          </details>
        )}
      </section>

      {record && record.frames.length > 0 && (
        <Replay
          visits={submission.data?.investigation ?? record.visits}
          frames={record.frames}
          mutatedPath={data.file_path}
          displacement={record.displacement}
          startedAt={record.startedAt}
          endedAt={record.endedAt}
        />
      )}

      {/* the reveal */}
      <section className="mt-16 border-t border-line pt-8" aria-label="where it came from">
        <h2 className="label">the repo</h2>
        <dl className="mt-4 grid max-w-[760px] grid-cols-[max-content_1fr] gap-x-8 gap-y-2">
          <dt className="text-dim">repo</dt>
          <dd className="text-text">{repoDisplay(data.repo)}</dd>
          <dt className="text-dim">commit</dt>
          <dd className="tabular-nums text-text">{shortSha}</dd>
          <dt className="text-dim">file</dt>
          <dd className="break-all text-text">
            {data.file_path}
            <span className="text-dim">:{data.lineno}</span>
          </dd>
          {data.license && (
            <>
              <dt className="text-dim">licence</dt>
              <dd className="text-text">{data.license}</dd>
            </>
          )}
          <dt className="text-dim">caught by</dt>
          <dd className="text-text">the repo&apos;s own suite. BugForge wrote no tests.</dd>
        </dl>

        <div className="mt-8 flex flex-wrap gap-3">
          <a
            href={data.github_url}
            target="_blank"
            rel="noreferrer"
            className="border border-text px-4 py-2.5 font-bold text-text transition-colors duration-[120ms] hover:bg-text hover:text-base"
          >
            view {data.file_path.split("/").pop()}:{data.lineno} on GitHub ↗
          </a>
          {next && (
            <Link
              href={solveHref(next.challenge_id)}
              className="border border-line px-4 py-2.5 text-text transition-colors duration-[120ms] hover:border-dim"
            >
              next bug: {next.title} →
            </Link>
          )}
          <Link
            href={`/repo/?name=${encodeURIComponent(data.repo)}`}
            className="border border-line px-4 py-2.5 text-dim transition-colors duration-[120ms] hover:border-dim hover:text-text"
          >
            {solvedCount !== null && total !== null
              ? `${solvedCount} of ${total} bugs in ${repoShort(data.repo)} solved`
              : `back to ${repoShort(data.repo)}`}
          </Link>
        </div>
      </section>
    </article>
  );
}

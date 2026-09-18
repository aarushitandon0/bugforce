"use client";

import { useRouter, useSearchParams } from "next/navigation";
import { useEffect, useRef, useState } from "react";
import { ApiError, getRepos, startForge, type Forgeable } from "@/lib/api";
import { HEADLINE, VETTED_REPOS } from "@/lib/forge-data";
import { normalizeRepoUrl, parseRepoInput, repoDisplay } from "@/lib/format";
import { Cursor } from "../Cursor";
import { ForgeStream, type LocalLine } from "../ForgeStream";

/*
 * Repos worth showing that no image exists for yet. They render dim and
 * disabled: the chips used to offer these as if they were forgeable, and
 * clicking one gave an error.
 */
const NOT_VETTED = ["psf/requests", "arrow-py/arrow"];

/* The whole pipeline in three lines. It fills the column under the input, and
 * it is the part a first-time reader actually needs: nothing here is a model
 * inventing a bug. */
const STEPS = [
  ["baseline", "run the suite once, mapping every line to the tests that cover it"],
  ["mutate", "flip one token by AST — keep it only if the suite catches it"],
  ["grade", "your patch runs against the repo's own suite. nothing else decides"],
];

function refusal(repo: string, forgeable: Forgeable[]): LocalLine[] {
  const available = forgeable.map((f) => repoDisplay(f.repo)).join(", ") || "none";
  return [
    { tone: "error", text: `✗ ${repo} is not vetted yet` },
    { tone: "dim", text: "  repos are forged from images built ahead of time, with dependencies" },
    { tone: "dim", text: "  installed on a trusted machine. nothing is cloned or installed at runtime." },
    { tone: "dim", text: `  forgeable now: ${available}` },
  ];
}

export function Landing() {
  const router = useRouter();
  const params = useSearchParams();
  const executionId = params.get("forge");

  const [input, setInput] = useState("");
  const [forgeable, setForgeable] = useState<Forgeable[] | null>(null);
  const [lines, setLines] = useState<LocalLine[]>([]);
  const [repoLabel, setRepoLabel] = useState<string | null>(null);
  const [starting, setStarting] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    let cancelled = false;
    getRepos()
      .then((r) => !cancelled && setForgeable(r.forgeable))
      .catch(() => !cancelled && setForgeable([]));
    return () => {
      cancelled = true;
    };
  }, []);

  async function forge(raw: string) {
    if (starting) return;
    const repo = parseRepoInput(raw);
    const command: LocalLine = { tone: "command", text: `$ forge github.com/${repo ?? raw.trim()}` };

    if (!repo) {
      setLines([command, { tone: "error", text: "✗ not a GitHub repo. expected owner/name, e.g. jd/tenacity" }]);
      if (executionId) router.replace("/", { scroll: false });
      return;
    }
    // Known list first, so a refusal is instant; the API refuses too if this list is stale.
    if (forgeable && forgeable.length > 0 && !forgeable.some((f) => normalizeRepoUrl(f.url) === repo.toLowerCase())) {
      setLines([command, ...refusal(repo, forgeable)]);
      if (executionId) router.replace("/", { scroll: false });
      return;
    }

    setStarting(true);
    setLines([command]);
    try {
      const started = await startForge(`https://github.com/${repo}`);
      setRepoLabel(repo);
      setLines([command, { tone: "dim", text: `  started ${started.execution_id}` }]);
      router.replace(`/?forge=${encodeURIComponent(started.execution_id)}`, { scroll: false });
    } catch (error) {
      if (error instanceof ApiError && error.status === 422) {
        const list = (error.body.forgeable as Forgeable[] | undefined) ?? forgeable ?? [];
        setLines([command, ...refusal(repo, list)]);
      } else {
        setLines([command, { tone: "error", text: `✗ ${error instanceof Error ? error.message : String(error)}` }]);
      }
    } finally {
      setStarting(false);
    }
  }

  // The vetted list is baked in at build time from infra/docker/vetted_repos.json,
  // so a chip can never offer a repo that has no image.
  const examples = VETTED_REPOS.map((r) => r.display);

  return (
    /*
     * Two columns on a wide screen: the pitch and the input on the left, the
     * stream on the right at full column height. It used to be one narrow
     * column with the whole right half empty and the stream -- the only proof
     * any of this is real -- pushed below the fold.
     */
    <div className="grid grid-cols-1 gap-8 pt-10 pb-6 lg:grid-cols-[minmax(0,0.92fr)_minmax(0,1.08fr)] lg:gap-12 lg:pt-14">
      <section className="flex min-w-0 flex-col">
        <h1 className="max-w-[18ch] text-[clamp(28px,3.4vw,40px)] font-bold leading-[1.14] tracking-[-0.02em] text-text">
          Every repo is a debugging gym.
          <Cursor className="ml-3" />
        </h1>
        <p className="mt-5 max-w-[58ch] text-[14.5px] leading-[1.65] text-dim">
          Paste any public repo with a test suite. BugForge breaks it the way it would break in production, hands you
          the stack trace, and checks your fix. Nothing here was written by hand.
        </p>

        <form
          className="mt-7 flex flex-col border border-line transition-colors duration-[120ms] focus-within:border-dim sm:flex-row"
          onSubmit={(e) => {
            e.preventDefault();
            forge(input);
          }}
        >
          <label className="flex min-w-0 flex-1 cursor-text items-center pl-4" htmlFor="repo-input">
            <span className="shrink-0 select-none text-[15px] text-dim">github.com/</span>
            <input
              id="repo-input"
              ref={inputRef}
              value={input}
              onChange={(e) => setInput(e.target.value)}
              placeholder="owner/repo"
              autoComplete="off"
              autoCapitalize="off"
              spellCheck={false}
              aria-label="GitHub repository, as owner/repo"
              className="min-w-0 flex-1 bg-transparent py-3.5 pr-4 text-[15px] text-text caret-text outline-none placeholder:text-dim/60 [caret-shape:block]"
            />
          </label>
          <button
            type="submit"
            disabled={starting}
            className="border-t border-line px-6 py-3.5 text-[13.5px] font-bold text-text transition-colors duration-[120ms] hover:bg-text hover:text-base disabled:cursor-wait disabled:text-dim disabled:hover:bg-transparent sm:border-t-0 sm:border-l"
          >
            {starting ? "forging…" : "forge bugs"}
          </button>
        </form>

        <div className="mt-3 flex min-h-7 flex-wrap items-center gap-2 text-[12px]">
          {examples.length > 0 && <span className="text-dim">try</span>}
          {examples.map((repo) => (
            <button
              key={repo}
              type="button"
              onClick={() => {
                setInput(repo);
                forge(repo);
              }}
              className="border border-line px-2 py-0.5 text-text transition-colors duration-[120ms] hover:border-dim"
            >
              {repo}
            </button>
          ))}
          {NOT_VETTED.map((repo) => (
            <span
              key={repo}
              title="not vetted yet &mdash; images are built ahead of time, on a trusted machine"
              className="cursor-not-allowed border border-line px-2 py-0.5 text-dim opacity-50"
              aria-disabled="true"
            >
              {repo}
            </span>
          ))}
        </div>

        <ol className="mt-10 space-y-2.5 border-t border-line pt-6 text-[12.5px] leading-[1.55]">
          {STEPS.map(([name, what], i) => (
            <li key={name} className="flex gap-3">
              <span className="w-[2ch] shrink-0 tabular-nums text-dim">{String(i + 1).padStart(2, "0")}</span>
              <span className="w-[9ch] shrink-0 text-text">{name}</span>
              <span className="min-w-0 text-dim">{what}</span>
            </li>
          ))}
        </ol>

        {/* the proof that there is a filter, and not just a model making bugs up */}
        <dl className="mt-auto flex flex-wrap items-baseline gap-x-2 gap-y-1 pt-10 text-[12px] text-dim tabular-nums">
          {[
            { n: HEADLINE.candidates, label: "candidates" },
            { n: HEADLINE.covered, label: "on covered lines" },
            { n: HEADLINE.admitted, label: "admitted" },
            { n: HEADLINE.gaps, label: "test gaps" },
          ].map((stat, i) => (
            <span key={stat.label} className="flex items-baseline gap-2">
              {i > 0 && <span aria-hidden className="text-dim opacity-40">&middot;</span>}
              <dt className="sr-only">{stat.label}</dt>
              <dd className="flex items-baseline gap-1.5">
                <span className="text-text">{stat.n}</span>
                <span>{stat.label}</span>
              </dd>
            </span>
          ))}
        </dl>
      </section>

      <section
        aria-label="generation stream"
        className="h-[420px] min-h-0 min-w-0 lg:h-[calc(100dvh-8.5rem)] lg:max-h-[720px] lg:min-h-[440px]"
      >
        <ForgeStream executionId={executionId} repoLabel={repoLabel} localLines={lines} legend />
      </section>
    </div>
  );
}

"use client";

import { useRouter, useSearchParams } from "next/navigation";
import { useEffect, useRef, useState } from "react";
import { ApiError, getRepos, startForge, type Forgeable } from "@/lib/api";
import { normalizeRepoUrl, parseRepoInput, repoDisplay } from "@/lib/format";
import { Cursor } from "../Cursor";
import { ForgeStream, type LocalLine } from "../ForgeStream";

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

  const examples = (forgeable ?? []).slice(0, 3).map((f) => repoDisplay(f.repo));

  return (
    <>
      <section className="pt-20">
        <h1 className="max-w-[20ch] text-[clamp(30px,5vw,46px)] font-bold leading-[1.12] tracking-[-0.02em] text-text">
          Every repo is a debugging gym.
          <Cursor className="ml-3" />
        </h1>
        <p className="mt-6 max-w-[68ch] text-[15px] leading-[1.7] text-dim">
          Paste any public repo with a test suite. BugForge breaks it the way it would break in production, hands
          you the stack trace, and checks your fix. Nothing here was written by hand.
        </p>

        <form
          className="mt-10 flex flex-col border border-line transition-colors duration-[120ms] focus-within:border-dim sm:flex-row"
          onSubmit={(e) => {
            e.preventDefault();
            forge(input);
          }}
        >
          <label className="flex min-w-0 flex-1 cursor-text items-center pl-4" htmlFor="repo-input">
            <span className="shrink-0 select-none text-[16px] text-dim">github.com/</span>
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
              className="min-w-0 flex-1 bg-transparent py-4 pr-4 text-[16px] text-text caret-text outline-none placeholder:text-dim/60 [caret-shape:block]"
            />
          </label>
          <button
            type="submit"
            disabled={starting}
            className="border-t border-line px-7 py-4 text-[14px] font-bold text-text transition-colors duration-[120ms] hover:bg-text hover:text-base disabled:cursor-wait disabled:text-dim disabled:hover:bg-transparent sm:border-t-0 sm:border-l"
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
        </div>
      </section>

      <section className="mt-14" aria-label="generation stream">
        <ForgeStream executionId={executionId} repoLabel={repoLabel} localLines={lines} />
        <dl className="mt-3 grid gap-x-8 gap-y-1 text-[11px] text-dim sm:grid-cols-3">
          <div>
            <dt className="inline text-success">✓ KEEP</dt> <dd className="inline">caught by the suite, and worth walking the trace for</dd>
          </div>
          <div>
            <dt className="inline">✗ drop</dt> <dd className="inline">too loud, too easy, timed out, or broke the import</dd>
          </div>
          <div>
            <dt className="inline text-error">✗ test gap</dt> <dd className="inline">no test noticed. goes to the maintainers</dd>
          </div>
        </dl>
      </section>
    </>
  );
}

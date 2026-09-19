"use client";

import { useRouter, useSearchParams } from "next/navigation";
import { useEffect, useRef, useState } from "react";
import { ApiError, getRepos, startForge, type Forgeable } from "@/lib/api";
import { HEADLINE, VETTED_REPOS } from "@/lib/forge-data";
import { normalizeRepoUrl, parseRepoInput, repoDisplay } from "@/lib/format";
import { rulesFor } from "@/lib/lang";
import { Cursor } from "../Cursor";
import { ForgeStream, type LocalLine } from "../ForgeStream";
import { buttonClass } from "../ui/Button";
import { StatLine } from "../ui/StatLine";

/*
 * Repos worth showing that no image exists for yet. They used to render at
 * 50% opacity, which made the whole row -- including the two that DO work --
 * read as disabled and blocked the fastest path to first value. They now carry
 * the same resting style as every other chip and say what is true instead.
 */
const NOT_VETTED = ["psf/requests", "arrow-py/arrow"];

/*
 * What is forgeable is a property of the DEPLOYED STACK, not of the build.
 * One stack carries one image and an image carries one repo, so the vetted
 * list can name repos this stack has no image for. Offering those as live
 * chips walks the reader straight into a 422; the build-time list is only
 * good for display names and languages, and GET /repos is the truth.
 */
const VETTED_BY_URL = new Map(VETTED_REPOS.map((r) => [normalizeRepoUrl(r.url), r]));

interface Chip {
  display: string;
  language: string;
}

function chipsFor(forgeable: Forgeable[] | null): { ready: Chip[]; pending: string[] } {
  // Until the first response lands, show the vetted list rather than an empty
  // row: it is the best guess available and it stops the row from popping in.
  if (forgeable === null) {
    return { ready: VETTED_REPOS.map((r) => ({ display: r.display, language: r.language })), pending: NOT_VETTED };
  }
  const readyUrls = new Set(forgeable.map((f) => normalizeRepoUrl(f.url)));
  return {
    ready: forgeable.map((f) => {
      const vetted = VETTED_BY_URL.get(normalizeRepoUrl(f.url));
      return { display: vetted?.display ?? repoDisplay(f.repo), language: vetted?.language ?? "python" };
    }),
    // A vetted repo with no image on this stack is in exactly the position an
    // unvetted one is in, and says the same thing. NOT_VETTED is a static
    // list, so filter it too: a stack that HAS an image for one of them would
    // otherwise render it twice, live and "not forged yet" at once.
    pending: [...VETTED_REPOS.map((r) => r.display), ...NOT_VETTED].filter(
      (display) => !readyUrls.has(normalizeRepoUrl(display)),
    ),
  };
}

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
    { tone: "error", text: `✗ ${repo} has not been forged yet` },
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

  const { ready: examples, pending } = chipsFor(forgeable);
  const chip = buttonClass("secondary", "sm");

  return (
    /*
     * Two columns on a wide screen: the pitch and the input on the left, the
     * stream on the right. The grid stretches both, so the stream's bottom
     * edge and the stats line at the foot of the left column resolve to the
     * same baseline instead of ending 80px apart.
     */
    <div className="grid grid-cols-1 gap-8 pt-10 pb-6 lg:grid-cols-[minmax(0,0.92fr)_minmax(0,1.08fr)] lg:gap-12 lg:pt-12">
      <section className="flex min-w-0 flex-col">
        <h1 className="t-display max-w-[18ch] text-text">
          Every repo is a debugging gym.
          <Cursor className="ml-3" />
        </h1>
        <p className="t-body mt-5 max-w-[58ch] text-muted">
          Paste any public repo with a test suite. BugForge breaks it the way it would break in production, hands you
          the stack trace, and checks your fix. Nothing here was written by hand.
        </p>

        {/*
         * The input and the button are one 52px box with a shared border. The
         * button is the only filled accent on the page -- the page previously
         * had no filled control anywhere, which is what made it read as a demo
         * rather than a product.
         */}
        <form
          className="mt-6 flex flex-col rounded border border-line transition-colors duration-[120ms] focus-within:border-line-strong sm:h-13 sm:flex-row"
          onSubmit={(e) => {
            e.preventDefault();
            forge(input);
          }}
        >
          <label className="flex min-w-0 flex-1 cursor-text items-center pl-5" htmlFor="repo-input">
            <span className="shrink-0 select-none text-[15px] text-muted">github.com/</span>
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
              className="min-w-0 flex-1 bg-transparent py-4 pr-5 text-[15px] text-text caret-text outline-none placeholder:text-faint [caret-shape:block]"
            />
          </label>
          <button
            type="submit"
            disabled={starting}
            className={buttonClass(
              "primary",
              "lg",
              "shrink-0 rounded-none border-t border-line disabled:cursor-wait sm:h-auto sm:self-stretch sm:border-t-0 sm:border-l",
            )}
          >
            {starting ? "forging…" : "forge bugs"}
          </button>
        </form>

        {/*
         * Every chip rests identically. What differs is hover, and the one
         * whose name is currently in the box -- which is the only distinction
         * that tells the reader anything.
         */}
        <div className="mt-3 flex min-h-8 flex-wrap items-center gap-2">
          {examples.length > 0 && <span className="t-small text-muted">try</span>}
          {examples.map(({ display, language }) => (
            <button
              key={display}
              type="button"
              aria-pressed={input.trim().toLowerCase() === display.toLowerCase()}
              onClick={() => {
                setInput(display);
                forge(display);
              }}
              className={buttonClass(
                "secondary",
                "sm",
                input.trim().toLowerCase() === display.toLowerCase() ? "border-line-strong bg-surface-2" : "",
              )}
            >
              {display}
              {/* The language, not decoration: picking an example is really
                  picking a language, and the two on offer behave differently
                  enough that a learner should know which one they clicked. */}
              <span className="text-muted">{rulesFor(language).label}</span>
            </button>
          ))}
          {pending.map((repo) => (
            <span key={repo} className={`${chip} text-muted`}>
              {repo}
              <span className="text-faint">&middot; not forged yet</span>
            </span>
          ))}
        </div>

        <ol className="mt-10 space-y-3 border-t border-line pt-10">
          {STEPS.map(([name, what], i) => (
            <li key={name} className="t-small flex gap-3">
              <span className="w-[2ch] shrink-0 tabular-nums text-faint">{String(i + 1).padStart(2, "0")}</span>
              <span className="w-[9ch] shrink-0 text-text">{name}</span>
              {/* fixed column, so the three descriptions wrap the same way at
                  every width instead of each finding its own break */}
              <span className="min-w-0 max-w-[52ch] text-muted">{what}</span>
            </li>
          ))}
        </ol>

        {/* the proof that there is a filter, and not just a model making bugs up */}
        <StatLine
          className="mt-auto pt-10"
          stats={[
            { value: HEADLINE.candidates, label: "candidates" },
            { value: HEADLINE.covered, label: "on covered lines" },
            { value: HEADLINE.admitted, label: "bugs" },
            { value: HEADLINE.gaps, label: "test gaps", tone: "text-gap" },
          ]}
        />
      </section>

      <section aria-label="generation stream" className="min-h-[420px] min-w-0 lg:min-h-[560px]">
        <ForgeStream executionId={executionId} repoLabel={repoLabel} localLines={lines} legend />
      </section>
    </div>
  );
}

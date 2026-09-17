/**
 * Typed client for the BugForge HTTP API (cloud/handlers/fn_api.py).
 *
 * GETs send no custom headers so they stay CORS "simple requests"; only the
 * two POSTs carry content-type and trigger a preflight.
 */

const BASE = (process.env.NEXT_PUBLIC_API_URL ?? "").replace(/\/+$/, "");

export const apiConfigured = BASE !== "";

export class ApiError extends Error {
  constructor(
    readonly status: number,
    message: string,
    readonly body: Record<string, unknown> = {},
  ) {
    super(message);
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  if (!apiConfigured) throw new ApiError(0, "NEXT_PUBLIC_API_URL was not set when this site was built");
  let response: Response;
  try {
    response = await fetch(`${BASE}${path}`, { cache: "no-store", ...init });
  } catch {
    throw new ApiError(0, "network error: the API did not respond");
  }
  const text = await response.text();
  let body: Record<string, unknown> = {};
  try {
    body = text ? JSON.parse(text) : {};
  } catch {
    body = { error: text.slice(0, 200) };
  }
  if (!response.ok) {
    const message = String(body.message ?? body.error ?? `HTTP ${response.status}`);
    throw new ApiError(response.status, message, body);
  }
  return body as T;
}

function post<T>(path: string, payload: unknown): Promise<T> {
  return request<T>(path, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(payload),
  });
}

// ---------------------------------------------------------------------------
// forge
// ---------------------------------------------------------------------------

export interface Forgeable {
  repo: string;
  url: string;
}

export interface ForgeStarted {
  execution_id: string;
  repo_url: string;
}

export type StreamVerdict = "keep" | "drop" | "gap" | "scoring";

export interface StreamRow {
  id: string;
  operator: string;
  verdict: StreamVerdict;
  location: string;
  tests_red: number | null;
  detail: string;
}

export type ForgePhase = "baseline" | "generate" | "run" | "score" | "package" | "done" | "failed";

export interface ForgeStatus {
  execution_id: string;
  repo_url: string | null;
  status: "RUNNING" | "SUCCEEDED" | "FAILED" | "TIMED_OUT" | "ABORTED" | "PENDING_REDRIVE";
  phase: ForgePhase;
  started_at: string;
  stopped_at: string | null;
  error: string | null;
  cause: string | null;
  baseline: { total_tests: number; covered_lines: number } | null;
  candidates: number;
  batches: number;
  batches_done: number;
  rows: StreamRow[];
  counts: Record<StreamVerdict, number>;
  summary: { challenges_ready: number; test_gaps: number; repo: string | null } | null;
}

export const startForge = (repoUrl: string) => post<ForgeStarted>("/forge", { repo_url: repoUrl });
export const getForge = (executionId: string) =>
  request<ForgeStatus>(`/forge/${encodeURIComponent(executionId)}`);

// ---------------------------------------------------------------------------
// browse
// ---------------------------------------------------------------------------

export interface RepoSummary {
  repo: string;
  repo_url: string;
  license: string;
  language: string;
  challenge_count: number;
  avg_difficulty: number;
  histogram: number[];
  gap_count: number;
}

export interface ReposResponse {
  repos: RepoSummary[];
  histogram_edges: number[];
  forgeable: Forgeable[];
}

export interface Breakdown {
  displacement: number;
  search_space: number;
  noise: number;
  d: number;
  s: number;
  n: number;
}

export type DifficultyLabel = "easy" | "medium" | "hard";

export interface ChallengeCard {
  challenge_id: string;
  repo: string;
  repo_url: string;
  license: string;
  language: string;
  title: string;
  description: string;
  difficulty_score: number;
  difficulty_label: DifficultyLabel;
  breakdown: Breakdown | null;
  failing_test_count: number;
  total_tests: number;
}

export interface ChallengeDetail extends ChallengeCard {
  failing_tests: string[];
}

export interface TreeUrls {
  url: string;
  traceback_url: string;
  expires_in: number;
}

export const getRepos = () => request<ReposResponse>("/repos");
export const getChallenges = (repo: string) =>
  request<{ challenges: ChallengeCard[]; count: number }>(`/challenges?repo=${encodeURIComponent(repo)}`);
export const getChallenge = (id: string) => request<ChallengeDetail>(`/challenges/${encodeURIComponent(id)}`);
export const getTreeUrls = (id: string) => request<TreeUrls>(`/challenges/${encodeURIComponent(id)}/tree`);

// ---------------------------------------------------------------------------
// gaps
// ---------------------------------------------------------------------------

export interface Gap {
  gap_id: string;
  repo: string;
  commit_sha: string;
  file_path: string;
  lineno: number;
  operator: string;
  original_token: string;
  mutated_token: string;
  enclosing_function: string | null;
  covering_test_count: number;
  reason: string;
}

export const getGaps = (repo?: string) =>
  request<{ gaps: Gap[]; count: number }>(repo ? `/gaps?repo=${encodeURIComponent(repo)}` : "/gaps");

// ---------------------------------------------------------------------------
// submissions
// ---------------------------------------------------------------------------

export type Verdict = "PASS" | "FAIL" | "REJECTED";

export interface Submission {
  submission_id: string;
  challenge_id: string;
  status: "PENDING" | "COMPLETE";
  verdict?: Verdict;
  reason?: string;
  detail?: string;
  failing_tests?: string[];
  tests_passed?: number;
}

export interface Reveal {
  submission_id: string;
  challenge_id: string;
  tests_passed: number;
  title: string;
  repo: string;
  repo_url: string;
  license: string;
  commit_sha: string;
  file_path: string;
  lineno: number;
  col_start: number;
  col_end: number;
  operator: string;
  original_token: string;
  mutated_token: string;
  original_line: string;
  mutated_line: string;
  diff: string;
  github_url: string;
}

export const submitPatch = (challengeId: string, patch: string) =>
  post<{ submission_id: string; status: "PENDING" }>("/submissions", { challenge_id: challengeId, patch });
export const getSubmission = (id: string) => request<Submission>(`/submissions/${encodeURIComponent(id)}`);
export const getReveal = (id: string) => request<Reveal>(`/submissions/${encodeURIComponent(id)}/reveal`);

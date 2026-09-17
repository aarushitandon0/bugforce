# BugForge Phase 4 — AWS

Everything deterministic from Phases 1–3 (`bugforge/`) runs unchanged; this
directory only moves it onto Lambda, Step Functions, DynamoDB and S3.

## Layout

```
infra/template.yaml                  SAM: 8 functions, 4 tables, 1 bucket, 1 workflow, 1 HTTP API
infra/statemachine/forge_repo.asl.json   baseline -> generate -> Map(15) -> score -> describe -> persist
infra/docker/Dockerfile              one image per vetted repo, deps baked at build time
infra/docker/vetted_repos.json       the allowlist; nothing else is ever cloned or installed
infra/docker/build_and_push.sh       build + push to ECR
cloud/                               the AWS glue (handlers, anti-cheat, describer)
```

## Deploy

```bash
# 1. build and push the image (one per repo; ~5 min, most of it pip + the
#    in-image test run that proves the suite is green before you deploy)
./infra/docker/build_and_push.sh jd__tenacity us-east-1

# 2. deploy the stack, passing the image URI the script printed
sam deploy --template infra/template.yaml --stack-name bugforge \
  --capabilities CAPABILITY_IAM --region us-east-1 \
  --resolve-s3 \
  --parameter-overrides \
    ImageUri=<ACCOUNT>.dkr.ecr.us-east-1.amazonaws.com/bugforge:jd__tenacity-3e58094d3b \
    RepoName=jd__tenacity RepoUrl=https://github.com/jd/tenacity RepoPackage=tenacity
```

`sam deploy` prints `ApiUrl`. Everything below assumes `API=$ApiUrl`.

## Forge a repo end to end

```bash
API=https://xxxx.execute-api.us-east-1.amazonaws.com

# start it
curl -sX POST $API/forge -H 'content-type: application/json' \
  -d '{"repo_url":"https://github.com/jd/tenacity"}'
# -> {"execution_id":"forge-ab12cd34ef56", ...}

# watch it, including the live reject stream
curl -s $API/forge/forge-ab12cd34ef56 | jq

# browse the results
curl -s "$API/repos" | jq
curl -s "$API/challenges?repo=jd__tenacity" | jq
curl -s "$API/challenges/<id>" | jq        # no patch, no line, no repo name
curl -s "$API/challenges/<id>/tree" | jq   # presigned, public prefix, 10 min
curl -s "$API/gaps?repo=jd__tenacity" | jq # maintainer test-gap report
```

## Submit a fix

```bash
curl -sX POST $API/submissions -H 'content-type: application/json' \
  -d "$(jq -n --arg p "$(cat fix.patch)" '{challenge_id:"<id>", patch:$p}')"
# -> {"submission_id":"sub-...","status":"PENDING"}

curl -s $API/submissions/sub-... | jq
# -> {"status":"COMPLETE","verdict":"PASS","tests_passed":183}
```

Verdicts are `PASS`, `FAIL` (with `failing_tests`), or `REJECTED`
(`reason: anti_cheat` or `patch_did_not_apply`).

## Things worth knowing before you touch this

**`ToleratedFailurePercentage: 90` is intentional.** `fn_run_batch` raises
when none of its 15 mutations survived the targeted run, and that is the
normal outcome — most mutations are test gaps. Every batch writes its full
per-mutation results to S3 *before* deciding whether to raise, so a failed
branch loses nothing; `fn_score` and `fn_persist` read that prefix directly.
Lowering this number will abort healthy runs.

**Why `fn_score` loops back into itself.** One full-suite run costs seconds,
and thirty survivors do not fit in a 300s Lambda. It works to a remaining-time
budget and returns `done: false` when it runs out; the `ScoringComplete`
choice sends it straight back in. Completed work is already in S3, so a
continuation never repeats a run.

**Root-layout packages only.** pytest prepends the *rootdir* of the mutated
copy to `sys.path`, which shadows the installed package only when the package
sits at the repo root. An `src/`-layout repo would run its tests against the
unmutated installed copy and grade every challenge as already fixed. The
Dockerfile fails the build rather than let that happen, so the failure is
loud and happens on your machine.

**The two prefixes have different IAM, and that is the whole security model.**

| | `public/{id}/` | `answers/{id}/` |
|---|---|---|
| contents | `tree.tar.gz`, `traceback.txt` | `mutation.patch`, `reveal.json` |
| `fn_persist` | write | write |
| `fn_grade` | read | **no access** |
| `fn_api` (the only presigner) | read | **no access** |
| `fn_reveal` | — | read, returned only for a PASS submission, never presigned |

A presigned URL carries the signer's permissions, so the API physically
cannot mint a URL to an answer patch. Grading doesn't need one either — the
repo's own suite is the oracle, which works precisely because the mutation
was selected for being caught by that suite. Pipeline intermediates live
under `answers/_work/` rather than a third top-level prefix so the split
stays exactly two prefixes wide; challenge ids never begin with `_`.

**Bedrock is optional.** `fn_describe` is the only model call in the system
and it only writes a title and two sentences. Deploy with
`DisableBedrock=true` (env `BUGFORGE_DISABLE_BEDROCK`) and every challenge
still gets the deterministic template copy. The model sees the failing test
name, expected vs actual, the exception type, and the module docstring. Its
output must be strict JSON, a 2–3 word title, and exactly two sentences, and
must not name a file, line, or any identifier from the mutated scope. Output
that fails that check is retried once, then replaced by the template.

**Adding a repo.** Append it to `vetted_repos.json` with a pinned commit sha
(a moving branch silently changes every line number), then build and deploy a
second image. Dependencies are installed at image-build time on your machine,
never at runtime, and never from a repo that isn't on that list.

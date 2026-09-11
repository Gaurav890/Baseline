# Baseline architecture

## Production topology

```text
Browser ──HTTPS──> Vercel / Next.js 16
                       │
                       └──HTTPS──> Render / FastAPI ──> PostgreSQL
                                             │
                                             └──LPUSH──> Redis <──BRPOP── Worker
                                                                        │
                          SEC EDGAR / Tavily / GitHub <── allowlisted connectors
```

The browser reads metrics and integration health from the API. Vercel contains no database credentials. The API owns validation, persistence, webhook verification, and job creation; the worker owns long regression/benchmark execution. PostgreSQL is canonical state and Redis is delivery infrastructure, not a result store.

## Reliability and evidence flow

1. An agent contract constrains the available tools.
2. The deterministic planner selects a minimal sequence.
3. A connector retrieves evidence under timeout, size, retry, DNS/IP, HTTPS, and domain rules.
4. The tool span records connector, source URL, retrieval time, full-content hash, latency, attempt, and provenance.
5. A provider synthesizes an answer from the observation packet.
6. Deterministic/rubric evaluators persist scores and a primary failure class.
7. Suite jobs aggregate pass rate, failure distribution, latency, and cost against a prior observed baseline.

Execution failures are retained as failed runs with evaluator spans. A data source is `LIVE` only when the associated connector returned a successful health result. Configuration presence alone produces `unconfigured` or `unchecked`, never a live claim.

### Agent portfolio

The four focused agents cover research, document analysis, support, and extraction. Three
production-shaped agents exercise the same runtime with four-step plans:

```text
Due diligence:    SEC identity ──> web signals ──> risk rubric ──> approval gate
Release risk:     GitHub issues ─> web signals ──> risk rubric ──> approval gate
Incident response: incident ─────> GitHub issue ─> risk rubric ──> approval gate
```

These workflows do not introduce an orchestration framework beside the existing runner. The
deterministic planner, tool registry, connector metadata, safety policy, model call, evaluator, and
trace persistence remain one reviewable execution path. No registered workflow tool mutates an
external system; the final policy step records approval requirements only.

## Persistent entities

| Entity | Evidence captured |
| --- | --- |
| `AgentRun` | task, agent, provider/model, output, status, latency, tokens, cost, retries, escalation |
| `TraceSpan` | typed span hierarchy, timing, I/O, status, attempts, connector provenance and hashes |
| `Evaluation` | composite/dimension scores, rubric, verdict, rationale, failure class |
| `HumanReview` | reviewer, verdict, score, notes, timestamp |
| `EvalSuiteRun` | commit, scenario count, current/prior success, regression state, failures, cost, p95 |
| `EvaluationJob` | idempotency key/hash, request, status, queue backend, attempts, result/error, timestamps |
| `Integration` | configured and verified health, provenance, last check/success, error, latency, event count |
| `Experiment` | immutable dataset version and scenario IDs, candidate cohort, selected baseline, recommendation, job and lifecycle |
| `ExperimentCandidate` | provider/model, progress, rubric dimensions, pass rate, latency, cost, failures, and real baseline delta |
| `UserFeedback` | product rating, comment, source, run linkage, timestamp |
| `DatasetVersion` | immutable candidate scenarios promoted from reviewed production runs |
| `CanaryRun` | current non-blocking connector result, provenance, URL, hash, latency, and error |
| `SafetyPolicy` | versioned per-agent tool, mutation, call, time, cost, and approval boundaries |
| `SafetyEvent` | allowed/blocked/timed-out policy decision, action, reason, input hash, and run linkage |

Alembic owns the production schema. Application startup creates tables only outside production; Render runs `alembic upgrade head` before API promotion.

## Connector boundary

All external evidence implements one connector result contract. The production connectors are:

- `sec_edgar`: public SEC submissions/company-facts endpoints, a declared contact user-agent, and configurable fair-access pacing (4 requests/second per process by default);
- `tavily`: keyed search, absent/unconfigured without `TAVILY_API_KEY`;
- `github`: public REST issues by default, optionally authenticated using `GITHUB_TOKEN`;
- `url_fetch`: HTTPS retrieval restricted by the configured domain allowlist and address validation;
- `source_packet`: deterministic caller-supplied/frozen evidence, enabled for controlled local/CI paths.

URL retrieval rejects embedded credentials, non-HTTPS schemes, unapproved ports, non-allowlisted hosts, and loopback/private/link-local/multicast/reserved/unspecified addresses after resolution. Redirects are rejected instead of followed. Streaming response limits are enforced instead of trusting `Content-Length`; transient failures use bounded exponential backoff.

`LIVE`, `SNAPSHOT`, and `SYNTHETIC` describe evidence origin, not connector configuration. UI and trace consumers must keep them distinct.

## Evaluation execution

`POST /api/evaluation-jobs` persists a queued job before delivery. An `Idempotency-Key` maps to a hash of the request. The same key and payload returns the existing job; a changed payload conflicts. In local/tests, the inline queue executes synchronously while preserving the same record transitions. Production validates `JOB_BACKEND=redis`; an independent worker claims Redis entries and persists results. Claims use expiring, renewable database leases and bounded attempts. Idle workers rediscover queued or expired work, while an atomic database update prevents duplicate Redis delivery from running a live lease twice.

```text
create ──> queued ──> running ──> completed
                         └──────> failed
```

Production clients poll job status/results, so HTTP request lifetimes do not contain a 218-scenario regression run. The local/test inline backend intentionally finishes work during the create request while preserving the same persistent state transitions.

### Controlled comparison invariant

An experiment resolves its scenario IDs once, before it is queued. Small cohorts are selected deterministically across all four work categories. The worker iterates every candidate over that same ordered list and persists `AgentRun.experiment_candidate_id`; results therefore never mix models, dataset versions, scenario sets, or unrelated dates. The automatic baseline is the newest completed experiment with the identical dataset version and scenario ID list. Provider/model deltas are shown only when that same candidate identity exists in the baseline.

The category evaluator is intentionally different by work type: extraction requires strict typed fields, support requires the ticket/resolution/escalation contract, document evaluation checks facts and summary coverage, and research accepts grounded semantic coverage rather than requiring one exact JSON layout. The prompt still requests an exact schema so provider formatting differences do not dominate the score.

## Golden snapshots and canaries

The blocking catalog remains exactly 218 scenarios:

- 55 research (including 20 frozen public-source snapshots)
- 55 document analysis
- 54 support/multi-tool
- 54 structured extraction

`backend/app/evals/snapshots/public_sources.v1.json` records primary SEC and GitHub API URLs, one UTC retrieval time, hashes of the full downloaded response bodies, and the selected fields used by the oracles. Those `SNAPSHOT` scenarios are immutable regression inputs and safe for deterministic CI. Source bodies were retrieved directly on 2026-09-06; future upstream changes do not mutate the fixture.

`LIVE_CANARIES` are a separate non-blocking catalog. They test current connectivity and schema drift, may fail because of rate limits or upstream outages, and are never counted among the 218 merge-gating scenarios. A live canary result may be marked `LIVE` only after retrieval succeeds.

`SHOWCASE_SCENARIOS` is a third, explicitly non-blocking catalog. Its six
`workflow-showcase-v1` cases reuse the frozen SEC/GitHub sources, add visibly labeled synthetic
business or incident signals where needed, and exercise the three complex four-tool plans. The
public `/api/workflows` response exposes tasks, provenance, stages, tools, and output field names,
but not the answer oracle. This makes the portfolio understandable without weakening or expanding
the release gate implicitly.

## Production learning loop

`POST /api/traces/ingest` accepts a bounded typed trace under a dedicated ingestion secret and deduplicates by the caller’s external trace ID. Sensitive key names and email values are redacted before persistence. Imported spans are `SNAPSHOT` observations because ingestion proves receipt, not independent source liveness. A stable hash of the external ID applies the configured online sample rate; the initial online evaluator measures completion only and explicitly leaves correctness unclaimed.

Negative feedback and failing run/evaluation signals form a human-review queue. Promotion creates a new `candidate` `DatasetVersion` with parent linkage and a copy of prior scenarios. It never modifies `GOLDEN_SCENARIOS`, so a production incident becomes a proposed regression case requiring review rather than silently changing the deployment gate.

## GitHub PR check contract

GitHub sends a `pull_request` webhook to `/api/webhooks/github`. The API verifies the HMAC-SHA256 signature against the raw bounded body, validates the delivery ID, accepts only supported actions, applies the exact repository allowlist, and creates a delivery-idempotent evaluation job. Check output targets the pull request head SHA.

The intended GitHub App permissions are Pull requests: read, Checks: write, and Metadata: read, with only Pull request events subscribed. The worker prefers short-lived installation tokens derived from `GITHUB_APP_ID`, `GITHUB_PRIVATE_KEY`, and the webhook installation ID; a pre-provisioned `GITHUB_TOKEN` is a fallback. No App credentials or secrets are generated by this repository.

## Operational endpoints and logging

- `/health/live` proves the API process can answer.
- `/health/ready` checks database and queue dependencies and returns a non-success status when traffic should not be routed.
- Structured JSON logs include request ID, method, path, status, and duration without bodies, provider keys, webhook secrets, or tokens.

Production startup fails fast on unsafe configuration. Integration checks retain the last error for operators while public responses never expose credentials.

Provider calls have a configured wall-clock timeout. Experiment admission applies call-count, concurrent-job, and estimated-cost ceilings before any work is queued. GitHub checks use an explicitly configured real provider/model and derive the comparison point from the latest matching persisted regression; a first run has no baseline and does not fabricate one.

## Agent safety enforcement

Before planning, the runner loads the persisted policy for the requested agent type and checks the environment execution switch plus a conservative estimated provider cost. The deterministic plan must be a subset of the policy tool allowlist and remain within its call ceiling. Every tool is checked again immediately before execution; tools declare whether they mutate external state, and mutation is denied unless the policy explicitly allows it. Runtime is checked between steps, while model generation receives the smaller of the provider timeout and remaining run budget.

Policy decisions are append-only application events linked to the run and policy version. They store a canonical input hash rather than another copy of potentially sensitive tool input. Approval requirements are recorded with `external_mutation_executed=false`; because the current registry contains no external write tools, the system does not pretend to implement a durable approve/resume workflow.

`AGENT_EXECUTION_ENABLED=false` is the environment kill switch. Policy changes require a dedicated admin secret. The safety posture API and dashboard explicitly expose missing sandbox isolation, covert-channel detection, and workspace RBAC instead of rolling them into a misleading green status.

## Remaining public-deployment boundary

The implemented controls do not constitute multi-tenant authentication. Dashboard, experiment, review, and dataset endpoints still require an identity-provider integration plus persistent workspace membership and role checks before exposure as a public SaaS. Until then, deploy behind an organization access proxy or private network. Ingestion and webhook secrets protect those machine-to-machine entry points only; they are not substitutes for user authentication.

Redaction is applied at trace ingestion, but retention periods and deletion/legal-hold semantics are product policy and are not yet scheduled. Those policies must be chosen and implemented before claiming full retention compliance.

## Primary platform references

- [SEC EDGAR APIs](https://www.sec.gov/search-filings/edgar-application-programming-interfaces)
- [SEC fair-access guidance](https://www.sec.gov/search-filings/edgar-search-assistance/accessing-edgar-data)
- [GitHub webhook signature validation](https://docs.github.com/en/webhooks/using-webhooks/validating-webhook-deliveries)
- [GitHub Checks API permissions](https://docs.github.com/en/rest/guides/using-the-rest-api-to-interact-with-checks)
- [GitHub pull request webhook contract](https://docs.github.com/en/webhooks/webhook-events-and-payloads#pull_request)
- [Render Blueprint specification](https://render.com/docs/blueprint-spec)
- [Vercel monorepo configuration](https://vercel.com/docs/monorepos)

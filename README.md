# baseline

**The reliability control plane for AI agents.**

Baseline is a FastAPI and Next.js platform for running agents, recording evidence-rich traces, comparing models, and gating changes against exactly 218 regression scenarios.

![Baseline dashboard overview](docs/dashboard-preview.svg)

_Design preview with synthetic values; the production UI never substitutes these for API data._

## Production behavior

Production never seeds or substitutes fabricated metrics. `DEMO_MODE=false` and `NEXT_PUBLIC_DEMO_MODE=false` are the defaults; an empty database renders an honest empty state and an unavailable API renders a connection error. The backend rejects demo mode, fixture connectors, SQLite, inline jobs, HTTP frontend origins, or a missing SEC contact identity when `ENVIRONMENT=production`.

Every connector observation carries its source URL, retrieval time, content SHA-256, connector name, latency, and `LIVE`, `SNAPSHOT`, or `SYNTHETIC` provenance. The dashboard labels a source `LIVE` only after a successful health check.

## Run locally

```bash
cp .env.example .env
docker compose up --build
```

Open [http://localhost:3000](http://localhost:3000); API docs are at [http://localhost:8000/docs](http://localhost:8000/docs). Local mode starts empty. To preview synthetic data explicitly, set both `DEMO_MODE=true` and `NEXT_PUBLIC_DEMO_MODE=true`.

The Docker API runs migrations before startup. For local volumes created by an older build before Alembic existed, a development-only bootstrap recognizes the original table set, stamps revision `0001_initial_schema`, and upgrades it. It refuses unknown schemas and is never allowed to stamp a production database.

Without Docker:

```bash
cd backend
uv sync --dev
uv run alembic upgrade head
uv run uvicorn app.main:app --reload
```

```bash
cd frontend
npm ci
npm run dev
```

For deterministic connector fixtures in tests only, set both `ENVIRONMENT=test` and `BASELINE_FIXTURE_CONNECTORS=true`. Production validation rejects fixture mode.

### Plain-English dashboard terms

- Golden dataset: fixed "quality checklist" of 218 test cases used for reliability checks. You can browse task names, but not answers, so scoring stays fair.
- Evaluation suite: a batch run over test cases that produces pass/fail, cost, and latency.
- Model experiment: two to four models run the exact same immutable scenario IDs. The comparison is one controlled cohort, not an aggregate of unrelated history.
- Baseline: the most recent completed experiment with the same dataset version and exact scenario IDs. A first run honestly shows “No baseline.”
- Live sources: the sources marked as LIVE are those successfully verified against real connectors.

## Connectors and provenance

- SEC EDGAR uses the public submissions and company-facts APIs without a key. Set `SEC_USER_AGENT` to an application name plus monitored email address. `SEC_REQUESTS_PER_SECOND` defaults to 4 per process so the default API-plus-worker topology remains below the SEC's published 10-request/second ceiling.
- Tavily is configured only when `TAVILY_API_KEY` is present.
- GitHub reads public issues anonymously, or uses `GITHUB_TOKEN` for higher limits/private access explicitly granted to the token.
- Direct URL retrieval is HTTPS-only, resolves and rejects private/link-local/reserved targets, follows only allowlisted hosts, enforces response limits and timeouts, and retries transient failures with backoff.

The data-source panel reports configured state, last successful check, event count, and provenance. “Configured” is not treated as proof of connectivity.

## Evaluation jobs

Regression and benchmark runs use a persistent job contract. Local/tests use the synchronous `inline` backend; production uses the API plus Redis-backed worker.

```bash
curl -X POST http://localhost:8000/api/evaluation-jobs \
  -H 'Content-Type: application/json' \
  -H 'Idempotency-Key: pr-184-head-8f3c2a1' \
  -d '{"kind":"regression","regression":{"provider":"mock","limit":218,"baseline_success_rate":0.90,"threshold":0.01}}'
```

Poll `GET /api/evaluation-jobs/{id}` and read `GET /api/evaluation-jobs/{id}/results`. Reusing a key with the same payload returns the original job; reusing it with a different payload is rejected. Headerless requests use a canonical-payload key scoped to the UTC hour, but callers should send an explicit stable key. Run the production worker with `python -m app.jobs.worker`.

Redis workers claim jobs with expiring leases, renew the lease while work runs, and retry up to `JOB_MAX_ATTEMPTS`. An idle worker requeues abandoned or queued jobs; database-side atomic claims make duplicate Redis deliveries harmless. Local inline execution uses the same retry state machine.

The checked-in gate remains deterministic:

```bash
cd backend
ENVIRONMENT=test BASELINE_FIXTURE_CONNECTORS=true uv run python -m app.evals.cli --provider mock --all --baseline 0.90 --threshold 0.01
```

Twenty of the 218 blocking scenarios are frozen, versioned snapshots derived from primary SEC and GitHub API payloads. Their retrieval timestamp, source URL, selected fields, and full-response hash are checked in at `backend/app/evals/snapshots/public_sources.v1.json`. Live SEC/GitHub canaries are defined separately, do not belong to the golden catalog, and never block CI.

## Controlled model comparisons

The dashboard’s **New comparison** action opens the persisted experiment builder. Configure at least two provider keys, choose 20, 60, or all 218 cases, and review the call count before starting. A 20-case cohort is category-balanced: five research, five document, five support, and five extraction cases. Every candidate receives the same ordered IDs and each `AgentRun` retains its candidate link.

```bash
curl -X POST http://localhost:8000/api/experiments \
  -H 'Content-Type: application/json' \
  -H 'Idempotency-Key: model-selection-2026-09-07' \
  -d '{"name":"Production model selection","scenario_limit":20,"candidates":[{"provider":"openai"},{"provider":"anthropic"},{"provider":"gemini"}]}'
```

The result stores mean rubric quality, pass rate, correctness dimensions, p50/p95 latency, total and per-task cost, failure classes, and a candidate-by-candidate delta where the previous matching experiment included the same provider/model. The dashboard’s failure view exposes the task, model output, rationale, and rubric without exposing the hidden golden answer.

Before queueing, the API enforces `EXPERIMENT_MAX_CALLS`, `EXPERIMENT_MAX_CONCURRENT`, and an estimated provider-spend ceiling. Every model call has a hard `PROVIDER_TIMEOUT_SECONDS` deadline. Tune the token estimate and provider price variables for your prompts and contracts; the limit is a guardrail, not an invoice.

## Production feedback loop

Production systems can import completed traces through `POST /api/traces/ingest` using a secret `X-Ingest-Key`. Imports are idempotent by `external_trace_id`, redact secret-bearing fields and email addresses, and label imported spans `SNAPSHOT` rather than pretending the API independently verified them as live. `ONLINE_EVAL_SAMPLE_RATE` deterministically samples traces for a completion-only online check; its rubric explicitly does not claim answer correctness.

User feedback is recorded at `POST /api/runs/{id}/feedback`. Negative feedback, failed runs, and failed evaluations enter `GET /api/review-queue` until a human review exists. A reviewed production failure can be copied into an immutable candidate dataset version with `POST /api/runs/{id}/promote`; this never mutates the 218-case blocking catalog automatically.

The dashboard’s **Human review** section makes that queue understandable to operators, records named pass/fail/needs-review decisions, and shows the resulting candidate dataset versions. A `needs_review` note deliberately leaves the run open; only a final pass or fail clears it.

The dashboard’s live-canary section calls `POST /api/canaries/run` on demand and displays persisted SEC/GitHub outcomes. These checks are current and non-blocking; failed connectivity cannot corrupt deterministic regression results.

## Agent safety control plane

The dashboard’s **Agent safety** section separates six concerns: scope, authority, budget, verification, containment, and auditability. Four persisted agent policies enforce the tool allowlist, mutating-tool permission, maximum tool calls, wall-clock deadline, preflight model-cost ceiling, and approval threshold for each work type. The runner attaches the policy ID/version to the root trace and records every plan/tool decision as a `SafetyEvent`; raw policy inputs are represented by a SHA-256 hash rather than duplicated into the decision ledger.

Set `AGENT_EXECUTION_ENABLED=false` to stop new agent work throughout an API/worker environment. Safety policy administration is machine-to-machine and requires `X-Safety-Admin-Key`; never put `SAFETY_ADMIN_KEY` in a `NEXT_PUBLIC_*` variable.

```bash
curl -X PATCH http://localhost:8000/api/safety/policies/support \
  -H 'Content-Type: application/json' \
  -H "X-Safety-Admin-Key: $SAFETY_ADMIN_KEY" \
  -d '{"max_tool_calls":3,"max_runtime_seconds":30,"max_estimated_cost_usd":0.50}'
```

The posture endpoint intentionally reports isolated execution sandboxing, covert-channel detection, and authenticated workspace roles as `not_implemented`. Current registered tools do not execute arbitrary code and are non-mutating; URL retrieval is still restricted by the connector containment boundary. This is an enforceable first layer, not a claim of frontier-model containment.

## GitHub PR evaluation checks

No GitHub App is created or registered by this repository. Perform these steps in GitHub:

1. Create a GitHub App owned by the target organization or account.
2. Grant repository permissions **Pull requests: Read-only**, **Checks: Read and write**, and **Metadata: Read-only**. Do not grant Contents write, Administration, or organization permissions.
3. Subscribe only to **Pull request** events.
4. Set the webhook URL to `https://<api-host>/api/webhooks/github`, generate a strong random webhook secret, select `application/json`, and enable SSL verification.
5. Install the App only on the repositories that should run evaluations.
6. Set `GITHUB_WEBHOOK_SECRET`, `GITHUB_APP_ID`, the PEM `GITHUB_PRIVATE_KEY`, and an exact comma-separated `GITHUB_REPOSITORY_ALLOWLIST=owner/repo`. Set `GITHUB_EVALUATION_PROVIDER` and optionally `GITHUB_EVALUATION_MODEL` to the real candidate used by your product. The worker exchanges the installation ID for short-lived tokens. `GITHUB_TOKEN` is a fallback that must have Checks write access. Then set `GITHUB_CHECKS_ENABLED=true` and redeploy both API and worker.
7. Send GitHub’s ping and open or update a test pull request. Confirm the API accepts the signed delivery once, creates one idempotent job, and publishes the configured check name on the head SHA.

The endpoint verifies `X-Hub-Signature-256` over the raw body, bounds payload size, validates delivery IDs, filters event/action types, and rejects repositories outside the allowlist. Use a secret manager and rotate the webhook secret and App private key regularly.

The PR result compares the configured provider/model against the latest persisted regression suite for that exact provider, model, and scenario count. If none exists, the first check reports no baseline and cannot claim a regression. The synthetic provider is rejected for enabled production checks.

## API surface

| Endpoint | Purpose |
| --- | --- |
| `GET /health/live` | Process liveness |
| `GET /health/ready` | Database and queue readiness |
| `GET /api/dashboard` | Observed quality, operations, benchmarks, and suite history |
| `GET /api/safety` | Active agent contracts, containment configuration, gaps, and decision events |
| `PATCH /api/safety/policies/{agent_type}` | Admin-key-protected safety policy update |
| `GET /api/integrations` | Connector configuration and proven health |
| `POST /api/integrations/{name}/check` | Perform and persist a connection check |
| `POST /api/runs` / `GET /api/runs/{id}` | Execute a task / read its trace |
| `POST /api/evaluation-jobs` | Idempotently create regression or benchmark work |
| `GET /api/evaluation-jobs/{id}` | Read job state |
| `GET /api/evaluation-jobs/{id}/results` | Read completed results |
| `POST /api/experiments` | Create an idempotent, same-cohort multi-model comparison |
| `GET /api/experiments` / `GET /api/experiments/{id}` | List or inspect persisted comparisons |
| `GET /api/experiments/{id}/failures` | Inspect failed candidate runs side by side |
| `POST /api/traces/ingest` | Idempotently import and redact a production trace |
| `POST /api/runs/{id}/feedback` / `GET /api/review-queue` | Capture product feedback and triage quality signals |
| `POST /api/runs/{id}/promote` / `GET /api/dataset-versions` | Version production failures as candidate tests |
| `GET /api/canaries` / `POST /api/canaries/run` | Read or execute non-blocking live connector checks |
| `POST /api/webhooks/github` | Receive verified PR events |

## Deploy

`render.yaml` provisions the API, worker, PostgreSQL, and Redis. Create a Render Blueprint from the repository, fill every `sync: false` secret/config value, verify `/health/ready`, then point `FRONTEND_ORIGIN` at the final HTTPS frontend origin. The API pre-deploy step runs `alembic upgrade head`.

For Vercel, import the same repository and set the project Root Directory to `frontend`. Configure `NEXT_PUBLIC_API_URL=https://<render-api-host>` and `NEXT_PUBLIC_DEMO_MODE=false`. Build settings are detected from `frontend/vercel.json`. Redeploy after changing any `NEXT_PUBLIC_*` value because it is embedded at build time.

Do not expose PostgreSQL or Redis publicly. Keep Render internal connection strings, restrict allowed frontend origins, and leave GitHub checks disabled until all least-privilege settings are present.

### Security boundary still required for a public SaaS

This slice is suitable for a private, access-controlled deployment; it is not yet a multi-tenant SaaS boundary. The dashboard and experiment APIs do not authenticate human users or enforce workspace membership. Put the deployment behind platform access controls until an identity provider, workspace/member model, and owner/editor/viewer authorization rules are implemented end to end. Choosing that identity provider is intentionally not hidden behind a fake local user.

Trace ingestion already applies bounded payloads, an ingestion secret, and redaction, but automated retention deletion is also still outstanding. Define the legal/product retention periods before adding a scheduled deletion job so evidence needed for audits is not silently destroyed.

## Verification

```bash
cd backend
uv run ruff check app tests
uv run pytest -q
ENVIRONMENT=test BASELINE_FIXTURE_CONNECTORS=true uv run python -m app.evals.cli --provider mock --all --baseline 0.90 --threshold 0.01
uv run alembic upgrade head

cd ../frontend
npm ci
npm run lint
NEXT_PUBLIC_DEMO_MODE=false NEXT_PUBLIC_API_URL=https://api.example.invalid npm run build
```

See [docs/architecture.md](docs/architecture.md) for boundaries, state transitions, deployment topology, and source references.

## License

MIT

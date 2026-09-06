# Baseline architecture

## Reliability loop

Each task moves through a fixed evidence pipeline:

1. An agent contract limits the available tools and defines the response policy.
2. The planner selects a minimal tool sequence and records its reasoning.
3. Every tool call persists typed input, output, timing, parent span, status, and attempt number.
4. A provider adapter synthesizes the final answer from the same observation packet.
5. Deterministic and rubric evaluators score the output and assign one primary failure class.
6. The suite runner aggregates pass rate, failure distribution, p95 latency, and cost against a prior baseline.

The system does not discard failed runs. An execution failure still produces an evaluator span and a complete run record.

## Persistent entities

| Entity | Evidence captured |
| --- | --- |
| `AgentRun` | task, agent, provider/model, plan, output, status, latency, tokens, cost, retries, escalation |
| `TraceSpan` | agent/planner/tool/model/evaluator span, parent, timing, I/O, arbitrary attributes |
| `Evaluation` | composite score, metric dimensions, rubric, verdict, rationale, failure class |
| `HumanReview` | reviewer, verdict, score, notes, timestamp |
| `EvalSuiteRun` | commit, scenario count, current/prior success, regression state, failures, cost, p95 |

JSON columns keep provider and tool payloads flexible while typed top-level columns make operational queries fast. PostgreSQL is the deployment target; SQLite is supported for tests and a zero-setup local demo.

## Agent contracts

| Agent | Primary work | Allowed tools |
| --- | --- | --- |
| Research Analyst | grounded briefs and evidence synthesis | `web_search`, `document_search` |
| Document Examiner | passage retrieval, clause analysis, field extraction | `document_search`, `extract_fields` |
| Support Resolver | multi-step issue resolution with authority boundaries | `ticket_lookup`, `knowledge_base_search`, `policy_check` |
| Data Extractor | typed extraction and validation | `extract_fields`, `validate_schema` |

The deterministic planner is intentional: its rules are versionable, directly testable, and easy to blame when tool-selection accuracy moves. A learned or LangGraph planner can implement the same planner contract later without changing downstream traces or evaluators.

## Evaluation model

The default composite score is:

```text
30% task completion
40% answer correctness
20% tool selection accuracy
10% groundedness (1 − hallucination rate)
```

An evaluation passes at `score >= 0.85` with no classified failure. Classification priority is execution error, incomplete task, tool selection, hallucination, then incorrect answer. That priority yields one actionable primary cause while the full rubric preserves secondary signals.

### Golden catalog

The catalog contains 218 generated-but-versioned scenarios:

- 55 research scenarios
- 55 document-analysis scenarios
- 54 support and multi-tool scenarios
- 54 structured-extraction scenarios

Each scenario has a stable ID, task, controlled context, expected answer, expected tool sequence, and tags. Tests assert the exact count, uniqueness, category balance, and presence of oracles.

## Provider comparison

The benchmark runner sends the same scenario slice through each configured provider. The comparison does not mix unmatched workloads. Results include:

- success rate as the quality axis;
- total and per-task cost from observed token use;
- p95 end-to-end latency;
- the exact provider and model identifier;
- the evaluated scenario count.

Provider pricing is environment-driven. Historical reports keep recorded dollar values even after configured rates change.

## Extension points

- Replace individual tools with real search, ticketing, or document services behind `ToolRegistry`.
- Add a provider by implementing the `ModelProvider` protocol and registering its cost function.
- Add semantic or LLM judge evaluators beside the deterministic rubric; keep each verdict as a separate `Evaluation` record when judge calibration matters.
- Export `TraceSpan` records to an OpenTelemetry collector or Langfuse without changing the canonical database trace.
- Move long suites to a job queue when running hundreds of paid, concurrent model requests.
- Add authentication and workspace scoping before exposing the API outside a trusted environment.


"use client";

import { useCallback, useEffect, useState } from "react";

import { demoData, demoIntegrations } from "@/lib/demo-data";
import type {
  DashboardData,
  GoldenScenario,
  GoldenScenariosResponse,
  Integration,
  IntegrationsResponse,
  RecentRun,
  RunDetail,
} from "@/lib/types";
import { Icon } from "./icon";
import { AgentSafetyPanel } from "./agent-safety-panel";
import { CanaryPanel } from "./canary-panel";
import { ExperimentWorkbench } from "./experiment-workbench";
import { TrendChart } from "./trend-chart";
import { ProductionLearningPanel } from "./production-learning-panel";

const apiUrl = (process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000").replace(/\/$/, "");
const demoMode = process.env.NEXT_PUBLIC_DEMO_MODE === "true";

type LoadStatus = "loading" | "ready" | "empty" | "error" | "demo";
type TraceStatus = "loading" | "ready" | "error" | "demo";
type ScenarioCategory = GoldenScenario["category"] | "all";

const DATASET_PAGE_SIZE = 12;
const scenarioCategories: Array<{ value: ScenarioCategory; label: string }> = [
  { value: "all", label: "All test cases" },
  { value: "research", label: "Research" },
  { value: "document", label: "Documents" },
  { value: "support", label: "Customer support" },
  { value: "extraction", label: "Data extraction" },
];

const agentTypeLabels: Record<string, string> = {
  research: "Research",
  document: "Documents",
  support: "Customer support",
  extraction: "Data extraction",
};

const formatPercent = (value: number, digits = 1) => `${(value * 100).toFixed(digits)}%`;
const formatCost = (value: number) => `$${value.toFixed(value < 0.1 ? 3 : 2)}`;
const formatLatency = (value: number) =>
  value >= 1000 ? `${(value / 1000).toFixed(2)}s` : `${value}ms`;
const titleCase = (value: string) =>
  value.replaceAll("_", " ").replace(/\b\w/g, (character) => character.toUpperCase());
const agentTypeLabel = (value: string) => agentTypeLabels[value] ?? titleCase(value);
const formatDate = (value: string | null) => {
  if (!value) return "Never";
  const date = new Date(value);
  return Number.isNaN(date.getTime())
    ? "Unavailable"
    : `${new Intl.DateTimeFormat("en-US", {
        dateStyle: "medium",
        timeStyle: "short",
        timeZone: "UTC",
      }).format(date)} UTC`;
};

function hasDashboardEvidence(data: DashboardData) {
  return Boolean(
    data.operations.runs ||
      data.recent_runs.length ||
      data.latest_suite ||
      data.model_comparison.some((model) => model.runs > 0),
  );
}

function MetricCard({ eyebrow, value, detail }: { eyebrow: string; value: string; detail: string }) {
  return (
    <article className="metric-card">
      <div>
        <span className="eyebrow">{eyebrow}</span>
        <strong>{value}</strong>
      </div>
      <span className="metric-detail">{detail}</span>
    </article>
  );
}

function ProviderMark({ provider }: { provider: string }) {
  return (
    <span className={`provider-mark ${provider}`} aria-hidden="true">
      {provider.slice(0, 1).toUpperCase()}
    </span>
  );
}

function DashboardState({ status, onRetry }: { status: LoadStatus; onRetry: () => void }) {
  const isError = status === "error";
  return (
    <section className={`dashboard-state ${isError ? "error" : ""}`} aria-live="polite">
      <span className="state-icon">
        <Icon name={isError ? "warning" : status === "loading" ? "refresh" : "database"} />
      </span>
      <div>
        <span className="eyebrow">{isError ? "Connection error" : "Workspace state"}</span>
        <h2>
          {status === "loading"
            ? "Loading observed reliability data…"
            : isError
              ? "The API could not be reached"
              : "No production runs recorded yet"}
        </h2>
        <p>
          {status === "loading"
            ? "Metrics will appear after the API responds."
            : isError
              ? "Check NEXT_PUBLIC_API_URL and API readiness. No demo metrics were substituted."
              : "Run an agent or evaluation job to populate metrics. Empty workspaces stay empty by design."}
        </p>
      </div>
      {status !== "loading" ? (
        <button className="secondary-button" onClick={onRetry}>
          <Icon name="refresh" />Retry
        </button>
      ) : null}
    </section>
  );
}

function DataSources({
  integrations,
  status,
  error,
  checking,
  onCheck,
  onRetry,
}: {
  integrations: Integration[];
  status: LoadStatus;
  error: string | null;
  checking: string | null;
  onCheck: (integration: Integration) => void;
  onRetry: () => void;
}) {
  return (
    <section className="panel sources-panel" id="sources">
      <div className="panel-head">
        <div><span className="eyebrow">Evidence plane</span><h3>Production data sources</h3></div>
        <span className={`source-summary ${status}`}>
          {status === "loading"
            ? "Checking API"
            : status === "error"
              ? "Unavailable"
              : `${integrations.filter((item) => item.live && item.health_status === "healthy").length} live`}
        </span>
      </div>

      {status === "loading" ? (
        <div className="source-state" aria-live="polite">Loading configured integrations…</div>
      ) : status === "error" ? (
        <div className="source-state error" role="alert">
          <span>{error ?? "Integration health is unavailable."}</span>
          <button className="text-button" onClick={onRetry}>Retry</button>
        </div>
      ) : integrations.length === 0 ? (
        <div className="source-state">No integrations are registered in this environment.</div>
      ) : (
        <div className="sources-table" role="table" aria-label="Configured data sources">
          <div className="source-row source-header" role="row">
            <span>Source</span><span>Status</span><span>Provenance</span><span>Last successful sync</span><span>Events</span><span />
          </div>
          {integrations.map((integration) => {
            const provenLive =
              integration.live &&
              integration.health_status === "healthy" &&
              integration.provenance === "LIVE";
            const provenance = provenLive
              ? "LIVE"
              : integration.provenance === "SNAPSHOT"
                ? "SNAPSHOT"
                : integration.provenance === "SYNTHETIC"
                  ? "SYNTHETIC"
                  : "UNVERIFIED";
            const health = status === "demo"
              ? "Demo fixture"
              : !integration.configured
              ? "Unconfigured"
              : provenLive
                ? "Healthy"
                : titleCase(integration.health_status || "unchecked");
            return (
              <div className="source-row" role="row" key={integration.id || integration.name}>
                <span className="source-name">
                  <span className={`source-mark ${integration.name.slice(0, 3)}`}>{integration.display_name.slice(0, 1)}</span>
                  <span><strong>{integration.display_name}</strong><small>{integration.name}</small></span>
                </span>
                <span className={`health-badge ${status === "demo" ? "unchecked" : provenLive ? "healthy" : integration.health_status}`}><i />{health}</span>
                <span className={`provenance ${provenance.toLowerCase()}`}>{provenance}</span>
                <span className="sync-time">{formatDate(integration.last_success_at)}</span>
                <strong className="event-count">{integration.event_count.toLocaleString()}</strong>
                <button
                  className="text-button source-check"
                  disabled={!integration.configured || status === "demo" || checking === integration.name}
                  onClick={() => onCheck(integration)}
                  aria-label={`Check ${integration.display_name} connection`}
                >
                  {checking === integration.name ? "Checking…" : "Check"}
                </button>
                {integration.last_error ? <small className="source-error">{integration.last_error}</small> : null}
              </div>
            );
          })}
        </div>
      )}
      {error && status !== "error" ? <div className="source-inline-error" role="alert">{error}</div> : null}
      <p className="source-footnote">LIVE is shown only after a successful connector check. Snapshot and synthetic evidence are labeled independently.</p>
    </section>
  );
}

function GoldenDatasetBrowser() {
  const [category, setCategory] = useState<ScenarioCategory>("all");
  const [offset, setOffset] = useState(0);
  const [catalog, setCatalog] = useState<GoldenScenariosResponse | null>(null);
  const [status, setStatus] = useState<LoadStatus>(demoMode ? "demo" : "loading");
  const [reloadKey, setReloadKey] = useState(0);

  useEffect(() => {
    if (demoMode) return;

    const controller = new AbortController();
    const params = new URLSearchParams({
      limit: String(DATASET_PAGE_SIZE),
      offset: String(offset),
    });
    if (category !== "all") params.set("category", category);

    const loadCatalog = async () => {
      setStatus("loading");
      try {
        const response = await fetch(`${apiUrl}/api/scenarios?${params}`, {
          cache: "no-store",
          signal: controller.signal,
        });
        if (!response.ok) throw new Error("Golden dataset unavailable");
        const payload = (await response.json()) as GoldenScenariosResponse;
        setCatalog(payload);
        setStatus(payload.items.length ? "ready" : "empty");
      } catch {
        if (controller.signal.aborted) return;
        setCatalog(null);
        setStatus("error");
      }
    };

    void loadCatalog();
    return () => controller.abort();
  }, [category, offset, reloadKey]);

  const total = catalog?.total ?? 0;
  const currentPage = Math.floor(offset / DATASET_PAGE_SIZE) + 1;
  const totalPages = Math.max(1, Math.ceil(total / DATASET_PAGE_SIZE));

  const chooseCategory = (value: ScenarioCategory) => {
    setCategory(value);
    setOffset(0);
  };

  return (
    <section className="panel dataset-browser" id="datasets" aria-labelledby="dataset-browser-title">
      <div className="panel-head dataset-browser-head">
        <div>
          <span className="eyebrow">Test library</span>
          <h3 id="dataset-browser-title">Golden dataset browser</h3>
        </div>
        <span className={`dataset-count ${status}`}>{status === "ready" ? `${total} test cases` : status === "loading" ? "Loading" : "Test library"}</span>
      </div>
      <p className="dataset-description">
        This is the checklist we use to see whether an agent can handle real work reliably. Each row is one test case; the expected answer stays private so the test remains fair.
      </p>
      <div className="dataset-explainer">
        <div><strong>Job</strong><span>What we ask the agent to do.</span></div>
        <div><strong>Work type</strong><span>The kind of task: research, documents, support, or extraction.</span></div>
        <div><strong>Information used</strong><span>The tools or sources the agent should consult.</span></div>
      </div>

      <div className="dataset-filter" aria-label="Filter golden scenarios by category">
        {scenarioCategories.map((option) => (
          <button
            className={category === option.value ? "active" : ""}
            key={option.value}
            type="button"
            aria-pressed={category === option.value}
            onClick={() => chooseCategory(option.value)}
          >
            {option.label}
          </button>
        ))}
      </div>

      {status === "loading" ? <div className="dataset-state" aria-live="polite">Loading test cases…</div> : null}
      {status === "demo" ? <div className="dataset-state">Demo mode does not expose the production catalog. Connect the API to browse it.</div> : null}
      {status === "error" ? (
        <div className="dataset-state error" role="alert">
          <span>The Golden Dataset API could not be reached.</span>
          <button className="text-button" type="button" onClick={() => setReloadKey((value) => value + 1)}>Retry</button>
        </div>
      ) : null}
      {status === "empty" ? <div className="dataset-state">No test cases match this category.</div> : null}

      {status === "ready" && catalog ? (
        <>
          <div className="dataset-table" role="table" aria-label="Golden dataset test cases">
            <div className="dataset-row dataset-header" role="row">
              <span>Test case</span><span>Job for the agent</span><span>Information it should use</span><span>Topics</span>
            </div>
            {catalog.items.map((scenario) => (
              <div className="dataset-row" role="row" key={scenario.id}>
                <span className="scenario-id"><code>{scenario.id}</code><em className={scenario.category}>{agentTypeLabel(scenario.category)}</em></span>
                <span className="scenario-task">{scenario.task}</span>
                <span className="tool-tags">{scenario.expected_tools.map((tool) => <code key={tool}>{tool}</code>)}</span>
                <span className="scenario-tags">{scenario.tags.slice(0, 3).map((tag) => <em key={tag}>{tag}</em>)}</span>
              </div>
            ))}
          </div>
          <div className="dataset-pagination">
            <span>Showing {offset + 1}–{Math.min(offset + catalog.items.length, total)} of {total} test cases</span>
            <div>
              <button className="secondary-button" type="button" disabled={offset === 0} onClick={() => setOffset((value) => Math.max(0, value - DATASET_PAGE_SIZE))}>Previous</button>
              <strong>Page {currentPage} / {totalPages}</strong>
              <button className="secondary-button" type="button" disabled={offset + DATASET_PAGE_SIZE >= total} onClick={() => setOffset((value) => value + DATASET_PAGE_SIZE)}>Next</button>
            </div>
          </div>
        </>
      ) : null}
    </section>
  );
}

function RunDrawer({ run, traceStatus, onClose }: { run: RecentRun | RunDetail; traceStatus: TraceStatus; onClose: () => void }) {
  const detail = "spans" in run ? run : null;
  const evaluation = detail?.evaluations.at(-1);
  return (
    <div className="drawer-backdrop" onMouseDown={onClose}>
      <aside className="run-drawer" onMouseDown={(event) => event.stopPropagation()} aria-label="Run trace" aria-live="polite" role="dialog" aria-modal="true">
        <div className="drawer-head"><div><span className="eyebrow">Trace explorer</span><h2>{run.id}</h2></div><button className="icon-button" onClick={onClose} aria-label="Close trace">×</button></div>
        <div className="drawer-summary"><div><span>Score</span><strong>{run.score !== null ? formatPercent(run.score, 0) : "—"}</strong></div><div><span>Latency</span><strong>{formatLatency(run.latency_ms)}</strong></div><div><span>Cost</span><strong>{formatCost(run.cost_usd)}</strong></div></div>
        <p className="drawer-task">{run.task}</p>

        {traceStatus === "loading" ? <div className="trace-state">Loading persisted spans…</div> : null}
        {traceStatus === "error" ? <div className="trace-state error">Trace details could not be loaded. The run summary above is still observed data.</div> : null}
        {traceStatus === "demo" ? <div className="trace-state">Demo mode includes a synthetic summary only; no spans are represented as observed.</div> : null}
        {traceStatus === "ready" && detail ? (
          <>
            {detail.spans.length ? (
              <div className="trace-list">
                {detail.spans.map((span, index) => {
                  const sourceUrl = typeof span.attributes.source_url === "string" ? span.attributes.source_url : null;
                  const provenance = typeof span.attributes.provenance === "string" ? span.attributes.provenance : null;
                  return (
                    <div className="trace-step" key={span.id}>
                      <div className={`trace-dot ${span.status === "error" ? "error" : ""}`}>{index + 1}</div>
                      <div className="trace-copy"><span>{titleCase(span.kind)}</span><strong>{span.name}</strong><p>{provenance ? `${provenance} provenance` : "Recorded span"}{sourceUrl ? ` · ${sourceUrl}` : ""}</p></div>
                      <code>{formatLatency(span.duration_ms)}</code>
                    </div>
                  );
                })}
              </div>
            ) : <div className="trace-state">This run has no persisted spans.</div>}
            {evaluation ? (
              <div className="json-preview"><div><span>Evaluator output</span><code>application/json</code></div><pre>{JSON.stringify({ score: evaluation.score, passed: evaluation.passed, failure_classification: evaluation.failure_classification, rubric: evaluation.rubric }, null, 2)}</pre></div>
            ) : null}
          </>
        ) : null}
      </aside>
    </div>
  );
}

export function Dashboard() {
  const [data, setData] = useState<DashboardData | null>(demoMode ? demoData : null);
  const [dataStatus, setDataStatus] = useState<LoadStatus>(demoMode ? "demo" : "loading");
  const [integrations, setIntegrations] = useState<Integration[]>(demoMode ? demoIntegrations : []);
  const [integrationStatus, setIntegrationStatus] = useState<LoadStatus>(demoMode ? "demo" : "loading");
  const [integrationError, setIntegrationError] = useState<string | null>(null);
  const [checking, setChecking] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [selectedRun, setSelectedRun] = useState<RecentRun | RunDetail | null>(null);
  const [traceStatus, setTraceStatus] = useState<TraceStatus>(demoMode ? "demo" : "loading");

  const loadAll = useCallback(async () => {
    if (demoMode) {
      setData(demoData);
      setDataStatus("demo");
      setIntegrations(demoIntegrations);
      setIntegrationStatus("demo");
      setIntegrationError(null);
      return;
    }
    setDataStatus("loading");
    setIntegrationStatus("loading");
    setIntegrationError(null);
    const [dashboardResult, integrationsResult] = await Promise.allSettled([
      fetch(`${apiUrl}/api/dashboard`, { cache: "no-store" }),
      fetch(`${apiUrl}/api/integrations`, { cache: "no-store" }),
    ]);

    if (dashboardResult.status === "fulfilled" && dashboardResult.value.ok) {
      const payload = (await dashboardResult.value.json()) as DashboardData;
      setData(payload);
      setDataStatus(hasDashboardEvidence(payload) ? "ready" : "empty");
    } else {
      setData(null);
      setDataStatus("error");
    }

    if (integrationsResult.status === "fulfilled" && integrationsResult.value.ok) {
      const payload = (await integrationsResult.value.json()) as IntegrationsResponse;
      setIntegrations(payload.items ?? []);
      setIntegrationStatus("ready");
    } else {
      setIntegrations([]);
      setIntegrationStatus("error");
      setIntegrationError("The API did not return integration health.");
    }
  }, []);

  useEffect(() => {
    const timeout = window.setTimeout(() => void loadAll(), 0);
    return () => window.clearTimeout(timeout);
  }, [loadAll]);

  const checkIntegration = async (integration: Integration) => {
    setChecking(integration.name);
    setIntegrationError(null);
    try {
      const response = await fetch(`${apiUrl}/api/integrations/${encodeURIComponent(integration.name)}/check`, { method: "POST" });
      if (!response.ok) throw new Error("Connection check failed");
      const updated = (await response.json()) as Integration;
      setIntegrations((current) => current.map((item) => item.name === updated.name ? updated : item));
    } catch {
      setIntegrationError(`${integration.display_name} could not be verified.`);
    } finally {
      setChecking(null);
    }
  };

  const openRun = async (run: RecentRun) => {
    setSelectedRun(run);
    if (demoMode) {
      setTraceStatus("demo");
      return;
    }
    setTraceStatus("loading");
    try {
      const response = await fetch(`${apiUrl}/api/runs/${encodeURIComponent(run.id)}`, { cache: "no-store" });
      if (!response.ok) throw new Error("Trace unavailable");
      const detail = (await response.json()) as RunDetail;
      setSelectedRun({ ...run, ...detail });
      setTraceStatus("ready");
    } catch {
      setTraceStatus("error");
    }
  };

  const suite = data?.latest_suite;
  const connectionLabel = dataStatus === "demo" ? "Demo mode" : dataStatus === "ready" || dataStatus === "empty" ? "API connected" : dataStatus === "loading" ? "Connecting" : "API unavailable";

  return (
    <div className="app-shell">
      <aside className="sidebar">
        <a className="brand" href="#top" aria-label="Baseline home"><span className="brand-glyph"><i /><i /><i /></span><span>baseline<span>.</span></span></a>
        <nav>
          <span className="nav-label">Workspace</span>
          <a className="active" href="#overview"><Icon name="overview" />Overview</a>
          <a href="#evaluation"><Icon name="flask" />Evaluation suites</a>
          <a href="#traces"><Icon name="trace" />Run traces</a>
          <a href="#datasets"><Icon name="database" />Golden datasets<span className="nav-count">218</span></a>
          <span className="nav-label second">Manage</span>
          <a href="#models"><Icon name="compare" />Model comparison</a>
          <a href="#sources"><Icon name="database" />Data sources</a>
          <a href="#safety"><Icon name="check" />Agent safety</a>
          <a href="#review-queue"><Icon name="overview" />Human review</a>
        </nav>
        <div className="sidebar-foot"><div className={`status-line ${dataStatus}`}><span className="status-dot" />{connectionLabel}</div><div className="workspace-switcher"><span className="workspace-avatar">AR</span><div><strong>Agent Reliability</strong><span>{demoMode ? "demo" : "production"}</span></div><span>···</span></div></div>
      </aside>

      <main id="top">
        <header className="topbar">
          <div><span className="breadcrumb">WORKSPACE / OVERVIEW</span><h1>Agent Reliability Lab</h1></div>
          <div className="top-actions"><span className={`environment ${dataStatus}`}><i />{connectionLabel}</span><button className="secondary-button" onClick={() => void loadAll()} disabled={dataStatus === "loading"}><Icon name="refresh" />Refresh</button><button className="primary-button" onClick={() => document.querySelector("#models")?.scrollIntoView({ behavior: "smooth" })} disabled={dataStatus === "error"}><Icon name="play" />New comparison</button></div>
        </header>

        <div className="content">
          {notice ? <div className="notice" role="status"><span>{notice}</span><button onClick={() => setNotice(null)} aria-label="Dismiss notification">×</button></div> : null}
          <section className="hero" id="overview"><div><span className="kicker"><i />Reliability intelligence</span><h2>Know if your agents<br /><em>actually work.</em></h2><p>Evaluation, observability, and model economics in one evidence control plane.</p></div><div className="hero-stat"><span>Recorded runs</span><strong>{data ? data.operations.runs.toLocaleString() : "—"}</strong><small>{dataStatus === "demo" ? "synthetic demo records" : dataStatus === "ready" ? "observed by the API" : "awaiting production data"}</small></div></section>

          {dataStatus === "loading" || dataStatus === "empty" || dataStatus === "error" ? <DashboardState status={dataStatus} onRetry={() => void loadAll()} /> : null}
          <DataSources integrations={integrations} status={integrationStatus} error={integrationError} checking={checking} onCheck={(integration) => void checkIntegration(integration)} onRetry={() => void loadAll()} />
          <AgentSafetyPanel apiUrl={apiUrl} demoMode={demoMode} />
          <CanaryPanel apiUrl={apiUrl} demoMode={demoMode} />
          <GoldenDatasetBrowser />
          <ProductionLearningPanel apiUrl={apiUrl} demoMode={demoMode} />
          <ExperimentWorkbench apiUrl={apiUrl} demoMode={demoMode} onExperimentComplete={loadAll} />

          {data && (dataStatus === "ready" || dataStatus === "demo") ? (
            <>
              {dataStatus === "demo" ? <div className="demo-banner"><strong>Explicit demo mode</strong><span>All metrics and integration events on this page are synthetic. Set NEXT_PUBLIC_DEMO_MODE=false to require API evidence.</span></div> : null}
              {suite ? (
                <section className={`regression-banner ${suite.regression_detected && suite.baseline_source === "observed_previous_suite" ? "failed" : "passed"}`} id="evaluation"><div className="regression-icon"><Icon name={suite.regression_detected && suite.baseline_source === "observed_previous_suite" ? "warning" : "check"} size={22} /></div><div className="regression-title"><span>Latest evaluation suite</span><strong>{suite.regression_detected && suite.baseline_source === "observed_previous_suite" ? "Observed regression" : "Evaluation complete"}</strong><code>{suite.commit_sha}</code></div><div className="suite-stat"><span>Scenarios</span><strong>{suite.scenario_count}</strong></div><div className="suite-stat"><span>Previous</span><strong>{suite.baseline_source === "observed_previous_suite" && suite.previous_success_rate !== null ? formatPercent(suite.previous_success_rate) : suite.baseline_source === "no_baseline" ? "No baseline" : "Unverified"}</strong></div><div className="suite-stat emphasis"><span>Current</span><strong>{formatPercent(suite.success_rate)}</strong></div><div className="suite-delta"><span>{suite.baseline_source === "observed_previous_suite" && suite.previous_success_rate !== null ? formatPercent(suite.success_rate - suite.previous_success_rate) : "No delta"}</span><small>success rate</small></div><button className="text-button" onClick={() => document.querySelector("#traces")?.scrollIntoView({ behavior: "smooth" })}>Inspect runs <Icon name="arrow" /></button></section>
              ) : <section className="suite-empty" id="evaluation">No completed evaluation suite is available yet.</section>}

              <div className="section-heading"><div><span className="eyebrow">Quality signals</span><h3>Observed confidence</h3></div><span className="period">Current dataset</span></div>
              <section className="metrics-grid"><MetricCard eyebrow="Task completion" value={formatPercent(data.quality.task_completion)} detail="Measured across recorded evaluations" /><MetricCard eyebrow="Answer correctness" value={formatPercent(data.quality.answer_correctness)} detail="Evaluator-scored answers" /><MetricCard eyebrow="Tool selection" value={formatPercent(data.quality.tool_selection_accuracy)} detail="Expected tool sequence match" /><MetricCard eyebrow="Hallucination rate" value={formatPercent(data.quality.hallucination_rate)} detail="Unsupported claim rate" /><MetricCard eyebrow="Eval pass rate" value={formatPercent(data.quality.eval_pass_rate)} detail="Passing persisted evaluations" /></section>

              <section className="two-col">
                <article className="panel trend-panel"><div className="panel-head"><div><span className="eyebrow">Quality over time</span><h3>Evaluation score</h3></div><div className="legend"><i />Pass rate</div></div>{data.trend.length ? <TrendChart data={data.trend} /> : <div className="panel-empty">No trend samples yet.</div>}</article>
                <article className="panel operations-panel"><div className="panel-head"><div><span className="eyebrow">Operations</span><h3>Recorded runtime</h3></div><span className={`observed-pill ${dataStatus}`}><i />{dataStatus === "demo" ? "Synthetic" : "Observed"}</span></div><div className="ops-grid"><div><Icon name="clock" /><span>p50 latency</span><strong>{formatLatency(data.operations.latency_p50_ms)}</strong><small>p95 {formatLatency(data.operations.latency_p95_ms)}</small></div><div><Icon name="coins" /><span>Cost / task</span><strong>{formatCost(data.operations.cost_per_task)}</strong><small>{formatCost(data.operations.cost_total)} total</small></div><div><Icon name="refresh" /><span>Retries</span><strong>{data.operations.retries}</strong><small>recorded retries</small></div><div><Icon name="arrow" /><span>Escalations</span><strong>{formatPercent(data.operations.escalation_rate)}</strong><small>recorded escalation rate</small></div></div><div className="token-strip"><span>Token volume</span><strong>{(data.operations.tokens / 1_000_000).toFixed(1)}M</strong><div><i style={{ width: `${Math.min(100, data.operations.tokens / 250_000)}%` }} /></div><small>{data.operations.runs.toLocaleString()} total runs</small></div></article>
              </section>
              <section className="two-col lower-grid"><article className="panel" id="coverage"><div className="panel-head"><div><span className="eyebrow">Evaluation coverage</span><h3>Confidence stack</h3></div><strong className="coverage-total">{data.evaluation.scenario_count}<small>scenarios</small></strong></div><div className="method-list">{data.evaluation.methods.map((method, index) => <div className="method-row" key={method.name}><span className={`method-icon method-${index + 1}`}><Icon name={index === 4 ? "overview" : index === 1 ? "database" : "flask"} /></span><div><strong>{method.name}</strong><span>{method.coverage} scenarios covered</span></div><span className={`method-status ${method.status}`}>{method.status}</span><strong>{method.coverage}</strong></div>)}</div></article><article className="panel failures-panel"><div className="panel-head"><div><span className="eyebrow">Failure intelligence</span><h3>Observed breakdown</h3></div><span className="bad-badge">{Object.values(data.failure_breakdown).reduce((sum, count) => sum + count, 0)} failures</span></div>{Object.keys(data.failure_breakdown).length ? <div className="failure-bars">{Object.entries(data.failure_breakdown).map(([name, count], index) => <div key={name}><span>{titleCase(name)}</span><div><i className={`failure-${index + 1}`} style={{ width: `${Math.min(100, Math.max(5, count * 4.8))}%` }} /></div><strong>{count}</strong></div>)}</div> : <div className="panel-empty">No classified failures in the current dataset.</div>}</article></section>

              <section className="panel runs-panel" id="traces"><div className="panel-head"><div><span className="eyebrow">Trace-level observability</span><h3>Representative agent runs</h3></div><span className="suite-label">Newest two from each work type</span></div><p className="runs-explainer">A balanced sample helps you compare the different kinds of work your agents handle. Select a run to see what happened step by step.</p>{data.recent_runs.length ? <div className="runs-table"><div className="run-row run-header"><span>Run / job</span><span>Work type</span><span>Model</span><span>Score</span><span>Latency</span><span>Cost</span><span>Status</span><span /></div>{data.recent_runs.map((run) => <button className="run-row" key={run.id} onClick={() => void openRun(run)}><span><code>{run.id.slice(0, 12)}</code><small>{run.task}</small></span><span className={`agent-chip ${run.agent_type}`}>{agentTypeLabel(run.agent_type)}</span><span className="run-model"><ProviderMark provider={run.provider} />{run.model}</span><span className={run.score !== null && run.score < 0.85 ? "score-bad" : "score-good"}>{run.score !== null ? formatPercent(run.score, 0) : "—"}</span><span>{formatLatency(run.latency_ms)}</span><span>{formatCost(run.cost_usd)}</span><span className={run.failure ? "run-failed" : "run-passed"}><i />{run.failure ? titleCase(run.failure) : titleCase(run.status)}</span><span><Icon name="chevron" /></span></button>)}</div> : <div className="panel-empty">No recent runs are available.</div>}</section>

              <footer><span>Baseline agent reliability platform</span><span>{dataStatus === "demo" ? "Synthetic snapshot" : `API snapshot ${formatDate(data.generated_at)}`}</span></footer>
            </>
          ) : null}
        </div>
      </main>
      {selectedRun ? <RunDrawer run={selectedRun} traceStatus={traceStatus} onClose={() => setSelectedRun(null)} /> : null}
    </div>
  );
}

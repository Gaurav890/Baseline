"use client";

import { useCallback, useEffect, useMemo, useState } from "react";

import type {
  Experiment,
  ExperimentFailure,
  ProviderAvailability,
} from "@/lib/types";
import { Icon } from "./icon";

const formatPercent = (value: number | null, digits = 1) =>
  value === null ? "—" : `${(value * 100).toFixed(digits)}%`;
const formatCost = (value: number | null) =>
  value === null ? "—" : `$${value.toFixed(value < 0.1 ? 4 : 2)}`;
const formatLatency = (value: number | null) =>
  value === null ? "—" : value >= 1000 ? `${(value / 1000).toFixed(2)}s` : `${value}ms`;
const titleCase = (value: string) =>
  value.replaceAll("_", " ").replace(/\b\w/g, (character) => character.toUpperCase());

type Props = {
  apiUrl: string;
  demoMode: boolean;
  onExperimentComplete: () => Promise<void>;
};

export function ExperimentWorkbench({ apiUrl, demoMode, onExperimentComplete }: Props) {
  const [providers, setProviders] = useState<ProviderAvailability[]>([]);
  const [selectedProviders, setSelectedProviders] = useState<Set<string>>(new Set());
  const [experiments, setExperiments] = useState<Experiment[]>([]);
  const [experiment, setExperiment] = useState<Experiment | null>(null);
  const [failures, setFailures] = useState<ExperimentFailure[]>([]);
  const [scenarioLimit, setScenarioLimit] = useState(20);
  const [name, setName] = useState("Model selection experiment");
  const [loading, setLoading] = useState(!demoMode);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const loadExperiment = useCallback(
    async (experimentId: string, signal?: AbortSignal) => {
      const [experimentResponse, failuresResponse] = await Promise.all([
        fetch(`${apiUrl}/api/experiments/${encodeURIComponent(experimentId)}`, {
          cache: "no-store",
          signal,
        }),
        fetch(`${apiUrl}/api/experiments/${encodeURIComponent(experimentId)}/failures`, {
          cache: "no-store",
          signal,
        }),
      ]);
      if (!experimentResponse.ok) throw new Error("Experiment results are unavailable.");
      const selected = (await experimentResponse.json()) as Experiment;
      setExperiment(selected);
      if (failuresResponse.ok) {
        const failurePayload = (await failuresResponse.json()) as {
          items: ExperimentFailure[];
        };
        setFailures(failurePayload.items);
      }
      return selected;
    },
    [apiUrl],
  );

  useEffect(() => {
    if (demoMode) return;
    const controller = new AbortController();
    const load = async () => {
      setLoading(true);
      try {
        const [agentsResponse, experimentsResponse] = await Promise.all([
          fetch(`${apiUrl}/api/agents`, { cache: "no-store", signal: controller.signal }),
          fetch(`${apiUrl}/api/experiments?limit=20`, {
            cache: "no-store",
            signal: controller.signal,
          }),
        ]);
        if (!agentsResponse.ok || !experimentsResponse.ok) {
          throw new Error("Comparison workspace could not be loaded.");
        }
        const agentsPayload = (await agentsResponse.json()) as {
          providers: ProviderAvailability[];
        };
        const history = (await experimentsResponse.json()) as Experiment[];
        setProviders(agentsPayload.providers);
        setExperiments(history);
        setSelectedProviders(
          new Set(
            agentsPayload.providers
              .filter((provider) => provider.configured && provider.provider !== "mock")
              .map((provider) => provider.provider),
          ),
        );
        if (history[0]) await loadExperiment(history[0].id, controller.signal);
        setError(null);
      } catch (caught) {
        if (!controller.signal.aborted) {
          setError(caught instanceof Error ? caught.message : "Comparison workspace failed.");
        }
      } finally {
        if (!controller.signal.aborted) setLoading(false);
      }
    };
    void load();
    return () => controller.abort();
  }, [apiUrl, demoMode, loadExperiment]);

  useEffect(() => {
    if (!experiment || !["queued", "running"].includes(experiment.status)) return;
    const timer = window.setTimeout(async () => {
      try {
        const updated = await loadExperiment(experiment.id);
        if (updated.status === "completed") await onExperimentComplete();
      } catch (caught) {
        setError(caught instanceof Error ? caught.message : "Could not refresh experiment.");
      }
    }, 2000);
    return () => window.clearTimeout(timer);
  }, [experiment, loadExperiment, onExperimentComplete]);

  const configuredProviders = providers.filter(
    (provider) => provider.configured && provider.provider !== "mock",
  );
  const selected = configuredProviders.filter((provider) =>
    selectedProviders.has(provider.provider),
  );
  const totalCalls = selected.length * scenarioLimit;
  const winners = useMemo(
    () => ({
      quality: experiment?.recommendation?.quality_candidate_id,
      fastest: experiment?.recommendation?.fastest_candidate_id,
      cheapest: experiment?.recommendation?.lowest_cost_candidate_id,
    }),
    [experiment],
  );

  const toggleProvider = (provider: string) => {
    setSelectedProviders((current) => {
      const next = new Set(current);
      if (next.has(provider)) next.delete(provider);
      else next.add(provider);
      return next;
    });
  };

  const submit = async () => {
    setSubmitting(true);
    setError(null);
    setFailures([]);
    try {
      const response = await fetch(`${apiUrl}/api/experiments`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "Idempotency-Key": crypto.randomUUID(),
        },
        body: JSON.stringify({
          name,
          dataset_version: "golden-v1",
          scenario_limit: scenarioLimit,
          repetitions: 1,
          candidates: selected.map((provider) => ({
            provider: provider.provider,
            model: provider.model,
            label: provider.model,
          })),
        }),
      });
      if (!response.ok) {
        const payload = (await response.json().catch(() => null)) as { detail?: string } | null;
        throw new Error(payload?.detail ?? "Experiment could not be created.");
      }
      const created = (await response.json()) as Experiment;
      setExperiment(created);
      setExperiments((current) => [created, ...current.filter((item) => item.id !== created.id)]);
      if (created.status === "completed") {
        await loadExperiment(created.id);
        await onExperimentComplete();
      }
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Experiment could not be created.");
    } finally {
      setSubmitting(false);
    }
  };

  if (demoMode) {
    return (
      <section className="panel experiment-panel" id="models">
        <span className="eyebrow">Controlled comparison</span>
        <h3>Model comparison experiments</h3>
        <div className="experiment-empty">
          Demo mode cannot claim real provider results. Disable demo mode and connect at least two
          model providers to run a comparison.
        </div>
      </section>
    );
  }

  return (
    <section className="panel experiment-panel" id="models" aria-labelledby="experiment-title">
      <div className="panel-head experiment-head">
        <div>
          <span className="eyebrow">Controlled comparison</span>
          <h3 id="experiment-title">Which model should run this agent?</h3>
        </div>
        {experiments.length ? (
          <label className="experiment-history">
            <span>Saved experiment</span>
            <select
              value={experiment?.id ?? ""}
              onChange={(event) => void loadExperiment(event.target.value)}
            >
              {experiments.map((item) => (
                <option value={item.id} key={item.id}>
                  {item.name} · {item.status}
                </option>
              ))}
            </select>
          </label>
        ) : null}
      </div>
      <p className="experiment-intro">
        Every selected model receives the same frozen test-case IDs. Quality, failures, speed, and
        cost are calculated only from that cohort—never from unrelated historical runs.
      </p>

      <div className="experiment-builder">
        <label className="experiment-name">
          <span>Experiment name</span>
          <input value={name} maxLength={160} onChange={(event) => setName(event.target.value)} />
        </label>
        <fieldset>
          <legend>1. Choose configured models</legend>
          <div className="provider-options">
            {providers
              .filter((provider) => provider.provider !== "mock")
              .map((provider) => (
                <label
                  className={`provider-option ${provider.configured ? "" : "disabled"}`}
                  key={provider.provider}
                >
                  <input
                    type="checkbox"
                    checked={selectedProviders.has(provider.provider)}
                    disabled={!provider.configured}
                    onChange={() => toggleProvider(provider.provider)}
                  />
                  <span className={`provider-logo ${provider.provider}`}>
                    {provider.provider.slice(0, 1).toUpperCase()}
                  </span>
                  <span>
                    <strong>{titleCase(provider.provider)}</strong>
                    <small>{provider.configured ? provider.model : "API key not configured"}</small>
                  </span>
                </label>
              ))}
          </div>
        </fieldset>
        <fieldset>
          <legend>2. Choose evaluation depth</legend>
          <div className="cohort-options">
            {[20, 60, 218].map((limit) => (
              <button
                className={scenarioLimit === limit ? "active" : ""}
                type="button"
                key={limit}
                onClick={() => setScenarioLimit(limit)}
              >
                <strong>{limit}</strong>
                <span>{limit === 20 ? "Quick signal" : limit === 60 ? "Balanced" : "Full gate"}</span>
              </button>
            ))}
          </div>
        </fieldset>
        <div className="experiment-submit">
          <div>
            <strong>{totalCalls.toLocaleString()} model calls</strong>
            <span>
              {scenarioLimit} identical scenarios × {selected.length} models · billed by providers
            </span>
          </div>
          <button
            className="primary-button"
            type="button"
            disabled={submitting || selected.length < 2 || name.trim().length < 3}
            onClick={() => void submit()}
          >
            <Icon name="play" />
            {submitting ? "Starting…" : "Run comparison"}
          </button>
        </div>
        {configuredProviders.length < 2 ? (
          <p className="experiment-warning">
            Configure at least two of OPENAI_API_KEY, ANTHROPIC_API_KEY, and GEMINI_API_KEY in the
            API environment, then restart the API.
          </p>
        ) : null}
        {error ? <p className="experiment-error" role="alert">{error}</p> : null}
      </div>

      {loading ? <div className="experiment-empty">Loading saved comparisons…</div> : null}
      {!loading && !experiment ? (
        <div className="experiment-empty">
          No comparison has run yet. Choose at least two configured providers above.
        </div>
      ) : null}
      {experiment ? (
        <div className="experiment-results">
          <div className="experiment-result-head">
            <div>
              <span className={`experiment-status ${experiment.status}`}>{experiment.status}</span>
              <strong>{experiment.name}</strong>
              <small>
                {experiment.scenario_count} exact scenarios · {experiment.dataset_version} · {experiment.id.slice(0, 8)}
              </small>
            </div>
            <span className="baseline-label">
              {experiment.baseline_experiment_id
                ? "Compared with the previous matching experiment"
                : "First run · no baseline yet"}
            </span>
          </div>
          {experiment.recommendation?.summary ? (
            <div className="recommendation-callout">
              <Icon name="check" />
              <span><strong>Measured result</strong>{experiment.recommendation.summary}</span>
            </div>
          ) : null}
          <div className="experiment-table" role="table" aria-label="Experiment candidates">
            <div className="experiment-row experiment-table-head" role="row">
              <span>Candidate</span><span>Quality</span><span>Pass rate</span><span>p50 / p95</span><span>Cost / task</span><span>Previous delta</span><span>Failures</span>
            </div>
            {experiment.candidates.map((candidate) => {
              const failureCount = Object.values(candidate.failure_breakdown).reduce(
                (sum, count) => sum + count,
                0,
              );
              return (
                <div className="experiment-row" role="row" key={candidate.id}>
                  <span className="experiment-candidate">
                    <span className={`provider-logo ${candidate.provider}`}>
                      {candidate.provider.slice(0, 1).toUpperCase()}
                    </span>
                    <span><strong>{candidate.label}</strong><small>{titleCase(candidate.provider)} · {candidate.run_count}/{experiment.scenario_count * experiment.repetitions} runs</small></span>
                  </span>
                  <span><strong>{formatPercent(candidate.quality)}</strong>{winners.quality === candidate.id ? <em>Best quality</em> : null}</span>
                  <span>{formatPercent(candidate.pass_rate)}</span>
                  <span>{formatLatency(candidate.latency_p50_ms)} / {formatLatency(candidate.latency_p95_ms)}{winners.fastest === candidate.id ? <em>Fastest</em> : null}</span>
                  <span>{formatCost(candidate.cost_per_task)}{winners.cheapest === candidate.id ? <em>Lowest cost</em> : null}</span>
                  <span className={candidate.quality_delta === null ? "" : candidate.quality_delta >= 0 ? "positive" : "negative"}>
                    {candidate.quality_delta === null
                      ? "No baseline"
                      : `${candidate.quality_delta >= 0 ? "+" : ""}${formatPercent(candidate.quality_delta)}`}
                  </span>
                  <span className={failureCount ? "negative" : "positive"}>{failureCount}</span>
                </div>
              );
            })}
          </div>

          <div className="failure-comparison">
            <div className="failure-comparison-head">
              <div><span className="eyebrow">Failure diff</span><h4>Where candidates break</h4></div>
              <strong>{failures.length} failed runs</strong>
            </div>
            {failures.length ? (
              <div className="experiment-failures">
                {failures.map((failure) => (
                  <details key={failure.run_id}>
                    <summary>
                      <code>{failure.scenario_id}</code>
                      <span>{failure.candidate_label}</span>
                      <em>{titleCase(failure.failure_classification ?? "failed")}</em>
                      <strong>{formatPercent(failure.score, 0)}</strong>
                    </summary>
                    <div>
                      <p>{failure.task}</p>
                      <span>{failure.rationale}</span>
                      <pre>{failure.output || "No model output was returned."}</pre>
                    </div>
                  </details>
                ))}
              </div>
            ) : (
              <div className="experiment-empty">
                {experiment.status === "completed"
                  ? "No failed runs in this experiment."
                  : "Failures will appear as the experiment completes."}
              </div>
            )}
          </div>
        </div>
      ) : null}
    </section>
  );
}

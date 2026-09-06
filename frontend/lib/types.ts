export type ModelComparison = {
  provider: string;
  model: string;
  quality: number;
  task_completion: number;
  latency_ms: number;
  cost_per_task: number;
  runs: number;
};

export type RecentRun = {
  id: string;
  task: string;
  agent_type: string;
  provider: string;
  model: string;
  status: string;
  score: number | null;
  failure: string | null;
  latency_ms: number;
  cost_usd: number;
  created_at: string;
};

export type TraceSpan = {
  id: string;
  parent_id: string | null;
  kind: string;
  name: string;
  status: string;
  duration_ms: number;
  input: Record<string, unknown>;
  output: Record<string, unknown>;
  attributes: Record<string, unknown>;
  started_at: string;
};

export type RunDetail = RecentRun & {
  spans: TraceSpan[];
  evaluations: Array<{
    score: number;
    passed: boolean;
    failure_classification: string | null;
    rubric: Record<string, unknown>;
    rationale: string;
  }>;
};

export type DashboardData = {
  generated_at: string;
  quality: {
    task_completion: number;
    answer_correctness: number;
    tool_selection_accuracy: number;
    hallucination_rate: number;
    failure_rate: number;
    eval_pass_rate: number;
  };
  operations: {
    runs: number;
    latency_p50_ms: number;
    latency_p95_ms: number;
    tokens: number;
    cost_total: number;
    cost_per_task: number;
    retries: number;
    escalation_rate: number;
  };
  model_comparison: ModelComparison[];
  trend: Array<{ date: string; quality: number; latency_ms: number; runs: number }>;
  failure_breakdown: Record<string, number>;
  evaluation: {
    scenario_count: number;
    methods: Array<{ name: string; status: string; coverage: number }>;
  };
  latest_suite: {
    id: string;
    commit_sha: string;
    scenario_count: number;
    success_rate: number;
    previous_success_rate: number;
    regression_detected: boolean;
    failure_breakdown: Record<string, number>;
    total_cost_usd: number;
    p95_latency_ms: number;
    created_at: string;
  } | null;
  recent_runs: RecentRun[];
};

export type IntegrationHealth =
  | "unconfigured"
  | "unchecked"
  | "healthy"
  | "degraded"
  | "unhealthy"
  | "unknown";

export type Provenance = "LIVE" | "SNAPSHOT" | "SYNTHETIC";

export type Integration = {
  id: string;
  name: string;
  display_name: string;
  configured: boolean;
  health_status: IntegrationHealth;
  provenance: Provenance | null;
  live: boolean;
  last_checked_at: string | null;
  last_success_at: string | null;
  last_error: string | null;
  latency_ms?: number | null;
  event_count: number;
  metadata: Record<string, unknown>;
};

export type IntegrationsResponse = {
  items: Integration[];
  generated_at: string;
};

export type EvaluationJob = {
  id: string;
  kind: "regression" | "benchmark";
  status: "queued" | "running" | "completed" | "failed";
  request: Record<string, unknown>;
  result: Record<string, unknown> | null;
  error: string | null;
  attempts: number;
  queue_backend: string;
  created_at: string;
  started_at: string | null;
  completed_at: string | null;
};

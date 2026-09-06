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


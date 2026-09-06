from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

AgentType = Literal["research", "document", "support", "extraction"]
ProviderType = Literal["mock", "openai", "anthropic", "gemini"]


class RunCreate(BaseModel):
    task: str = Field(min_length=3, max_length=20_000)
    agent_type: AgentType
    provider: ProviderType = "mock"
    model: str | None = None
    context: dict[str, Any] = Field(default_factory=dict)
    scenario_id: str | None = None


class SpanRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    parent_id: str | None
    kind: str
    name: str
    status: str
    duration_ms: int
    input: dict[str, Any]
    output: dict[str, Any]
    attributes: dict[str, Any]
    started_at: datetime


class EvaluationRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    evaluator_type: str
    score: float
    passed: bool
    task_completion: float
    answer_correctness: float
    tool_selection_accuracy: float
    hallucination_rate: float
    failure_classification: str | None
    rubric: dict[str, Any]
    rationale: str


class RunRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    task: str
    agent_type: str
    provider: str
    model: str
    status: str
    planner_output: dict[str, Any]
    final_output: str
    scenario_id: str | None
    latency_ms: int
    input_tokens: int
    output_tokens: int
    cost_usd: float
    retries: int
    escalated: bool
    error: str | None
    created_at: datetime
    completed_at: datetime | None
    spans: list[SpanRead] = Field(default_factory=list)
    evaluations: list[EvaluationRead] = Field(default_factory=list)


class HumanReviewCreate(BaseModel):
    reviewer: str = Field(min_length=1, max_length=120)
    verdict: Literal["pass", "fail", "needs_review"]
    score: float = Field(ge=0, le=1)
    notes: str = Field(default="", max_length=5000)


class RegressionRequest(BaseModel):
    provider: ProviderType = "mock"
    model: str | None = None
    baseline_success_rate: float = Field(default=0.912, ge=0, le=1)
    threshold: float = Field(default=0.01, ge=0, le=0.25)
    limit: int = Field(default=218, ge=1, le=218)


class BenchmarkRequest(BaseModel):
    scenario_limit: int = Field(default=12, ge=1, le=218)
    providers: list[Literal["openai", "anthropic", "gemini", "mock"]] = Field(
        default_factory=lambda: ["openai", "anthropic", "gemini"]
    )


class JudgeRequest(BaseModel):
    provider: ProviderType = "mock"
    model: str | None = None

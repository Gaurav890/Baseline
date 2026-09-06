from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class AgentProfile:
    id: str
    name: str
    description: str
    instructions: str
    tools: tuple[str, ...]
    accent: str


AGENT_PROFILES: dict[str, AgentProfile] = {
    "research": AgentProfile(
        id="research",
        name="Research Analyst",
        description="Builds evidence-backed briefs with explicit source grounding.",
        instructions=(
            "Answer the research request using only supplied evidence. Distinguish facts from "
            "inference, include source references, and state when evidence is insufficient."
        ),
        tools=("web_search", "document_search"),
        accent="#ff6b35",
    ),
    "document": AgentProfile(
        id="document",
        name="Document Examiner",
        description="Finds clauses, summarizes documents, and checks claims against source text.",
        instructions=(
            "Analyze the provided document precisely. Quote sparingly and point to supporting "
            "passages, "
            "and never invent a clause or obligation."
        ),
        tools=("document_search", "extract_fields"),
        accent="#17b890",
    ),
    "support": AgentProfile(
        id="support",
        name="Support Resolver",
        description="Diagnoses support issues and executes policy-aware, multi-tool resolutions.",
        instructions=(
            "Resolve the issue with the least risky valid action. Use ticket history and approved "
            "knowledge, run policy checks, and escalate when authority or evidence is insufficient."
        ),
        tools=("ticket_lookup", "knowledge_base_search", "policy_check"),
        accent="#7c5cff",
    ),
    "extraction": AgentProfile(
        id="extraction",
        name="Data Extractor",
        description="Converts unstructured records into validated structured data.",
        instructions=(
            "Return concise JSON-compatible data. Preserve source values, mark missing fields, and "
            "validate the result before completing the task."
        ),
        tools=("extract_fields", "validate_schema"),
        accent="#f5b700",
    ),
}


def choose_tools(agent_type: str, task: str) -> list[str]:
    """A reviewable deterministic planner used before model execution."""
    lower = task.lower()
    if agent_type == "research":
        tools = ["web_search"]
        if any(word in lower for word in ("document", "report", "filing", "memo")):
            tools.append("document_search")
        return tools
    if agent_type == "document":
        tools = ["document_search"]
        if any(word in lower for word in ("extract", "field", "date", "amount", "email")):
            tools.append("extract_fields")
        return tools
    if agent_type == "support":
        tools = ["ticket_lookup", "knowledge_base_search"]
        if any(word in lower for word in ("refund", "credit", "delete", "escalate", "outage")):
            tools.append("policy_check")
        return tools
    if agent_type == "extraction":
        return ["extract_fields", "validate_schema"]
    raise ValueError(f"Unknown agent type: {agent_type}")

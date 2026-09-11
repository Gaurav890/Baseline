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
    complexity: str = "focused"
    business_value: str = ""
    example_task: str = ""
    workflow: tuple[tuple[str, str], ...] = ()
    output_fields: tuple[str, ...] = ()


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
        business_value="Turns a source packet into a grounded research brief.",
        example_task="Summarize a filing and cite the evidence used.",
        workflow=(
            ("Collect", "Retrieve approved evidence."),
            ("Synthesize", "Write a grounded brief."),
        ),
        output_fields=("answer", "confidence", "sources"),
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
        business_value="Finds contract facts without inventing obligations.",
        example_task="Find a renewal clause and extract its effective date.",
        workflow=(
            ("Find", "Locate relevant passages."),
            ("Extract", "Return the requested facts."),
        ),
        output_fields=("clause", "summary", "effective_date"),
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
        business_value="Resolves support work within policy and approval limits.",
        example_task="Diagnose a customer outage and decide whether a credit needs approval.",
        workflow=(
            ("Investigate", "Read the case and knowledge base."),
            ("Authorize", "Apply the action policy."),
        ),
        output_fields=("ticket_id", "resolution", "escalate"),
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
        business_value="Creates validated records from unstructured business text.",
        example_task="Extract the owner, renewal date, and contract value.",
        workflow=(("Extract", "Parse typed fields."), ("Validate", "Check the record contract.")),
        output_fields=("emails", "dates", "amounts"),
    ),
    "due_diligence": AgentProfile(
        id="due_diligence",
        name="Company Due Diligence Analyst",
        description=(
            "Combines regulatory filings and public-web evidence into an auditable risk decision."
        ),
        instructions=(
            "Act as a company due-diligence analyst. Reconcile SEC facts with approved public-web "
            "evidence, separate observed facts from risk inference, and return the requested JSON "
            "decision. Never treat missing evidence as evidence of safety."
        ),
        tools=("sec_company_lookup", "web_intelligence", "risk_assessment", "policy_check"),
        accent="#246bce",
        complexity="multi-source workflow",
        business_value=(
            "Speeds vendor or investment screening while preserving evidence and approvals."
        ),
        example_task=(
            "Assess a public company before procurement: verify its SEC identity, inspect current "
            "public signals, score risk, and decide whether a human must approve."
        ),
        workflow=(
            ("Verify entity", "Retrieve the issuer record from SEC EDGAR."),
            ("Collect signals", "Search approved public-web evidence."),
            ("Assess risk", "Apply a deterministic risk rubric to the evidence."),
            ("Gate decision", "Require approval when authority thresholds are exceeded."),
        ),
        output_fields=(
            "company",
            "decision",
            "risk_level",
            "key_findings",
            "evidence",
            "approval_required",
        ),
    ),
    "release_risk": AgentProfile(
        id="release_risk",
        name="Release Risk Investigator",
        description=(
            "Triages repository issues and external signals before a production release decision."
        ),
        instructions=(
            "Act as a release-risk investigator. Use only retrieved GitHub and approved web "
            "evidence, identify blocking issues, explain uncertainty, and recommend go, hold, or "
            "human review. Do not change repository state."
        ),
        tools=("github_issue_lookup", "web_intelligence", "risk_assessment", "policy_check"),
        accent="#b64b77",
        complexity="multi-source workflow",
        business_value="Makes release readiness explainable before CI reports a final check.",
        example_task=(
            "Review a repository's issue evidence and public security signals, then produce an "
            "auditable go/hold recommendation."
        ),
        workflow=(
            ("Inspect repository", "Retrieve bounded GitHub issue evidence."),
            ("Check external signals", "Search approved web sources for relevant risk."),
            ("Classify blockers", "Score severity, evidence coverage, and uncertainty."),
            ("Gate release", "Escalate high-risk or costly action to a person."),
        ),
        output_fields=(
            "repository",
            "decision",
            "risk_level",
            "blocking_issues",
            "evidence",
            "approval_required",
        ),
    ),
    "incident_response": AgentProfile(
        id="incident_response",
        name="Incident Response Coordinator",
        description=(
            "Correlates incident context, repository evidence, and policy into a contained "
            "response plan."
        ),
        instructions=(
            "Act as an incident-response coordinator. Correlate the supplied incident record with "
            "retrieved repository and public evidence, prioritize containment, and produce a "
            "bounded response plan. Never claim an external action was executed."
        ),
        tools=("incident_context", "github_issue_lookup", "risk_assessment", "policy_check"),
        accent="#d3652f",
        complexity="cross-system workflow",
        business_value="Turns fragmented incident signals into a reviewable containment decision.",
        example_task=(
            "Triage an API degradation, connect it to repository evidence, rank containment steps, "
            "and identify actions requiring human approval."
        ),
        workflow=(
            ("Establish scope", "Load the supplied operational incident record."),
            ("Correlate evidence", "Retrieve related GitHub issue evidence."),
            ("Prioritize response", "Score impact and propose containment steps."),
            ("Control authority", "Record which actions require a human decision."),
        ),
        output_fields=(
            "incident_id",
            "severity",
            "status",
            "containment_plan",
            "evidence",
            "approval_required",
        ),
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
    if agent_type == "due_diligence":
        return ["sec_company_lookup", "web_intelligence", "risk_assessment", "policy_check"]
    if agent_type == "release_risk":
        return ["github_issue_lookup", "web_intelligence", "risk_assessment", "policy_check"]
    if agent_type == "incident_response":
        return ["incident_context", "github_issue_lookup", "risk_assessment", "policy_check"]
    raise ValueError(f"Unknown agent type: {agent_type}")

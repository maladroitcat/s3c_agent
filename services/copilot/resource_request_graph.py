from typing import Any, Callable, Literal, TypedDict

try:
    from langgraph.graph import END, START, StateGraph
except Exception:
    END = "__end__"
    START = "__start__"
    StateGraph = None


ResourceAction = Literal["ask_developer", "update_draft", "finalize_for_review", "reject", "answer_question"]
ALLOWED_ACTIONS = {"ask_developer", "update_draft", "finalize_for_review", "reject", "answer_question"}


class ResourceRequestState(TypedDict, total=False):
    job_id: str
    session_id: str
    request_id: str
    user: dict[str, Any]
    entitlements: list[str]
    messages: list[dict[str, str]]
    latest_input: str
    redacted_input: str
    dlp_findings_redacted: int
    extraction_backend: str
    extraction_error: str
    fields: dict[str, Any]
    retrieved_policy_chunks: list[dict[str, Any]]
    missing_fields: list[str]
    policy_violations: list[dict[str, Any]]
    validation_backend: str
    validation_error: str
    validation_signature: str
    validated_fields: dict[str, Any]
    risk_level: str
    draft: dict[str, Any]
    next_action: ResourceAction
    assistant_message: str
    action_reason: str
    proposed_question: str
    entitlement_violation: str
    agent_trajectory: list[dict[str, Any]]
    iterations: int
    terminated_by: str | None


class ResourceRequestSkill:
    def __init__(self, nodes: dict[str, Callable[[ResourceRequestState], ResourceRequestState]]) -> None:
        self.nodes = nodes
        self.graph = self._compile_graph()

    @property
    def backend(self) -> str:
        return "langgraph" if self.graph is not None else "sequential-fallback"

    def invoke(self, state: ResourceRequestState) -> ResourceRequestState:
        if self.graph is None:
            return self._invoke_sequential(state)
        return self.graph.invoke(state)

    def _compile_graph(self) -> Any | None:
        if StateGraph is None:
            return None
        graph = StateGraph(ResourceRequestState)
        for name in [
            "redact_input",
            "extract_fields",
            "retrieve_policies",
            "assess_request",
            "decide_action",
            "execute_action",
        ]:
            graph.add_node(name, self.nodes[name])
        graph.add_edge(START, "redact_input")
        graph.add_edge("redact_input", "extract_fields")
        graph.add_edge("extract_fields", "retrieve_policies")
        graph.add_edge("retrieve_policies", "assess_request")
        graph.add_edge("assess_request", "decide_action")
        graph.add_edge("decide_action", "execute_action")
        graph.add_edge("execute_action", END)
        return graph.compile()

    def _invoke_sequential(self, state: ResourceRequestState) -> ResourceRequestState:
        for name in [
            "redact_input",
            "extract_fields",
            "retrieve_policies",
            "assess_request",
            "decide_action",
            "execute_action",
        ]:
            state = self.nodes[name](state)
        return state


def normalize_action(action: str | None, missing_fields: list[str], iterations: int, max_iterations: int) -> ResourceAction:
    if action in ALLOWED_ACTIONS:
        return action  # type: ignore[return-value]
    if iterations >= max_iterations:
        return "finalize_for_review"
    if missing_fields:
        return "ask_developer"
    return "update_draft"

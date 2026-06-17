from __future__ import annotations

import json
import math
import os
import re
import threading
import time
import uuid
from dataclasses import dataclass
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import quote, urlparse
from urllib.request import Request, urlopen

from resource_request_graph import ResourceRequestSkill, ResourceRequestState, normalize_action

try:
    from langchain_core.documents import Document
    from langchain_core.vectorstores import InMemoryVectorStore
    from langchain_google_vertexai import VertexAIEmbeddings
except Exception:
    Document = None
    InMemoryVectorStore = None
    VertexAIEmbeddings = None

PROJECT_ID = os.environ.get("PROJECT_ID", "s3c-agent")
REGION = os.environ.get("REGION", "us-central1")
BUCKET = os.environ.get("POLICY_BUCKET", "s3c-agent-policy")
MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")
EMBEDDING_MODEL = os.environ.get("EMBEDDING_MODEL", "text-embedding-005")
MAX_ITERATIONS = int(os.environ.get("MAX_ITERATIONS", "5"))
LOG_NAME = "s3c-copilot-agent"

MOCK_USERS = {
    "healthcare-developer": {
        "name": "Healthcare Developer",
        "email": "developer@s3c.example",
        "entitlements": ["healthcare-client-a"],
    },
    "finance-developer": {
        "name": "Finance Developer",
        "email": "developer2@s3c.example",
        "entitlements": ["finance-client-b"],
    },
    "platform-architect": {
        "name": "Platform Architect",
        "email": "architect@s3c.example",
        "entitlements": ["healthcare-client-a", "finance-client-b"],
    },
}

REQUIRED_RESOURCE_FIELDS = [
    "application",
    "environment",
    "client_id",
    "resource_category",
    "intended_use",
    "owner",
    "cost_center",
]

PRODUCTION_FIELDS = [
    "data_classification",
    "regulated_data_confirmation",
    "availability_slo",
    "backup_retention",
    "region",
    "preferred_engine",
]

CHAT_SESSIONS: dict[str, dict[str, Any]] = {}
RESOURCE_SESSIONS: dict[str, ResourceRequestState] = {}
RESOURCE_SKILL: ResourceRequestSkill | None = None
CHAT_JOBS: dict[str, dict[str, Any]] = {}
RESOURCE_JOBS: dict[str, dict[str, Any]] = {}


@dataclass(frozen=True)
class PolicyChunk:
    source: str
    heading: str
    chunk: int
    text: str
    client_id: str
    visibility: str
    policy_version: str
    tokens: frozenset[str]

    @property
    def citation(self) -> str:
        return f"{self.source}#{self.chunk}"


class KnowledgeBase:
    def __init__(self) -> None:
        self.loaded_at = 0.0
        self.chunks: list[PolicyChunk] = []
        self.vector_store: Any | None = None
        self.vector_ready = False
        self.vector_error = ""

    def ensure_loaded(self) -> None:
        if self.chunks and time.time() - self.loaded_at < 300:
            return

        chunks: list[PolicyChunk] = []
        for name in gcs_list(BUCKET):
            if not name.endswith((".md", ".yaml", ".yml", ".json")):
                continue
            text = gcs_read(BUCKET, name)
            client_id, visibility = scope_from_path(name)
            version = metadata_value(text, "Policy version") or "sample/template"
            for number, heading, body in chunk_document(text):
                chunks.append(
                    PolicyChunk(
                        source=name,
                        heading=heading,
                        chunk=number,
                        text=body,
                        client_id=client_id,
                        visibility=visibility,
                        policy_version=version,
                        tokens=frozenset(tokens(f"{name} {heading} {body}")),
                    )
                )
        self.chunks = chunks
        self._build_vector_store(chunks)
        self.loaded_at = time.time()

    def retrieve(self, query: str, entitlements: list[str], limit: int = 8) -> list[dict[str, Any]]:
        self.ensure_loaded()
        if self.vector_ready and self.vector_store is not None:
            results = self._retrieve_vector(query, entitlements, limit)
            if results:
                return results
        return self._retrieve_keyword(query, entitlements, limit)

    def _build_vector_store(self, chunks: list[PolicyChunk]) -> None:
        self.vector_store = None
        self.vector_ready = False
        self.vector_error = ""
        if Document is None or InMemoryVectorStore is None or VertexAIEmbeddings is None:
            self.vector_error = "langchain dependencies unavailable"
            return
        if not chunks:
            self.vector_error = "no chunks loaded"
            return
        try:
            embeddings = VertexAIEmbeddings(model_name=EMBEDDING_MODEL, project=PROJECT_ID, location=REGION)
            documents = [
                Document(
                    page_content=f"{chunk.heading}\n\n{chunk.text}",
                    metadata={
                        "doc": chunk.source,
                        "heading": chunk.heading,
                        "chunk": chunk.chunk,
                        "citation": chunk.citation,
                        "client_id": chunk.client_id,
                        "visibility": chunk.visibility,
                        "policy_version": chunk.policy_version,
                    },
                )
                for chunk in chunks
            ]
            self.vector_store = InMemoryVectorStore.from_documents(documents, embedding=embeddings)
            self.vector_ready = True
        except Exception as exc:
            self.vector_error = str(exc)[:240]

    def _retrieve_vector(self, query: str, entitlements: list[str], limit: int) -> list[dict[str, Any]]:
        if self.vector_store is None:
            return []
        try:
            matches = self.vector_store.similarity_search_with_score(query, k=max(limit * 6, 24))
        except Exception as exc:
            self.vector_error = str(exc)[:240]
            self.vector_ready = False
            return []
        filtered: list[dict[str, Any]] = []
        for document, score in matches:
            metadata = document.metadata
            if metadata.get("visibility") != "shared" and metadata.get("client_id") not in entitlements:
                continue
            filtered.append(
                {
                    "doc": metadata.get("doc", ""),
                    "heading": metadata.get("heading", ""),
                    "chunk": metadata.get("chunk", 0),
                    "citation": metadata.get("citation", ""),
                    "client_id": metadata.get("client_id", ""),
                    "visibility": metadata.get("visibility", ""),
                    "policy_version": metadata.get("policy_version", ""),
                    "score": round(float(score), 3),
                    "retrieval_backend": "langchain-inmemory-vectorstore",
                    "embedding_model": EMBEDDING_MODEL,
                    "text": document.page_content[:1100],
                }
            )
            if len(filtered) >= limit:
                break
        return filtered

    def _retrieve_keyword(self, query: str, entitlements: list[str], limit: int) -> list[dict[str, Any]]:
        query_tokens = set(tokens(query))
        scored: list[tuple[float, PolicyChunk]] = []
        for chunk in self.chunks:
            if chunk.visibility != "shared" and chunk.client_id not in entitlements:
                continue
            overlap = query_tokens & chunk.tokens
            if not overlap:
                continue
            score = len(overlap) / math.sqrt(max(len(chunk.tokens), 1))
            if "database-standards" in chunk.source and {"database", "data", "store"} & query_tokens:
                score += 0.7
            if "approved-resource-patterns" in chunk.source:
                score += 0.5
            if "resource-request-standard" in chunk.source:
                score += 0.4
            if "agent-draft-handling-policy" in chunk.source:
                score += 0.3
            scored.append((score, chunk))
        scored.sort(key=lambda item: item[0], reverse=True)
        return [
            {
                "doc": chunk.source,
                "heading": chunk.heading,
                "chunk": chunk.chunk,
                "citation": chunk.citation,
                "client_id": chunk.client_id,
                "visibility": chunk.visibility,
                "policy_version": chunk.policy_version,
                "score": round(score, 3),
                "retrieval_backend": "keyword-fallback",
                "text": chunk.text[:1100],
            }
            for score, chunk in scored[:limit]
        ]


KB = KnowledgeBase()


class Handler(BaseHTTPRequestHandler):
    server_version = "SuperCoolCopilot/2.0"

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        if path == "/":
            self.send_html(INDEX_HTML)
        elif path == "/supercoollogo.png":
            self.send_png("supercoollogo.png")
        elif path.startswith("/api/resource-request/jobs/"):
            job_id = path.rsplit("/", 1)[-1]
            job = RESOURCE_JOBS.get(job_id)
            if not job:
                self.send_error(HTTPStatus.NOT_FOUND)
                return
            self.send_json(job)
        elif path.startswith("/api/chat/jobs/"):
            job_id = path.rsplit("/", 1)[-1]
            job = CHAT_JOBS.get(job_id)
            if not job:
                self.send_error(HTTPStatus.NOT_FOUND)
                return
            self.send_json(job)
        elif path == "/healthz":
            KB.ensure_loaded()
            self.send_json(
                {
                    "status": "ok",
                    "policy_chunks": len(KB.chunks),
                    "bucket": BUCKET,
                    "retrieval_backend": "langchain-inmemory-vectorstore" if KB.vector_ready else "keyword-fallback",
                    "embedding_model": EMBEDDING_MODEL,
                    "vector_error": KB.vector_error,
                    "log_name": LOG_NAME,
                }
            )
        else:
            self.send_error(HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:
        started = time.time()
        path = urlparse(self.path).path
        payload = self.read_json()
        try:
            if path == "/api/chat":
                response = chat_turn(payload, started)
            elif path == "/api/chat-async":
                response = start_chat_job(payload)
            elif path in {"/api/resource-request/start-async", "/api/ticket/start-async"}:
                response = start_resource_job(payload, new_session=True)
            elif path in {"/api/resource-request/reply-async", "/api/ticket/reply-async"}:
                response = start_resource_job(payload, new_session=False)
            elif path in {"/api/resource-request/start", "/api/ticket/start"}:
                response = resource_request_turn(payload, started, new_session=True)
            elif path in {"/api/resource-request/reply", "/api/ticket/reply"}:
                response = resource_request_turn(payload, started, new_session=False)
            elif path == "/api/isolation-test":
                response = isolation_test(payload, started)
            else:
                self.send_error(HTTPStatus.NOT_FOUND)
                return
            self.send_json(response)
        except Exception as exc:
            self.send_json({"error": str(exc)}, HTTPStatus.INTERNAL_SERVER_ERROR)

    def read_json(self) -> dict[str, Any]:
        length = int(self.headers.get("content-length", "0"))
        return json.loads(self.rfile.read(length)) if length else {}

    def send_json(self, payload: Any, status: HTTPStatus = HTTPStatus.OK) -> None:
        body = json.dumps(payload, indent=2).encode()
        self.send_response(status)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def send_html(self, body: str) -> None:
        encoded = body.encode()
        self.send_response(HTTPStatus.OK)
        self.send_header("content-type", "text/html; charset=utf-8")
        self.send_header("content-length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def send_png(self, path: str) -> None:
        with open(path, "rb") as image:
            body = image.read()
        self.send_response(HTTPStatus.OK)
        self.send_header("content-type", "image/png")
        self.send_header("cache-control", "public, max-age=3600")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt: str, *args: object) -> None:
        return


def start_resource_job(payload: dict[str, Any], new_session: bool) -> dict[str, Any]:
    cleanup_resource_jobs()
    job_id = f"job-{uuid.uuid4().hex[:8]}"
    RESOURCE_JOBS[job_id] = {
        "job_id": job_id,
        "status": "running",
        "phase": "Queued...",
        "response": None,
        "error": None,
        "started_at": int(time.time()),
    }
    job_payload = dict(payload)
    job_payload["job_id"] = job_id
    thread = threading.Thread(target=run_resource_job, args=(job_id, job_payload, new_session), daemon=True)
    thread.start()
    return {"job_id": job_id, "status": "running", "phase": "Queued..."}


def run_resource_job(job_id: str, payload: dict[str, Any], new_session: bool) -> None:
    started = time.time()
    try:
        set_job_phase(job_id, "Starting request...")
        response = resource_request_turn(payload, started, new_session)
        RESOURCE_JOBS[job_id].update(
            {
                "status": "done",
                "phase": "Done",
                "response": response,
                "completed_at": int(time.time()),
            }
        )
    except Exception as exc:
        RESOURCE_JOBS[job_id].update(
            {
                "status": "error",
                "phase": "Error",
                "error": str(exc),
                "completed_at": int(time.time()),
            }
        )


def set_job_phase(job_id: str | None, phase: str) -> None:
    if job_id and job_id in RESOURCE_JOBS:
        RESOURCE_JOBS[job_id]["phase"] = phase
        RESOURCE_JOBS[job_id]["updated_at"] = int(time.time())


def cleanup_resource_jobs(max_age_seconds: int = 900) -> None:
    cutoff = int(time.time()) - max_age_seconds
    for job_id, job in list(RESOURCE_JOBS.items()):
        if int(job.get("started_at", 0)) < cutoff:
            RESOURCE_JOBS.pop(job_id, None)


def start_chat_job(payload: dict[str, Any]) -> dict[str, Any]:
    cleanup_chat_jobs()
    job_id = f"chat-job-{uuid.uuid4().hex[:8]}"
    CHAT_JOBS[job_id] = {
        "job_id": job_id,
        "status": "running",
        "phase": "Queued...",
        "response": None,
        "error": None,
        "started_at": int(time.time()),
    }
    job_payload = dict(payload)
    job_payload["job_id"] = job_id
    thread = threading.Thread(target=run_chat_job, args=(job_id, job_payload), daemon=True)
    thread.start()
    return {"job_id": job_id, "status": "running", "phase": "Queued..."}


def run_chat_job(job_id: str, payload: dict[str, Any]) -> None:
    started = time.time()
    try:
        set_chat_job_phase(job_id, "Starting chat...")
        response = chat_turn(payload, started)
        CHAT_JOBS[job_id].update(
            {
                "status": "done",
                "phase": "Done",
                "response": response,
                "completed_at": int(time.time()),
            }
        )
    except Exception as exc:
        CHAT_JOBS[job_id].update(
            {
                "status": "error",
                "phase": "Error",
                "error": str(exc),
                "completed_at": int(time.time()),
            }
        )


def set_chat_job_phase(job_id: str | None, phase: str) -> None:
    if job_id and job_id in CHAT_JOBS:
        CHAT_JOBS[job_id]["phase"] = phase
        CHAT_JOBS[job_id]["updated_at"] = int(time.time())


def cleanup_chat_jobs(max_age_seconds: int = 900) -> None:
    cutoff = int(time.time()) - max_age_seconds
    for job_id, job in list(CHAT_JOBS.items()):
        if int(job.get("started_at", 0)) < cutoff:
            CHAT_JOBS.pop(job_id, None)


def chat_turn(payload: dict[str, Any], started: float) -> dict[str, Any]:
    user = user_context(payload.get("user", "healthcare-developer"))
    session_id = payload.get("session_id") or f"chat-{uuid.uuid4().hex[:8]}"
    request_id = payload.get("request_id") or f"rq-{uuid.uuid4().hex[:8]}"
    message = payload.get("message", "").strip()
    job_id = payload.get("job_id", "")
    set_chat_job_phase(job_id, "Redacting sensitive data...")
    redacted, findings = redact(message)

    session = CHAT_SESSIONS.setdefault(session_id, {"messages": []})
    if redacted:
        session["messages"].append({"role": "user", "content": redacted})

    set_chat_job_phase(job_id, "Retrieving policy docs...")
    retrieved = KB.retrieve(build_chat_query(redacted, user), user["entitlements"], limit=7)
    set_chat_job_phase(job_id, "Drafting answer...")
    answer = generate_chat_answer(redacted, session["messages"], retrieved)
    session["messages"].append({"role": "assistant", "content": answer})

    response = {
        "session_id": session_id,
        "request_id": request_id,
        "answer": answer,
        "citations": citations(retrieved),
        "dlp_findings_redacted": len(findings),
        "logs_query": logs_query(request_id),
    }
    set_chat_job_phase(job_id, "Writing audit log...")
    write_agent_log(
        {
            "event_type": "agent_action",
            "tab": "chat",
            "action": "answer_question",
            "request_id": request_id,
            "session_id": session_id,
            "user": user["email"],
            "entitlements": user["entitlements"],
            "retrieval_backend": retrieval_backend(retrieved),
            "embedding_model": EMBEDDING_MODEL if retrieval_backend(retrieved) == "langchain-inmemory-vectorstore" else None,
            "vector_error": KB.vector_error if retrieval_backend(retrieved) == "keyword-fallback" else "",
            "retrieved_policy_chunks": compact_chunks(retrieved),
            "cited_policy_chunks": [item["citation"] for item in citations(retrieved)],
            "dlp_findings_redacted": len(findings),
            "model": MODEL,
            "latency_ms": elapsed_ms(started),
        }
    )
    return response


def resource_request_turn(payload: dict[str, Any], started: float, new_session: bool) -> dict[str, Any]:
    user = user_context(payload.get("user", "healthcare-developer"))
    session_id = payload.get("session_id") if not new_session else None
    if not session_id:
        session_id = f"resource-{uuid.uuid4().hex[:8]}"
        RESOURCE_SESSIONS[session_id] = {
            "session_id": session_id,
            "request_id": f"rr-{uuid.uuid4().hex[:8]}",
            "job_id": payload.get("job_id", ""),
            "user": user,
            "entitlements": user["entitlements"],
            "messages": [],
            "draft": {"state": "draft", "versions": []},
            "fields": {},
            "retrieved_policy_chunks": [],
            "missing_fields": [],
            "risk_level": "medium",
            "iterations": 0,
            "agent_trajectory": [],
            "terminated_by": None,
        }
    state = RESOURCE_SESSIONS[session_id]
    state["user"] = user
    state["entitlements"] = user["entitlements"]
    state["job_id"] = payload.get("job_id", "")
    state["latest_input"] = payload.get("message", "").strip()
    state = get_resource_skill().invoke(state)
    RESOURCE_SESSIONS[session_id] = state

    response = resource_request_response(state, user, started)
    retrieved = state.get("retrieved_policy_chunks", [])
    action = state.get("next_action", "unknown")
    write_agent_log(
        {
            "event_type": "agent_action",
            "skill_name": "resource_request_skill",
            "graph_backend": get_resource_skill().backend,
            "tab": "resource_request",
            "action": action,
            "request_id": state["request_id"],
            "session_id": session_id,
            "user": user["email"],
            "entitlements": user["entitlements"],
            "retrieval_backend": retrieval_backend(retrieved),
            "embedding_model": EMBEDDING_MODEL if retrieval_backend(retrieved) == "langchain-inmemory-vectorstore" else None,
            "vector_error": KB.vector_error if retrieval_backend(retrieved) == "keyword-fallback" else "",
            "retrieved_policy_chunks": compact_chunks(retrieved),
            "cited_policy_chunks": response["cited_policy_chunks"],
            "dlp_findings_redacted": state.get("dlp_findings_redacted", 0),
            "extraction_backend": state.get("extraction_backend", ""),
            "extraction_error": state.get("extraction_error", ""),
            "validation_backend": state.get("validation_backend", ""),
            "validation_error": state.get("validation_error", ""),
            "policy_violations": state.get("policy_violations", []),
            "model": MODEL,
            "risk_level": response["risk_level"],
            "draft_state": response["draft"]["state"],
            "missing_fields": response["missing_fields"],
            "agent_iteration": state.get("iterations", 0),
            "agent_trajectory": state.get("agent_trajectory", []),
            "agent_terminated_by": state.get("terminated_by"),
            "latency_ms": elapsed_ms(started),
        }
    )
    return response


def generate_chat_answer(question: str, messages: list[dict[str, str]], retrieved: list[dict[str, Any]]) -> str:
    policy_context = "\n\n".join(f"[{item['citation']}]\n{item['text']}" for item in retrieved[:5])
    conversation_history = [
        {"role": item["role"], "content": item["content"]}
        for item in messages[-8:]
        if item.get("content")
    ]
    prompt = {
        "task": "Answer as Super Cool Copilot in a friendly chat style.",
        "rules": [
            "Use only the provided policy context; do not invent requirements.",
            "Use the conversation history so follow-up questions and answers are contextual.",
            "Keep the answer under 120 words unless the user asks for detail.",
            "Use plain sentences, not Markdown bullets, headings, bold text, or numbered lists.",
            "Do not use greetings, filler, or phrases like 'based on the policies I have'.",
            "Sound like a direct internal architecture assistant, not a policy memo.",
            "Give the practical answer first, then at most one clarifying question.",
            "Do not approve or provision anything.",
            "Do not include file paths, citation IDs, or source lists in the user-facing answer.",
        ],
        "question": question,
        "conversation_history": conversation_history,
        "policy_context": policy_context,
        "response_shape": "one or two short paragraphs, no Markdown formatting",
    }
    try:
        text = vertex_generate_text(prompt)
        if text.strip():
            return text.strip()
    except Exception:
        pass
    if not retrieved:
        return "I could not find a matching S3C policy for that question. Try including the client, environment, resource type, and data classification."
    first = retrieved[0]
    second = retrieved[1] if len(retrieved) > 1 else first
    return (
        f"The practical answer is that this needs a policy-aligned resource request before anything is approved. "
        f"{summarize_chunk(first)} [{first['citation']}] "
        f"{summarize_chunk(second)} [{second['citation']}] "
        "If you want, use Request Resource and I can draft the request fields."
    )


def get_resource_skill() -> ResourceRequestSkill:
    global RESOURCE_SKILL
    if RESOURCE_SKILL is None:
        RESOURCE_SKILL = ResourceRequestSkill(
            {
                "redact_input": rr_redact_input,
                "extract_fields": rr_extract_fields,
                "retrieve_policies": rr_retrieve_policies,
                "assess_request": rr_assess_request,
                "decide_action": rr_decide_action,
                "execute_action": rr_execute_action,
            }
        )
    return RESOURCE_SKILL


def rr_redact_input(state: ResourceRequestState) -> ResourceRequestState:
    set_job_phase(state.get("job_id"), "Redacting sensitive data...")
    latest = state.get("latest_input", "").strip()
    redacted, findings = redact(latest) if latest else ("", [])
    state["redacted_input"] = redacted
    state["dlp_findings_redacted"] = len(findings)
    if redacted:
        state.setdefault("messages", []).append({"role": "user", "content": redacted})
    return state


def rr_extract_fields(state: ResourceRequestState) -> ResourceRequestState:
    set_job_phase(state.get("job_id"), "Reading request...")
    text = "\n".join(item["content"] for item in state.get("messages", []) if item.get("role") == "user")
    draft_fields = state.get("draft", {}).get("fields", {})
    fields, backend, error = extract_resource_fields(text, draft_fields)
    state["fields"] = fields
    state["extraction_backend"] = backend
    state["extraction_error"] = error
    return state


def rr_retrieve_policies(state: ResourceRequestState) -> ResourceRequestState:
    set_job_phase(state.get("job_id"), "Retrieving policy docs...")
    fields = state.get("fields", {})
    requested_client = fields.get("client_id")
    entitlements = state.get("entitlements", [])
    if requested_client and requested_client not in entitlements:
        state["retrieved_policy_chunks"] = []
        return state
    state["retrieved_policy_chunks"] = KB.retrieve(build_query(fields, state.get("messages", [])), entitlements, limit=8)
    return state


def rr_assess_request(state: ResourceRequestState) -> ResourceRequestState:
    set_job_phase(state.get("job_id"), "Validating choices...")
    fields = state.get("fields", {})
    retrieved = state.get("retrieved_policy_chunks", [])
    signature = validation_signature(fields, retrieved)
    if signature and signature == state.get("validation_signature") and state.get("validated_fields") is not None:
        fields = dict(state.get("validated_fields", {}))
        violations = state.get("policy_violations", [])
        backend = "cached-policy-validation"
        error = ""
    else:
        fields, violations, backend, error = validate_resource_fields(fields, retrieved)
        state["validation_signature"] = signature
        state["validated_fields"] = dict(fields)
    state["fields"] = fields
    state["policy_violations"] = violations
    state["validation_backend"] = backend
    state["validation_error"] = error
    assessment = assess_resource_request(fields, retrieved)
    state["missing_fields"] = assessment["missing_fields"]
    state["risk_level"] = assessment["risk_level"]
    requested_client = fields.get("client_id")
    if requested_client and requested_client not in state.get("entitlements", []):
        state["entitlement_violation"] = requested_client
    else:
        state.pop("entitlement_violation", None)
    return state


def rr_decide_action(state: ResourceRequestState) -> ResourceRequestState:
    set_job_phase(state.get("job_id"), "Choosing next step...")
    if state.get("entitlement_violation"):
        state["next_action"] = "reject"
        state["action_reason"] = "requested client is outside the caller's entitlements"
        return state

    action = deterministic_resource_action(state)
    state["next_action"] = action
    state["action_reason"] = f"deterministic {action}"
    state["proposed_question"] = ""
    return state


def rr_execute_action(state: ResourceRequestState) -> ResourceRequestState:
    action = state.get("next_action", "ask_developer")
    phase_by_action = {
        "reject": "Checking entitlements...",
        "answer_question": "Drafting answer...",
        "ask_developer": "Preparing follow-up...",
        "update_draft": "Updating draft...",
        "finalize_for_review": "Finalizing for review...",
    }
    set_job_phase(state.get("job_id"), phase_by_action.get(action, "Applying action..."))
    state["iterations"] = int(state.get("iterations", 0)) + 1
    state.setdefault("agent_trajectory", [])
    state.setdefault("draft", {"state": "draft", "versions": []})

    if action == "reject":
        client_id = state.get("entitlement_violation", "that client")
        state["draft"]["state"] = "rejected"
        state["terminated_by"] = "entitlement_reject"
        assistant = f"I cannot request a resource for {client_id} because this identity is not entitled to that client engagement."
        state["assistant_message"] = assistant
        state["messages"].append({"role": "assistant", "content": assistant})
        state["agent_trajectory"].append(trajectory(state, "reject", assistant))
        return state

    if state.get("extraction_backend") == "llm-extraction-unavailable":
        assistant = "I had trouble reading that last reply. Please resend the request details in one message, and I'll continue the draft."
        state["assistant_message"] = assistant
        state["messages"].append({"role": "assistant", "content": assistant})
        state["agent_trajectory"].append(trajectory(state, "ask_developer", assistant))
        return state

    if action == "answer_question":
        assistant = generate_resource_answer(state)
        state["assistant_message"] = assistant
        state["messages"].append({"role": "assistant", "content": assistant})
        state["agent_trajectory"].append(trajectory(state, "answer_question", "answered a request question without changing the draft"))
        return state

    if action == "ask_developer":
        question = resource_followup_question(state)
        if state.get("policy_violations"):
            question = f"{policy_violation_message(state)} {question}"
        state["assistant_message"] = question
        state["messages"].append({"role": "assistant", "content": question})
        state["agent_trajectory"].append(trajectory(state, "ask_developer", question))
        return state

    if action == "update_draft":
        patch = draft_patch(
            state.get("fields", {}),
            {
                "missing_fields": state.get("missing_fields", []),
                "risk_level": state.get("risk_level", "medium"),
                "policy_violations": state.get("policy_violations", []),
            },
        )
        update_draft(state, patch)
        assistant = draft_update_message(state)
        state["assistant_message"] = assistant
        state["messages"].append({"role": "assistant", "content": assistant})
        state["agent_trajectory"].append(trajectory(state, "update_draft", "updated the draft resource request"))
        return state

    state["draft"]["state"] = "pending_review"
    state["terminated_by"] = "finalize_for_review"
    assistant = "I moved the resource request to pending human review. It is not approved, and nothing has been provisioned."
    state["assistant_message"] = assistant
    state["messages"].append({"role": "assistant", "content": assistant})
    state["agent_trajectory"].append(trajectory(state, "finalize_for_review", "moved the request to pending human review"))
    return state


def resource_action_decision(state: ResourceRequestState) -> dict[str, Any]:
    prompt = {
        "task": "Select the next bounded action for the S3C Resource Request Skill.",
        "allowed_actions": ["ask_developer", "update_draft", "finalize_for_review", "answer_question"],
        "rules": [
            "Return only JSON.",
            "Do not approve or provision anything.",
            "Use answer_question when the user's latest message asks why a field was chosen, what a policy requires, what the draft means, or what happens next.",
            "answer_question must not change the draft or request state.",
            "Ask for missing required fields before updating or finalizing.",
            "Use update_draft when enough information exists to improve the draft.",
            "Use finalize_for_review only after a draft exists or the iteration cap requires stopping.",
            "Do not write the user-facing follow-up question; the application will choose deterministic question text.",
        ],
        "response_schema": {
            "action": "ask_developer | update_draft | finalize_for_review | answer_question",
            "reason": "short reason",
        },
        "fields": state.get("fields", {}),
        "missing_fields": state.get("missing_fields", []),
        "risk_level": state.get("risk_level"),
        "draft_state": state.get("draft", {}).get("state", "draft"),
        "draft_versions": len(state.get("draft", {}).get("versions", [])),
        "iterations": state.get("iterations", 0),
        "policy_requirements": requirements(state.get("retrieved_policy_chunks", [])),
        "recent_messages": state.get("messages", [])[-6:],
    }
    try:
        return vertex_generate_json(prompt)
    except Exception:
        return resource_action_fallback(state)


def resource_action_fallback(state: ResourceRequestState) -> dict[str, Any]:
    missing = state.get("missing_fields", [])
    if int(state.get("iterations", 0)) >= MAX_ITERATIONS:
        return {"action": "finalize_for_review", "reason": "iteration cap reached"}
    if is_resource_question(state.get("latest_input", "")):
        return {"action": "answer_question", "reason": "user asked a question about the request"}
    if "data_classification" in missing or "regulated_data_confirmation" in missing:
        return {"action": "ask_developer", "reason": "data classification is required", "question": next_resource_question(missing)}
    if "owner" in missing or "cost_center" in missing:
        return {"action": "ask_developer", "reason": "owner and cost center are required", "question": next_resource_question(missing)}
    if not state.get("draft", {}).get("versions"):
        return {"action": "update_draft", "reason": "enough information is available to draft"}
    return {"action": "finalize_for_review", "reason": "draft exists and is ready for human review"}


def deterministic_resource_action(state: ResourceRequestState) -> str:
    latest = state.get("latest_input", "").strip()
    missing = state.get("missing_fields", [])
    has_draft = bool(state.get("draft", {}).get("versions"))
    if state.get("extraction_backend") == "llm-extraction-unavailable":
        return "ask_developer"
    if latest and is_resource_question(latest):
        return "answer_question"
    if not latest and has_draft and not missing:
        return "finalize_for_review"
    if draft_needs_update(state):
        return "update_draft"
    if missing:
        return "ask_developer"
    if not has_draft:
        return "update_draft"
    return "finalize_for_review" if not latest else "update_draft"


def draft_needs_update(state: ResourceRequestState) -> bool:
    draft_fields = state.get("draft", {}).get("fields", {})
    fields = state.get("fields", {})
    for key, value in fields.items():
        if draft_fields.get(key) != value:
            return True
    for violation in state.get("policy_violations", []):
        if violation.get("field") in draft_fields:
            return True
    return False


def is_resource_question(text: str) -> bool:
    value = text.strip().lower()
    if not value:
        return False
    question_terms = ["?", "why", "what", "how", "explain", "where", "when", "which", "should", "can", "policy", "require"]
    return any(term in value for term in question_terms)


def validate_resource_fields(fields: dict[str, Any], retrieved: list[dict[str, Any]]) -> tuple[dict[str, Any], list[dict[str, Any]], str, str]:
    if not fields:
        return fields, [], "no-fields", ""
    policy_context = "\n\n".join(f"[{item['citation']}]\n{item['text']}" for item in retrieved[:8])
    violations: list[dict[str, Any]] = []
    backend = "llm-policy-validation"
    error = ""
    prompt = {
        "task": "Validate proposed resource request fields against S3C policy context.",
        "rules": [
            "Return only JSON.",
            "Check every proposed field for compliance with the supplied policy context.",
            "If a proposed value violates a requirement, mark that field invalid.",
            "Only mark a field invalid when the policy context clearly supports the finding.",
            "Do not invent policy requirements.",
            "Use citations exactly as they appear in the provided policy context.",
            "If you cannot cite a provided policy chunk for a violation, omit that violation.",
        ],
        "response_schema": {
            "violations": [
                {
                    "field": "field name",
                    "proposed_value": "value the user suggested",
                    "reason": "short explanation",
                    "required_value": "minimum or allowed value if known",
                    "citation": "policy citation",
                }
            ]
        },
        "fields": fields,
        "policy_context": policy_context,
    }
    try:
        result = vertex_generate_json(prompt)
        violations = normalize_policy_violations(result.get("violations", []), fields, retrieved)
    except Exception as exc:
        backend = "deterministic-policy-validation"
        error = str(exc)[:240]
    violations = merge_policy_violations(violations, deterministic_policy_violations(fields, retrieved))
    accepted = dict(fields)
    for violation in violations:
        accepted.pop(violation["field"], None)
    return accepted, violations, backend, error


def validation_signature(fields: dict[str, Any], retrieved: list[dict[str, Any]]) -> str:
    if not fields:
        return ""
    payload = {
        "fields": fields,
        "policy": [
            {
                "citation": item.get("citation", ""),
                "policy_version": item.get("policy_version", ""),
            }
            for item in retrieved[:8]
        ],
    }
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


def normalize_policy_violations(raw: Any, fields: dict[str, Any], retrieved: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not isinstance(raw, list):
        return []
    citations_available = {item.get("citation") for item in retrieved}
    output: list[dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        field = str(item.get("field", "")).strip()
        if field not in fields:
            continue
        citation = str(item.get("citation", "")).strip()
        if citation and citations_available and citation not in citations_available:
            citation = ""
        if not citation:
            continue
        reason = str(item.get("reason", "")).strip()
        required = str(item.get("required_value", "")).strip()
        output.append(
            {
                "field": field,
                "proposed_value": str(fields.get(field, "")),
                "reason": reason or "The proposed value does not comply with retrieved policy.",
                "required_value": required,
                "citation": citation,
            }
        )
    return output


def deterministic_policy_violations(fields: dict[str, Any], retrieved: list[dict[str, Any]]) -> list[dict[str, Any]]:
    violations: list[dict[str, Any]] = []
    retention_days = parse_days(fields.get("backup_retention"))
    if retention_days is not None:
        minimum, citation = minimum_backup_retention(fields, retrieved)
        if minimum and retention_days < minimum:
            violations.append(
                {
                    "field": "backup_retention",
                    "proposed_value": str(fields.get("backup_retention", "")),
                    "reason": f"Backup retention must be at least {minimum} days for this request.",
                    "required_value": f"{minimum} days or longer",
                    "citation": citation,
                }
            )
    engine_violation = deterministic_engine_violation(fields, retrieved)
    if engine_violation:
        violations.append(engine_violation)
    return violations


def deterministic_engine_violation(fields: dict[str, Any], retrieved: list[dict[str, Any]]) -> dict[str, Any] | None:
    engine = str(fields.get("preferred_engine", "")).lower().strip()
    if not engine:
        return None
    text = "\n\n".join(item.get("text", "") for item in retrieved).lower()
    requires_relational = "managed relational" in text or fields.get("resource_category") == "data_store"
    relational_terms = ["mysql", "postgres", "postgresql", "cloud sql", "alloydb", "relational", "sql server", "oracle"]
    non_relational_terms = ["mongodb", "mongo", "dynamodb", "firestore", "bigtable", "redis", "cassandra", "elasticsearch", "opensearch"]
    if requires_relational and any(term in engine for term in non_relational_terms) and not any(term in engine for term in relational_terms):
        citation = first_citation_containing(retrieved, ["managed relational", "engine"])
        return {
            "field": "preferred_engine",
            "proposed_value": str(fields.get("preferred_engine", "")),
            "reason": "This request requires a managed relational database engine, and the proposed engine is not relational.",
            "required_value": "managed relational database engine",
            "citation": citation,
        }
    return None


def first_citation_containing(retrieved: list[dict[str, Any]], terms: list[str]) -> str:
    for item in retrieved:
        text = item.get("text", "").lower()
        if all(term in text for term in terms):
            return item.get("citation", "")
    return retrieved[0].get("citation", "") if retrieved else ""


def merge_policy_violations(primary: list[dict[str, Any]], extra: list[dict[str, Any]]) -> list[dict[str, Any]]:
    merged = {item["field"]: item for item in primary if item.get("field")}
    for item in extra:
        merged[item["field"]] = item
    return list(merged.values())


def parse_days(value: Any) -> int | None:
    if value is None:
        return None
    match = re.search(r"\b(\d+)\s*(day|days|d)\b", str(value), re.I)
    if not match:
        return None
    return int(match.group(1))


def minimum_backup_retention(fields: dict[str, Any], retrieved: list[dict[str, Any]]) -> tuple[int | None, str]:
    text = "\n\n".join(f"[{item.get('citation', '')}]\n{item.get('text', '')}" for item in retrieved)
    candidates: list[tuple[int, str]] = []
    for item in retrieved:
        item_text = item.get("text", "")
        if "backup" not in item_text.lower() or "retention" not in item_text.lower():
            continue
        for match in re.finditer(r"minimum\s+(\d+)[-\s]?day\s+backup\s+retention|minimum\s+(\d+)[-\s]?day\s+retention|retention_days:\s*(\d+)", item_text, re.I):
            days = next((int(group) for group in match.groups() if group), None)
            if days:
                candidates.append((days, item.get("citation", "")))
    regulated_production = fields.get("environment") == "production" and fields.get("data_classification") == "regulated"
    if fields.get("client_id") == "finance-client-b" and regulated_production:
        return max(candidates, default=(90, "clients/finance-client-b/database-standards.md#3"), key=lambda item: item[0])
    if regulated_production:
        return max(candidates, default=(35, "shared/architecture/approved-resource-patterns.md#2"), key=lambda item: item[0])
    if "minimum 7-day retention" in text.lower() or "retention_days: 7" in text.lower():
        return 7, "shared/templates/standard-data-store-template.yaml#1"
    return (max(candidates, key=lambda item: item[0]) if candidates else (None, ""))


def generate_resource_answer(state: ResourceRequestState) -> str:
    policy_context = "\n\n".join(f"[{item['citation']}]\n{item['text']}" for item in state.get("retrieved_policy_chunks", [])[:6])
    draft = state.get("draft", {})
    prompt = {
        "task": "Answer a user's question about an in-progress S3C resource request.",
        "rules": [
            "Use the current draft fields, conversation history, and policy context.",
            "Justify draft choices with policy requirements when policy context supports the answer.",
            "If the policy context does not support the answer, say what is missing instead of inventing a rule.",
            "Do not approve, provision, or imply the request is approved.",
            "Keep the response concise and conversational.",
            "Return plain text, not JSON.",
        ],
        "latest_question": state.get("latest_input", ""),
        "draft": {
            "state": draft.get("state", "draft"),
            "fields": draft.get("fields", state.get("fields", {})),
            "recommended_pattern": draft.get("recommended_pattern"),
            "proposed_configuration": draft.get("proposed_configuration"),
        },
        "known_fields": state.get("fields", {}),
        "missing_fields": state.get("missing_fields", []),
        "policy_context": policy_context,
        "recent_messages": state.get("messages", [])[-8:],
        "response_shape": "one or two short paragraphs, no Markdown table",
    }
    try:
        answer = vertex_generate_text(prompt).strip()
        if answer:
            return answer
    except Exception:
        pass
    if not state.get("retrieved_policy_chunks"):
        return "I do not have enough policy context to justify that yet. Send the resource details or ask about a specific draft field, and I will retrieve the relevant policy before answering."
    return "I chose the current draft values from the request details you provided and the retrieved S3C policy context. The request is still a draft and still needs human review before anything is approved or provisioned."


def draft_update_message(state: ResourceRequestState) -> str:
    missing = state.get("missing_fields", [])
    violation = policy_violation_message(state)
    if not missing:
        if violation:
            return f"{violation} I updated the draft with the remaining compliant fields."
        return "I updated the draft resource request with the known fields and policy-aligned controls. The draft has the required fields and is ready for your review."
    question = resource_followup_question(state)
    prefix = f"{violation} " if violation else ""
    return f"{prefix}I updated the draft with what I know so far. {question}"


def policy_violation_message(state: ResourceRequestState) -> str:
    violations = state.get("policy_violations", [])
    if not violations:
        return ""
    parts = []
    for item in violations[:3]:
        field = str(item.get("field", "")).replace("_", " ")
        value = item.get("proposed_value", "")
        reason = item.get("reason", "That value does not comply with the retrieved policy.")
        required = item.get("required_value", "")
        citation = item.get("citation", "")
        sentence = f"I can't accept {field} = {value!r}: {reason}"
        if required:
            sentence += f" Required value: {required}."
        if citation:
            sentence += f" Source: {citation}."
        parts.append(sentence)
    if len(violations) > 3:
        parts.append(f"There are {len(violations) - 3} more policy issues to resolve.")
    return " ".join(parts)


def resource_request_response(state: ResourceRequestState, user: dict[str, Any], started: float) -> dict[str, Any]:
    retrieved = state.get("retrieved_policy_chunks", [])
    draft = dict(state.get("draft", {"state": "draft", "versions": []}))
    if not draft.get("fields") and state.get("fields"):
        draft["fields"] = dict(state.get("fields", {}))
    return {
        "session_id": state["session_id"],
        "request_id": state["request_id"],
        "messages": state.get("messages", []),
        "draft": draft,
        "missing_fields": state.get("missing_fields", []),
        "policy_violations": state.get("policy_violations", []),
        "risk_level": state.get("risk_level", "medium"),
        "citations": citations(retrieved),
        "cited_policy_chunks": [item["citation"] for item in citations(retrieved)],
        "trajectory": state.get("agent_trajectory", []),
        "iterations": state.get("iterations", 0),
        "terminated_by": state.get("terminated_by"),
        "dlp_findings_redacted": state.get("dlp_findings_redacted", 0),
        "logs_query": logs_query(state["request_id"]),
        "user": user["email"],
        "latency_ms": elapsed_ms(started),
    }


def assess_resource_request(fields: dict[str, Any], retrieved: list[dict[str, Any]]) -> dict[str, Any]:
    missing = [field for field in REQUIRED_RESOURCE_FIELDS if not fields.get(field)]
    if fields.get("environment") == "production":
        missing.extend(field for field in PRODUCTION_FIELDS if not fields.get(field))
    missing = list(dict.fromkeys(missing))
    risk = "high" if fields.get("environment") == "production" and fields.get("data_classification") == "regulated" else "medium"
    return {
        "fields": fields,
        "missing_fields": missing,
        "risk_level": risk,
        "requirements": requirements(retrieved),
    }


def next_resource_question(missing: list[str]) -> str:
    if "data_classification" in missing or "regulated_data_confirmation" in missing:
        return "Will this resource store PHI, PCI, financial records, or other regulated data?"
    if "owner" in missing or "cost_center" in missing:
        return "Who is the accountable owner, and what cost center should be charged?"
    if "region" in missing:
        return "Which deployment region should be used?"
    if "availability_slo" in missing:
        return "What availability target or recovery objective should the request include?"
    if "backup_retention" in missing:
        return "What backup retention period should the request use?"
    if "preferred_engine" in missing:
        return "Which approved database engine should the draft use for this engagement?"
    return f"What value should I use for {missing[0]}?"


def resource_followup_question(state: ResourceRequestState) -> str:
    missing = state.get("missing_fields", [])
    if not missing:
        return "Review the draft and send any corrections, or finalize it for human review."
    return f"Please provide: {format_field_list(missing)}."


def safe_followup_question(question: str) -> bool:
    if not question or len(question) > 220 or "?" not in question:
        return False
    blocked = ["investigate", "debug", "parser", "json", "log", "state", "missing_fields", "implementation"]
    return not any(term in question.lower() for term in blocked)


def format_field_list(fields: list[str]) -> str:
    return ", ".join(field.replace("_", " ") for field in fields)


def update_draft(session: dict[str, Any], patch: dict[str, Any]) -> None:
    draft = session["draft"]
    fields = dict(draft.get("fields", {}))
    for field in patch.get("rejected_fields", []):
        fields.pop(field, None)
    fields.update(patch["fields"])
    draft["fields"] = fields
    draft["state"] = "draft"
    draft["recommended_pattern"] = patch["recommended_pattern"]
    draft["proposed_configuration"] = patch["proposed_configuration"]
    draft["versions"].append({"version": len(draft["versions"]) + 1, "timestamp": int(time.time()), "state": "draft", "patch": patch})


def draft_patch(fields: dict[str, Any], assessment: dict[str, Any]) -> dict[str, Any]:
    pattern = "regulated-production-data-store" if fields.get("environment") == "production" or fields.get("data_classification") == "regulated" else "standard-data-store"
    return {
        "fields": fields,
        "rejected_fields": [item.get("field") for item in assessment.get("policy_violations", []) if item.get("field")],
        "recommended_pattern": pattern,
        "proposed_configuration": {
            "pattern": pattern,
            "state": "draft",
            "encryption": "CMEK required" if pattern == "regulated-production-data-store" else "provider-managed encryption acceptable",
            "networking": "private backend-only access",
            "backup": "point-in-time recovery and client-specific retention required",
            "review": "human architecture/security review required before approval",
        },
    }


def isolation_test(payload: dict[str, Any], started: float) -> dict[str, Any]:
    message = payload.get("message") or "production healthcare data store"
    healthcare = KB.retrieve(message, ["healthcare-client-a"], limit=10)
    finance = KB.retrieve(message, ["finance-client-b"], limit=10)
    result = "passed"
    if any("finance-client-b" in item["doc"] for item in healthcare):
        result = "failed"
    if any("healthcare-client-a" in item["doc"] for item in finance):
        result = "failed"
    request_id = f"iso-{uuid.uuid4().hex[:8]}"
    write_agent_log(
        {
            "event_type": "verification",
            "tab": "ops",
            "action": "isolation_test",
            "request_id": request_id,
            "result": result,
            "healthcare_docs": [item["doc"] for item in healthcare],
            "finance_docs": [item["doc"] for item in finance],
            "latency_ms": elapsed_ms(started),
        }
    )
    return {"request_id": request_id, "result": result, "healthcare_docs": [item["doc"] for item in healthcare], "finance_docs": [item["doc"] for item in finance], "logs_query": logs_query(request_id)}


RESOURCE_FIELD_KEYS = set(REQUIRED_RESOURCE_FIELDS + PRODUCTION_FIELDS + ["access_pattern", "recommended_pattern"])


def extract_resource_fields(text: str, base: dict[str, Any] | None = None) -> tuple[dict[str, Any], str, str]:
    base_fields = dict(base or {})
    if not text.strip():
        return base_fields, "empty-input", ""
    prompt = {
        "task": "Extract structured fields from a conversational enterprise resource request.",
        "rules": [
            "Use only the user messages and known fields provided.",
            "Preserve known field values unless the user explicitly corrects them.",
            "Infer obvious values from natural language. For example, 'healthcare client' means healthcare-client-a, 'finance client' means finance-client-b, 'prod' means production, and 'Postgres' means postgresql.",
            "Map semantic phrases to every field, not just exact field names: owners, cost centers, retention, regions, availability, engine products, access patterns, environments, client signals, and data classification.",
            "If the user confirms regulated, PHI, PCI, financial records, or healthcare production data, set data_classification to regulated and regulated_data_confirmation to confirmed.",
            "Extract user-proposed values even if they might violate policy. A later validation step will decide whether values are compliant.",
            "If a value is unknown, omit it. Do not invent owners, cost centers, regions, SLOs, or engines.",
            "Return only JSON.",
        ],
        "response_schema": {
            "fields": {
                "application": "string",
                "environment": "production | development | test | staging",
                "client_id": "healthcare-client-a | finance-client-b",
                "resource_category": "data_store | object_storage | compute | network | other",
                "intended_use": "short string",
                "owner": "string",
                "cost_center": "string",
                "data_classification": "regulated | internal | public",
                "regulated_data_confirmation": "confirmed | not_regulated",
                "availability_slo": "string",
                "backup_retention": "string",
                "region": "string",
                "preferred_engine": "string",
                "access_pattern": "string",
            },
            "notes": ["short extraction notes"],
        },
        "known_fields": base_fields,
        "user_messages": text,
    }
    try:
        result = vertex_generate_json(prompt)
        fields = normalize_extracted_fields(base_fields, result.get("fields", result))
        if not fields and text.strip():
            retry = {
                "task": "Extract resource request fields from the user text. Return only JSON.",
                "rules": [
                    "Capture user-proposed values exactly enough for later policy validation.",
                    "Map semantically relevant phrases to the closest request field even when the user does not name that field.",
                    "Do not reject, correct, or omit a value because it may be noncompliant.",
                    "Do not invent values that the user did not provide or clearly imply.",
                ],
                "fields_to_extract": sorted(RESOURCE_FIELD_KEYS),
                "known_fields": base_fields,
                "user_text": text,
                "response_schema": {"fields": "object containing extracted fields"},
            }
            result = vertex_generate_json(retry)
            fields = normalize_extracted_fields(base_fields, result.get("fields", result))
        return fields, "llm-structured-extraction", ""
    except Exception as exc:
        return base_fields, "llm-extraction-unavailable", str(exc)[:240]


def normalize_extracted_fields(base: dict[str, Any], extracted: Any) -> dict[str, Any]:
    fields = dict(base)
    if not isinstance(extracted, dict):
        return fields
    for key, value in extracted.items():
        if key not in RESOURCE_FIELD_KEYS:
            continue
        if value is None:
            continue
        value = str(value).strip()
        if not value or value.lower() in {"unknown", "n/a", "none", "null"}:
            continue
        fields[key] = normalize_field_value(key, value)
    return fields


def normalize_field_value(key: str, value: str) -> str:
    lower = value.lower().strip()
    if key == "environment":
        if lower in {"prod", "production", "live"} or "production" in lower:
            return "production"
        if lower in {"dev", "development"}:
            return "development"
        if lower in {"stage", "staging"}:
            return "staging"
        if lower == "test":
            return "test"
    if key == "client_id":
        if "health" in lower or "patient" in lower or lower == "healthcare":
            return "healthcare-client-a"
        if "financ" in lower or "pci" in lower:
            return "finance-client-b"
    if key == "resource_category":
        if lower in {"database", "db", "data store", "datastore", "sql"} or "database" in lower:
            return "data_store"
        if lower in {"bucket", "object storage", "gcs"} or "bucket" in lower:
            return "object_storage"
    if key == "data_classification":
        if any(term in lower for term in ["regulated", "phi", "pci", "financial record"]):
            return "regulated"
    if key == "regulated_data_confirmation":
        if lower in {"yes", "confirmed", "regulated", "true"}:
            return "confirmed"
        if lower in {"no", "not regulated", "false"}:
            return "not_regulated"
    if key == "preferred_engine":
        if "postgres" in lower:
            return "postgresql"
        if "mysql" in lower:
            return "mysql"
        if "cloud sql" in lower:
            return "cloud sql"
    if key == "access_pattern":
        if any(term in lower for term in ["backend", "service-only", "service only", "private"]):
            return "backend_service_only"
    return value


def build_query(fields: dict[str, Any], messages: list[dict[str, str]]) -> str:
    text = " ".join(item["content"] for item in messages)
    return " ".join(
        [
            text,
            fields.get("resource_category", ""),
            fields.get("environment", ""),
            fields.get("data_classification", ""),
            "resource request data classification encryption network approved pattern architecture review database retention agent draft",
        ]
    )


def build_chat_query(question: str, user: dict[str, Any]) -> str:
    entitlement_terms = []
    if "healthcare-client-a" in user["entitlements"]:
        entitlement_terms.append("healthcare patient PHI regulated health information partner sharing data handling")
    if "finance-client-b" in user["entitlements"]:
        entitlement_terms.append("finance financial records PCI regulated account data handling")
    return " ".join(
        [
            question,
            "regulated data classification architecture review resource request data handling access logging encryption network",
            *entitlement_terms,
        ]
    )


def user_context(key: str) -> dict[str, Any]:
    return MOCK_USERS.get(key, MOCK_USERS["healthcare-developer"])


def redact(text: str) -> tuple[str, list[dict[str, str]]]:
    findings: list[dict[str, str]] = []
    redacted = text
    try:
        redacted, findings = dlp_redact(text)
    except Exception:
        pass
    patterns = [
        ("api_key", re.compile(r"\b(?:sk|api|token)[_-]?[A-Za-z0-9]{12,}\b", re.I)),
        ("ssn", re.compile(r"\b\d{3}-\d{2}-\d{4}\b")),
    ]
    for name, pattern in patterns:
        def replace(_: re.Match[str]) -> str:
            findings.append({"type": name})
            return f"[REDACTED:{name}]"
        redacted = pattern.sub(replace, redacted)
    return redacted, findings


def dlp_redact(text: str) -> tuple[str, list[dict[str, str]]]:
    body = {
        "item": {"value": text},
        "inspectConfig": {"infoTypes": [{"name": "US_SOCIAL_SECURITY_NUMBER"}, {"name": "CREDIT_CARD_NUMBER"}, {"name": "API_KEY"}], "minLikelihood": "POSSIBLE"},
    }
    response = http_json(f"https://dlp.googleapis.com/v2/projects/{PROJECT_ID}/locations/global/content:inspect", body)
    redacted = text
    findings = []
    for finding in response.get("result", {}).get("findings", []):
        quote_text = finding.get("quote")
        info_type = finding.get("infoType", {}).get("name", "sensitive")
        if quote_text:
            redacted = redacted.replace(quote_text, f"[REDACTED:{info_type}]")
            findings.append({"type": info_type})
    return redacted, findings


def vertex_generate_text(prompt: dict[str, Any]) -> str:
    body = {
        "contents": [{"role": "user", "parts": [{"text": json.dumps(prompt)}]}],
        "generationConfig": {"temperature": 0.2},
    }
    response = http_json(f"https://{REGION}-aiplatform.googleapis.com/v1/projects/{PROJECT_ID}/locations/{REGION}/publishers/google/models/{MODEL}:generateContent", body)
    return response["candidates"][0]["content"]["parts"][0]["text"]


def vertex_generate_json(prompt: dict[str, Any]) -> dict[str, Any]:
    text = vertex_generate_text(prompt).strip()
    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, re.S)
        if not match:
            raise
        value = json.loads(match.group(0))
    if not isinstance(value, dict):
        raise ValueError("model did not return a JSON object")
    return value


def write_agent_log(payload: dict[str, Any]) -> None:
    payload = dict(payload)
    payload["timestamp_ms"] = int(time.time() * 1000)
    print(json.dumps(payload, separators=(",", ":")), flush=True)
    body = {
        "logName": f"projects/{PROJECT_ID}/logs/{LOG_NAME}",
        "resource": {
            "type": "k8s_container",
            "labels": {
                "project_id": PROJECT_ID,
                "location": REGION,
                "cluster_name": "s3c-copilot",
                "namespace_name": "s3c-demo",
                "pod_name": os.environ.get("HOSTNAME", "unknown"),
                "container_name": "copilot",
            },
        },
        "entries": [{"severity": "INFO", "jsonPayload": payload}],
    }
    try:
        http_json("https://logging.googleapis.com/v2/entries:write", body)
    except Exception as exc:
        print(json.dumps({"event_type": "logging_write_failed", "error": str(exc)[:200]}), flush=True)


def http_json(url: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
    data = json.dumps(body).encode() if body is not None else None
    req = Request(url, data=data, headers={"authorization": f"Bearer {access_token()}", "content-type": "application/json"})
    with urlopen(req, timeout=20) as response:
        return json.loads(response.read())


def access_token() -> str:
    req = Request("http://metadata.google.internal/computeMetadata/v1/instance/service-accounts/default/token", headers={"Metadata-Flavor": "Google"})
    with urlopen(req, timeout=5) as response:
        return json.loads(response.read())["access_token"]


def gcs_list(bucket: str) -> list[str]:
    return [item["name"] for item in http_json(f"https://storage.googleapis.com/storage/v1/b/{bucket}/o?maxResults=1000").get("items", [])]


def gcs_read(bucket: str, name: str) -> str:
    req = Request(f"https://storage.googleapis.com/storage/v1/b/{bucket}/o/{quote(name, safe='')}?alt=media", headers={"authorization": f"Bearer {access_token()}"})
    with urlopen(req, timeout=20) as response:
        return response.read().decode()


def scope_from_path(path: str) -> tuple[str, str]:
    parts = path.split("/")
    return (parts[1], "client") if parts[0] == "clients" else ("shared", "shared")


def metadata_value(text: str, key: str) -> str | None:
    match = re.search(rf"\*\*{re.escape(key)}:\*\*\s*(.+)", text)
    return match.group(1).strip() if match else None


def chunk_document(text: str) -> list[tuple[int, str, str]]:
    chunks = []
    heading = "Overview"
    lines: list[str] = []
    for line in text.splitlines():
        if line.startswith("## "):
            if lines:
                chunks.append((len(chunks) + 1, heading, "\n".join(lines).strip()))
            heading = line[3:].strip()
            lines = [line]
        else:
            lines.append(line)
    if lines:
        chunks.append((len(chunks) + 1, heading, "\n".join(lines).strip()))
    return chunks


def tokens(text: str) -> frozenset[str]:
    stop = {"the", "and", "for", "with", "that", "this", "must", "from", "into", "only", "all", "any", "are", "before", "what", "how"}
    return frozenset(tok for tok in re.findall(r"[a-z0-9][a-z0-9_-]+", text.lower()) if tok not in stop)


def citations(chunks: list[dict[str, Any]]) -> list[dict[str, str]]:
    seen = set()
    output = []
    for chunk in chunks:
        if chunk["doc"] in seen:
            continue
        seen.add(chunk["doc"])
        output.append({"citation": chunk["citation"], "source": chunk["doc"], "heading": chunk["heading"]})
        if len(output) >= 5:
            break
    return output


def compact_chunks(chunks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    keys = ["doc", "chunk", "policy_version", "client_id", "visibility", "score", "retrieval_backend", "embedding_model"]
    return [{k: item.get(k) for k in keys if item.get(k) is not None} for item in chunks]


def retrieval_backend(chunks: list[dict[str, Any]]) -> str:
    if chunks:
        return chunks[0].get("retrieval_backend", "unknown")
    return "langchain-inmemory-vectorstore" if KB.vector_ready else "keyword-fallback"


def requirements(chunks: list[dict[str, Any]]) -> list[dict[str, str]]:
    return [{"requirement": summarize_chunk(item), "citation": item["citation"]} for item in chunks[:5]]


def summarize_chunk(item: dict[str, Any]) -> str:
    doc = item["doc"]
    if "agent-draft" in doc:
        return "Agent actions are limited to questions, draft writes, and finalizing for human review."
    if "resource-request" in doc:
        return "Resource requests need required fields, ownership, cost center, and review-ready controls."
    if "database-standards" in doc:
        return "Client-specific database standards govern engine, backup, retention, availability, and approval gates."
    if "approved-resource-patterns" in doc:
        return "Use an approved resource pattern; regulated production data stores require the regulated pattern."
    if "encryption" in doc:
        return "Confidential and regulated data require encryption controls, with CMEK for regulated workloads."
    if "network" in doc:
        return "Backend-only data stores must remain private and restricted to named service identities."
    return item["heading"]


def trajectory(session: dict[str, Any], action: str, detail: str) -> dict[str, Any]:
    return {"iteration": session["iterations"], "action": action, "detail": detail, "timestamp": int(time.time())}


def logs_query(request_id: str) -> str:
    return f'logName="projects/{PROJECT_ID}/logs/{LOG_NAME}" jsonPayload.request_id="{request_id}"'


def elapsed_ms(started: float) -> int:
    return int((time.time() - started) * 1000)


INDEX_HTML = r"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Super Cool Copilot</title>
  <style>
    :root{font-family:Inter,system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;color:#071f3d;background:#f8fafc;--navy:#06274a;--teal:#00a9b7;--orange:#ff6a00;--lime:#c8dc00;--panel:#ffffff}
    *{box-sizing:border-box}body{margin:0;height:100vh;overflow:hidden}button,textarea,select,input{font:inherit}
    .app{display:grid;grid-template-columns:260px minmax(0,1fr);height:100vh}
    .sidebar{background:linear-gradient(180deg,var(--navy),#03172b);color:#e5f8fb;padding:16px;display:flex;flex-direction:column;gap:14px}
    .logo-card{background:#fff;border-radius:10px;padding:10px;display:grid;place-items:center}.logo{width:100%;max-height:118px;object-fit:contain}
    .brand{font-size:20px;font-weight:800;line-height:1.15;color:#fff}.brand span{color:var(--teal)}.muted{color:#b9d3df;font-size:13px;line-height:1.4}
    .nav{display:grid;gap:8px}.nav button,.new-chat{width:100%;border:0;border-radius:8px;padding:10px 12px;text-align:left;cursor:pointer}
    .nav button{background:transparent;color:#d8eef3}.nav button.active{background:rgba(0,169,183,.18);color:#fff;border-left:4px solid var(--teal)}.new-chat{background:var(--orange);color:#fff;text-align:center;font-weight:700}
    label{display:grid;gap:6px;font-size:13px;color:#cbd5e1}select,input,textarea{border:1px solid #d1d5db;border-radius:10px;background:#fff;color:#111827;padding:10px}
    .main{display:flex;flex-direction:column;min-width:0;min-height:0;height:100vh;background:#fff;overflow:hidden}
    .topbar{height:58px;border-bottom:1px solid #e5e7eb;display:flex;align-items:center;justify-content:space-between;padding:0 22px;gap:14px;background:linear-gradient(90deg,#fff,#f0fbfc)}
    .title{font-weight:800;color:var(--navy)}.status{font-size:13px;color:#557083}.status.working::after{content:"";display:inline-block;width:1.4em;text-align:left;animation:dots 1.2s steps(4,end) infinite}@keyframes dots{0%{content:""}25%{content:"."}50%{content:".."}75%,100%{content:"..."}}
    .view{display:none;min-height:0;overflow:hidden;flex:1}.view.active{display:flex}
    .chat-view{flex-direction:column;min-height:0}.messages{flex:1;min-height:0;overflow-y:auto;overflow-x:hidden;padding:28px 20px 18px;display:flex;flex-direction:column;gap:18px;scroll-behavior:smooth}
    .empty{margin:auto;max-width:720px;text-align:center;color:#6b7280}.empty h1{font-size:30px;color:#111827;margin:0 0 10px}
    .message{display:grid;grid-template-columns:34px minmax(0,760px);gap:12px;align-items:start;max-width:920px;width:100%;margin:0 auto}
    .message.user{grid-template-columns:minmax(0,760px) 34px;justify-content:end}.avatar{width:34px;height:34px;border-radius:50%;display:grid;place-items:center;font-weight:700;font-size:13px;background:#e5e7eb;color:#374151}
    .assistant .avatar{background:#d9f7fa;color:var(--teal)}.user .avatar{background:var(--navy);color:#fff;grid-column:2}.bubble{white-space:pre-wrap;line-height:1.55;padding:12px 14px;border-radius:14px;background:#f3f4f6}
    .assistant .bubble{background:#fff;border:1px solid #dbeff2}.user .bubble{background:var(--navy);color:#fff;grid-column:1;grid-row:1}
    .composer{border-top:1px solid #e5e7eb;padding:14px 20px 18px;background:#fff}.composer-inner{max-width:850px;margin:0 auto;display:grid;grid-template-columns:1fr auto;gap:10px;align-items:end}
    textarea{min-height:52px;max-height:150px;resize:vertical;line-height:1.45}.send{border:0;border-radius:10px;background:var(--teal);color:#fff;padding:12px 16px;cursor:pointer;font-weight:700}.send:disabled{opacity:.6;cursor:default}
    .ticket-view{display:none;min-height:0;overflow:hidden}.ticket-view.active{display:flex}.ticket-start{flex:1;display:grid;place-items:center;padding:28px}.ticket-start-card{max-width:850px;width:100%;display:grid;gap:16px}.ticket-start-card h1{margin:0;color:#111827;font-size:28px}.ticket-start-card p{margin:0;color:#6b7280;line-height:1.5}.ticket-start-card textarea{min-height:170px}
    .ticket-workspace{display:none;grid-template-columns:minmax(0,1fr) 380px;gap:0;min-height:0;overflow:hidden;flex:1}.ticket-workspace.active{display:grid}.ticket-chat{display:flex;flex-direction:column;min-width:0;min-height:0;overflow:hidden}.ticket-panel{border-left:1px solid #e5e7eb;background:#f9fafb;padding:18px;overflow:auto;min-height:0}
    .ticket-form{border-top:1px solid #e5e7eb;padding:14px 20px 18px;background:#fff}.ticket-composer{max-width:850px;margin:0 auto;display:grid;grid-template-columns:1fr auto auto;gap:10px;align-items:end}.ticket-actions{display:flex;gap:10px;flex-wrap:wrap}.secondary{border:1px solid #d1d5db;background:#fff;color:#111827;border-radius:10px;padding:10px 12px;cursor:pointer}.secondary:disabled{opacity:.5;cursor:not-allowed}
    .field{background:#fff;border:1px solid #e5e7eb;border-radius:10px;padding:10px;margin:8px 0}.field strong{display:block;font-size:12px;text-transform:uppercase;color:#6b7280;margin-bottom:4px}.missing{border-color:var(--orange)}.ok{border-color:var(--teal)}
    pre{white-space:pre-wrap;margin:0;font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:12px}.hidden{display:none}
    @media(max-width:900px){body{overflow:auto}.app{grid-template-columns:1fr;height:auto;min-height:100vh}.sidebar{position:sticky;top:0;z-index:2}.main{height:calc(100vh - 210px);min-height:620px}.ticket-workspace.active{grid-template-columns:1fr;grid-template-rows:minmax(0,1fr) auto}.ticket-composer{grid-template-columns:1fr}.ticket-panel{border-left:0;border-top:1px solid #e5e7eb;max-height:320px}}
  </style>
</head>
<body>
<div class="app">
  <aside class="sidebar">
    <div>
      <div class="logo-card"><img class="logo" src="/supercoollogo.png" alt="Super Cool Consulting Company logo"></div>
      <div class="brand">Super Cool <span>Copilot</span></div>
      <div class="muted">Policy chat and resource request guidance for S3C teams.</div>
    </div>
    <button class="new-chat" id="newChatBtn">New chat</button>
    <div class="nav">
      <button class="active" id="chatTab">Chat</button>
      <button id="ticketTab">Request Resource</button>
    </div>
    <label>Mock identity
      <select id="user">
        <option value="healthcare-developer">Healthcare Developer</option>
        <option value="finance-developer">Finance Developer</option>
        <option value="platform-architect">Platform Architect</option>
      </select>
    </label>
    <div class="muted">Use the identity switcher to demo client-specific policy access.</div>
  </aside>
  <main class="main">
    <div class="topbar">
      <div class="title" id="viewTitle">Chat</div>
      <div class="status" id="status">Ready</div>
    </div>

    <section class="view chat-view active" id="chatView">
      <div class="messages" id="chatMessages">
        <div class="empty" id="chatEmpty">
          <h1>Ask Super Cool Copilot</h1>
          <p>Ask about architecture review, regulated data, approved patterns, or client-specific standards.</p>
        </div>
      </div>
      <div class="composer">
        <div class="composer-inner">
          <textarea id="chatInput" placeholder="Ask a policy question..."></textarea>
          <button class="send" id="askBtn">Send</button>
        </div>
      </div>
    </section>

    <section class="view ticket-view" id="ticketView">
      <div class="ticket-start" id="ticketStart">
        <div class="ticket-start-card">
          <div>
            <h1>Request a resource</h1>
            <p>Describe what you need. Super Cool Copilot will turn it into a draft request, ask follow-up questions, and keep the decision with a human reviewer.</p>
          </div>
          <textarea id="ticketInput" placeholder="Describe the resource request...">I need a new data store for the Patient Analytics app. This is for production and will be used by a healthcare client. The app needs to store reporting data and should only be available to the backend service.</textarea>
          <button class="send" id="startTicketBtn">Start request</button>
        </div>
      </div>
      <div class="ticket-workspace" id="ticketWorkspace">
        <div class="ticket-chat">
          <div class="messages" id="ticketMessages"></div>
          <div class="ticket-form">
            <div class="ticket-composer">
              <textarea id="ticketReply" placeholder="Ask a question or reply with more request details..."></textarea>
              <button class="send" id="replyTicketBtn">Send</button>
              <button class="secondary" id="finalizeBtn">Finalize for review</button>
            </div>
          </div>
        </div>
        <aside class="ticket-panel">
          <h2>Draft resource request</h2>
          <div id="draft"></div>
        </aside>
      </div>
    </section>
  </main>
</div>
<script>
let chatSession=null,ticketSession=null,ticketStarted=false,ticketMissingCount=0,ticketDraftState='draft',statusTimer=null;
const $=id=>document.getElementById(id);
const chatMessages=$('chatMessages'),ticketMessages=$('ticketMessages'),chatEmpty=$('chatEmpty'),statusEl=$('status');
async function post(url,payload){const r=await fetch(url,{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify(payload)});return r.json()}
async function getChatJob(jobId){const r=await fetch('/api/chat/jobs/'+encodeURIComponent(jobId));if(!r.ok)throw new Error('Chat status unavailable');return r.json()}
async function waitChatJob(jobId){while(true){const job=await getChatJob(jobId);statusEl.textContent=job.phase||'Working';if(job.status==='done')return job.response;if(job.status==='error')throw new Error(job.error||'Chat failed');await new Promise(resolve=>setTimeout(resolve,700))}}
async function getResourceJob(jobId){const r=await fetch('/api/resource-request/jobs/'+encodeURIComponent(jobId));if(!r.ok)throw new Error('Job status unavailable');return r.json()}
async function waitResourceJob(jobId){while(true){const job=await getResourceJob(jobId);statusEl.textContent=job.phase||'Working...';if(job.status==='done')return job.response;if(job.status==='error')throw new Error(job.error||'Request failed');await new Promise(resolve=>setTimeout(resolve,700))}}
function setMode(mode){const chat=mode==='chat';$('chatView').classList.toggle('active',chat);$('ticketView').classList.toggle('active',!chat);$('chatTab').classList.toggle('active',chat);$('ticketTab').classList.toggle('active',!chat);$('viewTitle').textContent=chat?'Chat':'Request Resource';statusEl.textContent='Ready'}
function addMessage(container,emptyEl,role,text){emptyEl?.remove();const row=document.createElement('div');row.className='message '+role;const avatar=document.createElement('div');avatar.className='avatar';avatar.textContent=role==='assistant'?'AI':'You';const bubble=document.createElement('div');bubble.className='bubble';bubble.textContent=text;row.append(avatar,bubble);container.append(row);container.scrollTop=container.scrollHeight}
function updateFinalizeButton(){const disabled=!ticketStarted||ticketMissingCount>0||ticketDraftState==='pending_review';$('finalizeBtn').disabled=disabled;$('finalizeBtn').title=ticketMissingCount>0?'Complete missing fields before finalizing':''}
function startStatus(labels){let i=0;clearInterval(statusTimer);statusEl.classList.add('working');statusEl.textContent=labels[0];statusTimer=setInterval(()=>{i=(i+1)%labels.length;statusEl.textContent=labels[i]},1200)}
function stopStatus(label='Ready'){clearInterval(statusTimer);statusTimer=null;statusEl.classList.remove('working');statusEl.textContent=label}
function setBusy(busy,labels=['Thinking...']){if(busy){clearInterval(statusTimer);statusTimer=null;statusEl.classList.add('working');if(labels&&labels.length){startStatus(labels)}else{statusEl.textContent='Starting'}}else{stopStatus()}$('askBtn').disabled=busy;$('startTicketBtn').disabled=busy;$('replyTicketBtn').disabled=busy;if(busy){$('finalizeBtn').disabled=true}else{updateFinalizeButton()}}
async function ask(){const question=$('chatInput').value.trim();if(!question)return;addMessage(chatMessages,chatEmpty,'user',question);$('chatInput').value='';setBusy(true,null);try{const job=await post('/api/chat-async',{user:$('user').value,session_id:chatSession,message:question});const r=await waitChatJob(job.job_id);chatSession=r.session_id;addMessage(chatMessages,chatEmpty,'assistant',r.answer||'I could not generate an answer.')}catch(e){addMessage(chatMessages,chatEmpty,'assistant','I could not reach the service. Try again.')}finally{setBusy(false)}}
function renderDraft(d){const draft=$('draft');draft.innerHTML='';const fields=(d&&d.fields)||{};const keys=['application','client_id','environment','resource_category','data_classification','owner','cost_center','region','availability_slo','backup_retention','preferred_engine','recommended_pattern'];keys.forEach(k=>{const div=document.createElement('div');div.className='field '+(fields[k]||d?.[k]?'ok':'missing');div.innerHTML=`<strong>${k.replaceAll('_',' ')}</strong>${fields[k]||d?.[k]||'Missing'}`;draft.append(div)});if(d?.proposed_configuration){const div=document.createElement('div');div.className='field ok';div.innerHTML=`<strong>proposed configuration</strong><pre>${JSON.stringify(d.proposed_configuration,null,2)}</pre>`;draft.append(div)}}
function showTicketWorkspace(show){ticketStarted=show;$('ticketStart').classList.toggle('hidden',show);$('ticketWorkspace').classList.toggle('active',show)}
function renderTicket(r){showTicketWorkspace(true);ticketMessages.innerHTML='';(r.messages||[]).forEach(m=>addMessage(ticketMessages,null,m.role==='user'?'user':'assistant',m.content));renderDraft(r.draft||{});ticketMissingCount=(r.missing_fields||[]).length;ticketDraftState=(r.draft&&r.draft.state)||'draft';updateFinalizeButton();statusEl.textContent=ticketDraftState}
async function startTicket(){const message=$('ticketInput').value.trim();if(!message)return;setBusy(true,null);try{const job=await post('/api/resource-request/start-async',{user:$('user').value,message});const r=await waitResourceJob(job.job_id);ticketSession=r.session_id;renderTicket(r)}catch(e){showTicketWorkspace(true);addMessage(ticketMessages,null,'assistant','I could not reach the service. Try again.')}finally{setBusy(false)}}
async function replyTicket(message){if(!ticketSession)return startTicket();setBusy(true,null);try{const job=await post('/api/resource-request/reply-async',{user:$('user').value,session_id:ticketSession,message});const r=await waitResourceJob(job.job_id);$('ticketReply').value='';renderTicket(r)}catch(e){addMessage(ticketMessages,null,'assistant','I could not reach the service. Try again.')}finally{setBusy(false)}}
$('askBtn').onclick=ask;$('chatInput').addEventListener('keydown',e=>{if(e.key==='Enter'&&!e.shiftKey){e.preventDefault();ask()}});
$('startTicketBtn').onclick=startTicket;$('replyTicketBtn').onclick=()=>replyTicket($('ticketReply').value);$('finalizeBtn').onclick=()=>replyTicket('');
$('ticketReply').addEventListener('keydown',e=>{if(e.key==='Enter'&&!e.shiftKey){e.preventDefault();replyTicket($('ticketReply').value)}});
$('chatTab').onclick=()=>setMode('chat');$('ticketTab').onclick=()=>setMode('ticket');
$('newChatBtn').onclick=()=>{chatSession=null;chatMessages.innerHTML='';chatMessages.append(chatEmpty);statusEl.textContent='Ready'};
$('user').onchange=()=>{chatSession=null;ticketSession=null;ticketMissingCount=0;ticketDraftState='draft';chatMessages.innerHTML='';chatMessages.append(chatEmpty);ticketMessages.innerHTML='';showTicketWorkspace(false);renderDraft({});updateFinalizeButton();statusEl.textContent='Ready'};
showTicketWorkspace(false);
renderDraft({});
</script>
</body></html>"""


def main() -> None:
    port = int(os.environ.get("PORT", "8080"))
    server = ThreadingHTTPServer(("0.0.0.0", port), Handler)
    print(f"Super Cool Copilot listening on :{port}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()

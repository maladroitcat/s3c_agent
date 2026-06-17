# Agent Draft Handling Policy

**Scope:** Shared / enterprise-wide
**Applies to:** The S3C Architecture Copilot review agent and all engagements
**Status:** Active
**Policy version:** 2026-01
**Owner:** Enterprise Architecture / Information Security

This policy governs what the AI review agent may do autonomously when it loops to
clarify and draft a resource request. It exists because the agent can take
actions, and those actions must be bounded.

## 1. Permitted autonomous actions

The agent may take only the following actions without human approval:

1. Ask the developer a clarifying question.
2. Write proposed field values to a draft-state request.
3. Write a proposed configuration drawn from an approved resource pattern to a
   draft-state request.
4. Finalize a request for human review.

Any action not in this list is prohibited for the agent.

## 2. Prohibited autonomous actions

The agent must never, on its own:

1. Approve a request or transition it to an approved state.
2. Provision, deploy, modify, or delete any technical resource.
3. Route a request to another team's queue except by finalizing it for review
   through the approval router.
4. Generate infrastructure intended to be directly deployed without human
   review.
5. Write to any request other than the one currently in session.
6. Write to any state other than draft.

## 3. Draft state is reversible

1. Everything the agent writes is held in draft state.
2. Draft content is versioned; each write is recorded.
3. The developer and the human approver can edit or discard any drafted content.
4. Drafting a configuration is a proposal, not a decision. It carries no
   approval and provisions nothing.

## 4. Clarifying questions

1. The agent should ask one clarifying question at a time.
2. Questions must be limited to information needed to complete the request per
   the Resource Request Standard.
3. A developer's answer is treated as untrusted input and is scanned for secrets
   and regulated data before use.

## 5. Loop limits

1. The agent loop is bounded by a maximum number of iterations.
2. The agent loop is bounded by a per-request processing budget.
3. On reaching either limit, the agent finalizes the request with the
   information available and flags it for human review rather than continuing.

## 6. Human gate is absolute

1. No drafted content, however complete, constitutes approval.
2. Only a human approver, acting through the approval router, may approve a
   request.
3. Production-impacting and regulated-data requests always require human
   approval regardless of how complete the draft is.

## 7. Auditability

1. Every agent action — each question, each draft write, each re-retrieval, and
   the final decision — is logged as a discrete event.
2. The recorded trajectory must be sufficient to reconstruct why the agent took
   each step.

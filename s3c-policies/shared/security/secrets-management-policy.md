# Secrets Management Policy

**Scope:** Shared / enterprise-wide
**Applies to:** All S3C-managed workloads and resources
**Status:** Active
**Policy version:** 2025-08
**Owner:** Information Security

This policy governs how credentials, keys, and tokens are handled.

## 1. No static credentials in code or images

1. Application code and container images must not contain passwords, API keys,
   tokens, or private keys.
2. Workloads must obtain cloud access through workload identity, not static
   service-account keys.

## 2. Storage of secrets

1. Secrets that must exist (for example third-party API tokens) must be stored
   in the managed secret store.
2. Access to a secret must be scoped to the workloads that require it.

## 3. Detection and redaction

1. User-supplied input and ingested documents must be scanned for secrets and
   regulated identifiers before being sent to any model.
2. Detected secrets must be redacted before model calls and must not be written
   to logs in cleartext.

## 4. Rotation and revocation

1. Secrets must have a defined rotation schedule.
2. On suspected exposure, the secret must be revoked and rotated promptly.

## 5. AI assistant handling

1. The AI assistant must never request, store, or echo secrets.
2. If a request contains a secret, the assistant must rely on the redacted form
   and may note that a secret was detected and removed.

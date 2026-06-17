# Approved Resource Patterns

**Scope:** Shared / enterprise-wide
**Applies to:** All S3C delivery teams
**Status:** Active
**Policy version:** 2026-01
**Owner:** Enterprise Architecture

This document lists S3C's approved patterns for common resource categories.
Using an approved pattern shortens review. Deviations require justification and
architecture review.

## 1. Standard data store pattern

1. For non-regulated application data in any environment.
2. Managed relational database with encryption at rest enabled by default.
3. Private networking; no public endpoint.
4. Automated daily backups with a minimum 7-day retention.
5. Single-region deployment acceptable for non-production.

## 2. Regulated production data-store pattern

1. For Confidential or Regulated data in production (for example PHI, PCI,
   financial records).
2. Managed relational database with customer-managed encryption keys (CMEK).
3. Private networking only; access restricted to named backend service
   accounts.
4. Automated backups with point-in-time recovery and a minimum 35-day
   retention.
5. High availability across zones with a defined recovery objective.
6. Architecture review and security approval required before go-live.

## 3. Object storage pattern

1. For files, exports, and unstructured artifacts.
2. Uniform bucket-level access; public access prevention enabled.
3. Encryption at rest; CMEK for Confidential or Regulated data.
4. Lifecycle rules for retention and deletion appropriate to the data class.

## 4. Compute resource pattern

1. Containerized workloads on the managed Kubernetes platform.
2. Workload identity for access to cloud services; no static credentials.
3. Least-privilege service accounts scoped to the workload's needs.
4. Resource requests and limits set; autoscaling configured for production.

## 5. Message queue pattern

1. Managed pub/sub or queue service with encryption at rest.
2. Private access; producers and consumers authenticated by service account.
3. Dead-letter handling configured for production.

## 6. Choosing a pattern

1. If the data is Regulated or the environment is production for a regulated
   client, default to the regulated production pattern (clause 2).
2. If no approved pattern fits, request architecture review before proceeding.

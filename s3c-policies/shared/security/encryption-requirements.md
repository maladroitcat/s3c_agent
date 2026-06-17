# Encryption Requirements

**Scope:** Shared / enterprise-wide
**Applies to:** All S3C-managed resources
**Status:** Active
**Policy version:** 2025-07
**Owner:** Information Security

This document defines encryption requirements by data classification. It is
referenced by the resource request standard and the approved resource patterns.

## 1. Encryption at rest

1. All Internal, Confidential, and Regulated data must be encrypted at rest.
2. Confidential and Regulated data must use customer-managed encryption keys
   (CMEK) rather than default provider-managed keys.
3. Encryption keys must be stored in the managed key service and access to them
   restricted to the workloads that require them.

## 2. Encryption in transit

1. All Confidential and Regulated data must be encrypted in transit using
   current TLS standards.
2. Service-to-service traffic carrying Confidential or Regulated data must not
   traverse the public internet unencrypted.
3. Internal-classified data should be encrypted in transit wherever practical.

## 3. Key management

1. Keys must have a defined rotation schedule appropriate to the data class.
2. Key access must be logged and auditable.
3. Static, long-lived credentials must not be embedded in application code or
   container images; use workload identity instead.

## 4. Backups

1. Backups inherit the classification of their source data and must be encrypted
   to the same standard.
2. Backups of Regulated data must use CMEK.

## 5. Verification

1. Architecture review must confirm encryption at rest and in transit for any
   Confidential or Regulated production resource before go-live.

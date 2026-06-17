# Database Standards — Healthcare Client A

**Scope:** Client engagement — healthcare-client-a
**Visibility:** client
**Applies to:** Delivery teams on the Healthcare Client A engagement only
**Status:** Active
**Policy version:** 2026-01
**Owner:** Healthcare Client A Engagement Architecture

These standards apply only to the Healthcare Client A engagement and are
additional to the shared S3C standards. Where these are stricter, they govern.

## 1. Data classification defaults

1. All patient-related data on this engagement is treated as Regulated (PHI)
   unless explicitly proven otherwise in writing.
2. Reporting and analytics data derived from patient records remains Regulated
   if it can be re-identified.

## 2. Required engine and pattern

1. Production data stores holding PHI must use the regulated production
   data-store pattern with customer-managed encryption keys.
2. The approved database engine for this engagement is the managed relational
   engine specified in the engagement architecture; alternative engines require
   client and S3C architecture approval.

## 3. Backup and retention

1. Production PHI data stores require point-in-time recovery and a minimum
   35-day backup retention.
2. Backups must remain within the contractually agreed region.

## 4. Availability

1. Production PHI workloads require high availability across zones with a
   defined recovery objective stated in the request.

## 5. Access

1. PHI data stores are backend-only and restricted to named service accounts.
2. Direct human access to production PHI data requires break-glass procedures
   and is logged.

## 6. Review and contractual gates

1. Any production PHI resource requires architecture review and security
   approval before go-live.
2. Provisioning of PHI-handling resources is contingent on the client Business
   Associate Agreement being in place.

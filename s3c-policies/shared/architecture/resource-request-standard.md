# Resource Request Standard

**Scope:** Shared / enterprise-wide
**Applies to:** All S3C delivery teams and all client engagements
**Status:** Active
**Policy version:** 2026-01
**Owner:** Enterprise Architecture

This standard defines the minimum information and controls required before any
technical resource is provisioned in an S3C-managed environment. It applies in
addition to any client-specific standards. Where a client standard is stricter,
the client standard governs.

## 1. Required request fields

Every resource request must include all of the following before it can enter
review:

1. Application or system name.
2. Target environment (one of: development, test, staging, production).
3. Client engagement identifier.
4. Resource category (for example: data store, object storage, compute,
   message queue, service account, network access, CI/CD resource).
5. Brief description of intended use.
6. Application owner (a named accountable individual).
7. Cost center for chargeback.

A request missing any field in this clause must be returned as
"more information required" rather than approved.

## 2. Mandatory resource tags

All provisioned resources must carry the following tags:

1. `owner` — the accountable individual or team.
2. `cost-center` — for billing and chargeback.
3. `environment` — matching the requested environment.
4. `client-id` — the client engagement the resource belongs to.
5. `data-classification` — the highest classification of data the resource will
   hold (see the Data Classification Policy).

Production resources missing `owner` or `cost-center` tags must not be approved.

## 3. Environment rules

1. Development and test environments may use relaxed availability and backup
   settings but must still honor data classification and encryption rules.
2. Staging must mirror production controls so that review findings in staging
   are representative.
3. Production resources are subject to all controls in this standard and to
   architecture review where required by clause 6.

## 4. Monitoring and alerting

1. All production resources must emit metrics and logs to the central
   monitoring platform.
2. Production data stores and externally reachable services must have alerting
   configured for availability and error-rate thresholds before go-live.

## 5. Approved patterns

1. Teams must use an approved resource pattern where one exists for the
   requested resource category.
2. Deviations from an approved pattern require documented justification and
   architecture review.

## 6. When architecture review is required

Architecture review is required before production deployment when any of the
following is true:

1. The resource will hold data classified Confidential or Regulated.
2. The resource serves a client in a regulated industry (for example
   healthcare, financial services, public sector, insurance).
3. The request deviates from an approved pattern.
4. The resource is internet-reachable.

## 7. Decision outcomes

A review results in exactly one of:

1. `approved` — all required fields present, controls satisfied, and any
   required human approval granted.
2. `more_information_required` — one or more required fields or controls are
   unresolved.
3. `rejected` — the request conflicts with a mandatory control and cannot
   proceed as written.

The AI assistant may produce `more_information_required` or a draft
recommendation but must never set a request to `approved`.

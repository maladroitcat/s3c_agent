# Architecture Review Policy

**Scope:** Shared / enterprise-wide
**Applies to:** All S3C delivery teams
**Status:** Active
**Policy version:** 2025-11
**Owner:** Enterprise Architecture

This policy defines when an architecture review is required, what it covers, and
who must approve.

## 1. Purpose

Architecture review exists to catch security, compliance, cost, and
maintainability issues before resources are provisioned in production, rather
than after work has started.

## 2. Triggers

A formal architecture review is required when any of the following applies:

1. The resource will store or process data classified Confidential or
   Regulated.
2. The engagement is for a client in a regulated industry.
3. The design deviates from an approved resource pattern.
4. The resource is reachable from the public internet.
5. The resource introduces a new technology not on the approved technology list.

## 3. What the review covers

1. Data classification and handling.
2. Encryption at rest and in transit.
3. Network exposure and segmentation.
4. Identity and access (least privilege, no static credentials).
5. Availability, backup, and recovery objectives.
6. Monitoring, alerting, and audit logging.
7. Cost and tagging.

## 4. Approval authority

1. Standard production resources: platform engineering lead.
2. Regulated or Confidential data resources: security partner and enterprise
   architecture, jointly.
3. Internet-reachable production services: security partner approval is
   mandatory.

## 5. AI assistant role

1. The AI assistant may identify when a review is likely required and may draft
   the review summary.
2. The AI assistant must route triggering requests to human approval and must
   not approve them itself.

## 6. Evidence

1. Each review must produce a record of the request, the policies applied, the
   findings, and the approver decision.
2. This record is retained as audit evidence for the engagement.

# Network Access Policy

**Scope:** Shared / enterprise-wide
**Applies to:** All S3C-managed resources
**Status:** Active
**Policy version:** 2025-10
**Owner:** Information Security / Platform Engineering

This policy defines network exposure rules for provisioned resources.

## 1. Default posture

1. Resources are private by default. Public exposure is an exception that must
   be justified and approved.
2. Data stores must not have public endpoints.

## 2. Backend-only resources

1. A resource described as backend-only or service-only must be reachable only
   from named service accounts or workloads within the private network.
2. Backend-only data stores must use private networking and must not be exposed
   to client networks or the internet.

## 3. Internet-reachable resources

1. Any internet-reachable resource requires security partner approval and
   architecture review before production.
2. Internet-reachable services must sit behind the approved ingress and edge
   controls.

## 4. Segmentation

1. Client engagements must be network-segmented from one another.
2. A workload on one client engagement must not have network paths to another
   client's resources.

## 5. Verification

1. Architecture review must confirm that production data stores use private
   networking and that access is limited to the intended service identities.

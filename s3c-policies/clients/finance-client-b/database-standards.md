# Database Standards — Finance Client B

**Scope:** Client engagement — finance-client-b
**Visibility:** client
**Applies to:** Delivery teams on the Finance Client B engagement only
**Status:** Active
**Policy version:** 2025-12
**Owner:** Finance Client B Engagement Architecture

These standards apply only to the Finance Client B engagement and are additional
to the shared S3C standards. Where these are stricter, they govern.

## 1. Data classification defaults

1. Account numbers, balances, and transaction histories are Regulated
   (financial records).
2. Cardholder data, where present, is Regulated (PCI) and subject to
   cardholder-data handling requirements.

## 2. Required engine and pattern

1. Production data stores holding financial records or cardholder data must use
   the regulated production data-store pattern with customer-managed encryption
   keys.
2. The approved engine for this engagement is the managed relational engine
   specified in the engagement architecture.

## 3. Backup and retention

1. Production financial-records data stores require point-in-time recovery and a
   minimum 90-day backup retention to meet the client's audit requirements.
2. Backups must remain within the contractually agreed region.

## 4. Availability

1. Production financial workloads require high availability across zones with a
   stated recovery objective and a recovery time objective of no more than four
   hours.

## 5. Access

1. Financial data stores are backend-only and restricted to named service
   accounts.
2. Segregation of duties applies: the identity that deploys must not be the
   identity that approves production changes.

## 6. Review and compliance gates

1. Any production financial-records or cardholder-data resource requires
   architecture review and security approval before go-live.
2. Cardholder-data resources are additionally subject to the engagement's PCI
   scope controls.

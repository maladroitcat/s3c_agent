# Client Data Handling Rules — Finance Client B

**Scope:** Client engagement — finance-client-b
**Visibility:** client
**Applies to:** Delivery teams on the Finance Client B engagement only
**Status:** Active
**Policy version:** 2025-12
**Owner:** Finance Client B Engagement Security

These rules govern handling of Finance Client B data and apply only to that
engagement.

## 1. Regulated data handling

1. Financial account records and cardholder data must be handled per the
   client's contractual and regulatory obligations.
2. Production financial data must not be copied into non-production
   environments; use synthetic data for development and test.

## 2. Region and residency

1. Finance Client B data must remain within the contractually agreed region,
   including backups and replicas.

## 3. Access boundaries

1. Only personnel assigned to the Finance Client B engagement may access this
   client's data, policies, and resources.
2. Cross-engagement access is prohibited.

## 4. Segregation of duties

1. Production changes require an approver distinct from the requester.
2. Access to financial data is granted on least-privilege, time-bound terms.

## 5. Logging and audit

1. Access to financial records must be logged with identity and purpose.
2. Audit records are retained for the period required by the engagement
   contract.

## 6. AI assistant constraints

1. The assistant may only retrieve Finance Client B policies for users assigned
   to this engagement.
2. The assistant must not surface this client's data or policies to any other
   engagement.

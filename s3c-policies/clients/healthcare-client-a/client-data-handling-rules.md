# Client Data Handling Rules — Healthcare Client A

**Scope:** Client engagement — healthcare-client-a
**Visibility:** client
**Applies to:** Delivery teams on the Healthcare Client A engagement only
**Status:** Active
**Policy version:** 2026-01
**Owner:** Healthcare Client A Engagement Security

These rules govern handling of Healthcare Client A data and apply only to that
engagement.

## 1. Regulated data handling

1. PHI must be handled in accordance with healthcare regulatory obligations and
   the client Business Associate Agreement.
2. PHI must not be copied into development or test environments. Use synthetic
   or de-identified data for non-production.

## 2. Region and residency

1. Healthcare Client A data must remain within the contractually agreed region.
2. Backups and replicas must also remain within that region.

## 3. Access boundaries

1. Only personnel assigned to the Healthcare Client A engagement may access this
   client's data, policies, and resources.
2. Cross-engagement access is prohibited; data must not be shared with other
   client engagements.

## 4. Logging and audit

1. Access to PHI must be logged with the accessing identity and purpose.
2. Audit records are retained per the engagement's contractual retention
   requirement.

## 5. AI assistant constraints

1. The assistant may only retrieve Healthcare Client A policies for users
   assigned to this engagement.
2. The assistant must not surface this client's data or policies to any other
   engagement.

# Data Classification Policy

**Scope:** Shared / enterprise-wide
**Applies to:** All S3C delivery teams and client engagements
**Status:** Active
**Policy version:** 2025-09
**Owner:** Information Security

This policy defines S3C's data classification levels and the baseline handling
requirements for each. Classification drives encryption, networking, retention,
and review requirements elsewhere in the standards.

## 1. Classification levels

S3C uses four classification levels:

1. **Public** — information approved for public release. No confidentiality
   impact if disclosed.
2. **Internal** — non-public business information. Limited impact if disclosed.
3. **Confidential** — sensitive business or client information. Significant
   impact if disclosed.
4. **Regulated** — data governed by law or contract, including PHI, PCI
   cardholder data, financial account records, and government-restricted data.
   Severe impact if disclosed.

## 2. How to classify

1. Classify a resource at the highest level of any data it may hold.
2. If the data may include PII, PHI, PCI, or financial account records, it is at
   least Confidential and is Regulated where law or client contract applies.
3. When classification is uncertain, treat the data as Confidential pending
   confirmation and require explicit classification before production.

## 3. Handling baselines

1. **Public:** no special handling beyond integrity controls.
2. **Internal:** access limited to S3C staff with a business need; encryption at
   rest required.
3. **Confidential:** encryption at rest and in transit; private networking;
   access on a need-to-know basis; access logging.
4. **Regulated:** all Confidential controls plus customer-managed encryption
   keys, mandatory architecture and security review, and contractual handling
   requirements (for example a Business Associate Agreement for PHI).

## 4. Regulated data categories

1. **PHI** — protected health information; triggers healthcare contractual and
   legal obligations.
2. **PCI** — payment card data; triggers cardholder-data handling requirements.
3. **Financial records** — account numbers, balances, transaction histories.
4. **Government-restricted** — data subject to public-sector or sovereignty
   rules.

## 5. Required confirmation for production

1. Production resources for regulated clients must carry an explicit
   `data-classification` value.
2. A production request that does not confirm whether it holds PHI, PCI, or
   financial records cannot be approved and must be returned for clarification.

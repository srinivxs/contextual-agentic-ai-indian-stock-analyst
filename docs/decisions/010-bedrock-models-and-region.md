# 010 — Bedrock region, chat model, embedding model and dimension

- **Status:** Accepted
- **Date:** 2026-09-20

## Context

P2 asked which Bedrock models this account can actually call, so the embedding dimension (which fixes
the pgvector column type) and the region could be chosen before any vector column exists. The owner ran
every check personally with a least-privilege CLI identity (`stock-analyst-cli`, short-lived `aws login`
credentials, no access keys). Listing models costs nothing; the invocation tests cost under a cent in
total.

## Decision

| Item | Choice |
|---|---|
| **Region** | `ap-south-1` (Mumbai) |
| **Embedding model** | `amazon.titan-embed-text-v2:0`, **1024 dimensions**, normalised vectors |
| **Chat / extraction model** | **Amazon Nova 2 Lite** through the global inference profile `global.amazon.nova-2-lite-v1:0` |
| **Backup chat model** | Nova Pro through `apac.amazon.nova-pro-v1:0` (listed and priced, **not tested**) |
| **Vector column** | `vector(1024)` |

Model IDs and the region live in `Settings`, never in code. Third-party models sold through AWS Marketplace are **not used** (see below).

## Verification record

| Check | Result |
|---|---|
| Models visible | 115 in us-east-1, 76 in ap-south-1; the models we need exist in both |
| Titan V2 `InvokeModel` in ap-south-1 | Works. Returned a vector of length **1024**, 2 input tokens. |
| Nova 2 Lite `Converse` via the global profile | Works (47 input tokens, 20 output tokens, well under $0.0001) |
| A small third-party Marketplace model via a `global.` profile | **Blocked**: `INVALID_PAYMENT_INSTRUMENT` (AWS Marketplace payment check) |
| A newer third-party Marketplace model via a `global.` profile | **Blocked**: "not available for this account" |

## Alternatives considered

| Option | Why not |
|---|---|
| Third-party models on Bedrock | They are subscribed through AWS Marketplace on first use. On this India (AISPL) account the subscription fails the payment check, and every attempt creates an agreement that expires instantly. Setting UPI AutoPay did not change it. The owner chose not to pursue an AWS support case. Not a judgement on the models. |
| A model vendor's own API with an API key | Would work around the block, but adds a second vendor, a stored secret and separate billing, and gives up the "IAM roles, no keys" design. Only if Bedrock is unavailable; not needed now. |
| Nova Pro / Lite / Micro | Pro is the tested-later backup (about 2.7x the input price, a stronger model). Lite and Micro are cheaper but likely too weak for structured extraction; untested. |
| Titan V2 at 512 or 256 dimensions | Saves almost nothing at our scale (about 4 KB per 1024-dimension vector). 1024 is the default and the most accurate. |
| Cohere embedding models | Third-party models; probably Marketplace-gated like the ones above. Not tested. |
| A different region (us-east-1) | Same models are available; the blocker is account-level, so it would not help. Mumbai keeps the whole stack in one region. |

## Reasoning

- **Amazon-first models avoid the Marketplace gate.** Titan and Nova needed only IAM permission; no
  subscription, use-case form or payment step applied.
- **1024 dimensions.** The column type follows the model. 10,000 chunks is about 40 MB. pgvector's HNSW
  index supports up to 2000 dimensions. Changing the dimension later means re-embedding everything and a
  new migration, which is why it is fixed now.
- **Nova 2 Lite is cheap and capable enough to try.** Mumbai global standard price is **$0.35 per 1M input
  and $2.95 per 1M output tokens**. Rough estimates (assumed token counts): about $0.003 per cited answer
  and about $0.03 to extract facts from a 100-page filing. It has a 1M-token context, supports the
  Converse API and client-side tool calling.
- **Deterministic code checks the output.** Extraction and answers are validated (quote on the cited page,
  numbers in the evidence, citation IDs exist), so a smaller model's mistakes are caught rather than
  trusted (ADR 003 workflow).

## Tradeoffs and limitations

- **Quality is unproven.** Nova 2 Lite has only answered "ping". Whether it extracts facts and follows the
  citation format well is decided in P11 and P12. If it is too weak, switch to Nova Pro (a config change,
  plus an IAM change of one profile and six regional model ARNs).
- **No India-only processing claim for chat.** In Mumbai, Nova 2 Lite is available only through the
  global profile, so a request may be served from any supported commercial region. Titan embeddings run
  in-region. The data is public filings plus the owner's own chat, so this is acceptable; do not claim
  residency in docs or interviews.
- **Bedrock's structured-outputs feature is not supported** for this model. We ask for JSON in the
  prompt, parse it with Pydantic, and rely on the validator and one retry.
- **Model lifecycle.** The model card says end of life is no sooner than 2026-12-02, with a legacy period
  of at least six months. The model ID is configuration, so a replacement is a small change.
- **IAM shape for a global profile needs three resources**: the inference profile ARN (in the source
  region, with the account ID), the source-region foundation-model ARN, and the regionless global
  foundation-model ARN. The two model statements are conditioned on `bedrock:InferenceProfileArn`
  (the key is only populated for model evaluations, never on the profile statement) and on
  `aws:RequestedRegion` (`ap-south-1`, and `unspecified` for the global ARN). The P7 task role gets the
  same shape with `bedrock:InvokeModel` only; add `InvokeModelWithResponseStream` only if streaming is used.

## If a third-party model is reconsidered later

Third-party Marketplace models need all of: the vendor's first-use form (once per account), an AWS Marketplace
subscription created automatically on first invocation (the invoking identity needs
`aws-marketplace:Subscribe` and `ViewSubscriptions` until it exists, plus
`bedrock:GetUseCaseForModelAccess`), and a payment instrument that Marketplace accepts. For an India
account, AWS documents stored-card limits for Marketplace and recommends Pay by Invoice; updated payment
methods can take up to seven days to apply. None of this applies to Amazon's own models.

## Consequences for other decisions

- ADR 001: the embedding column is `vector(1024)`.
- ADR 004 and 008: Bedrock stays IAM-authenticated with no API keys; the task role needs Bedrock
  invoke permissions for the two models above.
- P3 can start; nothing in P3 to P10 needs a chat model (P11 is the first phase that does).

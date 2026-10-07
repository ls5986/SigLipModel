# Feature 002: Supplemental source-aware MLS evidence

Status: implementation proposal, not deployed or promoted.
Owner request: 2026-10-07, paired with MLSSourcing specs/002-mls-evidence-opportunities.

## Scope and ownership
ActVision preserves and interprets separately identified public descriptions, permitted private remarks, sale context and other relevant acquisition-time fields. It does not decide canonical MLS opportunity membership, ranking or economics. The product owns the explicit weaker Trust-sale opportunity rule.

## Requirements
- EVD-P01 MUST preserve field, ListingKey/event, observation time, content identity, visibility and availability for supplemental evidence. Missing, blank, withheld, unsupported and invalid are not negative observations.
- EVD-P02 MUST provide evidence-aware review/draft context without pretending the current twelve-field structured model or public-remarks-only text model consumed new fields. Existing v1/v2 releases and their pinned contract bundle remain byte-compatible.
- EVD-P03 MUST keep private documents and derived text restricted. External AI processing needs explicit permitted-use authorization distinct from viewing authorization. No raw private text, contact/access details or tokens in logs, tracked fixtures or exports.
- EVD-P04 MUST recognize remaining-work language supported by the actual source and an exact structured Trust value. Neither is a physical damage label, calibrated confidence, seller urgency, probate confirmation, investment-return forecast or human approval.
- EVD-P05 MUST bind every quoted signal to a field and exact span; existing text alone is insufficient unless it supports the asserted claim. Non-mention remains UNKNOWN. A physical-condition judgment may remain unknown while text/sale-context evidence is present.
- EVD-P06 MUST preserve existing source snapshots, historical labels, frozen dataset, group splits, running training request and model registry. Enrichment appends evidence revisions, does not change current frozen training or auto-approve a new release.
- EVD-P07 MUST expose supplemental evidence and modality-specific uncertainty in the Studio review UI. Page views are read-only. New evidence creates a new supplemental identity and makes affected draft context stale without rewriting the original approval.
- EVD-P08 MUST use a separately versioned schema and tests for any future learned-model feature expansion. Supplemental analysis is a clearly labeled rules/assisted analysis, never a trained v2 prediction.

## Acceptance
Synthetic no-photo private remaining-work example gives renovation evidence and unknown physical condition. Exact Trust sale context with updated marketing retains both signals. Estate architecture/trust deed/negated repairs give no false Trust signal. Unsupported feed fields remain explicit. Restricted evidence never appears for unentitled viewers or external processing without permission. Later resale documents cannot enter an earlier acquisition example. Existing contract manifest and fixture tests remain green. No training restart or deployment is part of this feature's implementation tests.

## Constitution check
Complies with separate axes, immutable evidence, explicit promotion and singular product ownership. No approved label or governance review is fabricated. Foundation approval state and external feed rights remain distinct from this implementation request. Published contract bundle 1.0.0 remains unchanged; the supplement is not silently inserted into a previously trained input.

## Failure modes and rollout
Schema/identity/authorization mismatch fails closed for the supplement without breaking legacy image or public-text processing. Roll out provider support before the consumer enables richer exchange. Initial review branches do not deploy, backfill or run paid calls. Live feed verification, reviewed entitlements, a bounded explicit backfill and paired-service acceptance remain release checks. Rollback disables supplemental enrichment and retains append-only evidence.
